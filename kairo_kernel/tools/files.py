"""Async workspace-confined file tools."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path

from kairo_kernel.contracts.content import ContentBlock, TextBlock
from kairo_kernel.contracts.enums import OperationScope
from kairo_kernel.contracts.json import JsonObject, freeze_json
from kairo_kernel.contracts.tools import ToolDescriptor, ToolExecutionContext, ToolInvocation
from kairo_kernel.runtime.workspace import WorkspaceLease, WorkspaceLeaseManager
from kairo_kernel.tools.base import BuiltinTool, bool_argument, int_argument, string_argument
from kairo_kernel.tools.policy import PolicyViolation


def _schema(properties: object, required: tuple[str, ...] = ()) -> JsonObject:
    frozen = freeze_json({"type": "object", "properties": properties, "required": list(required)})
    if not isinstance(frozen, JsonObject):
        raise TypeError("Tool schema must be an object.")
    return frozen


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        with suppress(OSError):
            os.unlink(temporary)
        raise


class _PathTool(BuiltinTool):
    write_access = False

    async def _classify(self, invocation: ToolInvocation) -> OperationScope:
        path = string_argument(invocation.arguments, "path", ".")
        snapshot = await self.workspace.snapshot()
        return self.path_policy(snapshot.root).scope(path)

    async def _lease(self) -> WorkspaceLease:
        return await (self.workspace.write() if self.write_access else self.workspace.read())


class ReadFileTool(_PathTool):
    def __init__(
        self,
        workspace: WorkspaceLeaseManager,
        *,
        max_read_bytes: int = 1_048_576,
        timeout_seconds: float = 30.0,
        max_output_chars: int = 200_000,
    ) -> None:
        super().__init__(
            ToolDescriptor(
                "read_file",
                "Read a UTF-8 text file inside the active workspace.",
                _schema({"path": {"type": "string"}}, ("path",)),
                ("read",),
            ),
            workspace,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )
        self.max_read_bytes = max(1, max_read_bytes)

    async def _run(self, invocation: ToolInvocation, context: ToolExecutionContext) -> tuple[ContentBlock, ...]:
        del context
        async with await self._lease() as lease:
            path = self.path_policy(lease.snapshot.root).resolve(string_argument(invocation.arguments, "path"), must_exist=True)
            if not path.is_file():
                raise PolicyViolation(f"Not a file: {path}")
            size = path.stat().st_size
            if size > self.max_read_bytes:
                raise PolicyViolation(f"File exceeds read limit ({size} > {self.max_read_bytes} bytes).")
            raw = await asyncio.to_thread(path.read_bytes)
            if b"\0" in raw[:8192]:
                raise PolicyViolation("Binary files cannot be read as text.")
            return (TextBlock(raw.decode("utf-8", errors="replace")),)


class WriteFileTool(_PathTool):
    write_access = True

    def __init__(
        self,
        workspace: WorkspaceLeaseManager,
        *,
        max_write_bytes: int = 1_048_576,
        timeout_seconds: float = 30.0,
        max_output_chars: int = 200_000,
    ) -> None:
        super().__init__(
            ToolDescriptor(
                "write_file",
                "Atomically write a UTF-8 text file inside the active workspace.",
                _schema({"path": {"type": "string"}, "content": {"type": "string"}}, ("path", "content")),
                ("write",),
            ),
            workspace,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )
        self.max_write_bytes = max(1, max_write_bytes)

    async def _run(self, invocation: ToolInvocation, context: ToolExecutionContext) -> tuple[ContentBlock, ...]:
        del context
        content = string_argument(invocation.arguments, "content")
        size = len(content.encode("utf-8"))
        if size > self.max_write_bytes:
            raise PolicyViolation(f"Content exceeds write limit ({size} > {self.max_write_bytes} bytes).")
        async with await self._lease() as lease:
            policy = self.path_policy(lease.snapshot.root)
            path = policy.resolve(string_argument(invocation.arguments, "path"))
            await asyncio.to_thread(_atomic_write, path, content)
            return (TextBlock(f"Wrote {size} bytes to {path.relative_to(policy.root).as_posix()}."),)


class ListDirTool(_PathTool):
    def __init__(
        self,
        workspace: WorkspaceLeaseManager,
        *,
        max_entries: int = 2000,
        timeout_seconds: float = 30.0,
        max_output_chars: int = 200_000,
    ) -> None:
        super().__init__(
            ToolDescriptor(
                "list_dir",
                "List directory entries inside the active workspace.",
                _schema(
                    {"path": {"type": "string"}, "recursive": {"type": "boolean"}, "max_depth": {"type": "integer"}}
                ),
                ("read",),
            ),
            workspace,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )
        self.max_entries = max(1, max_entries)

    async def _run(self, invocation: ToolInvocation, context: ToolExecutionContext) -> tuple[ContentBlock, ...]:
        del context
        recursive = bool_argument(invocation.arguments, "recursive")
        max_depth = max(0, int_argument(invocation.arguments, "max_depth", 4))
        async with await self._lease() as lease:
            policy = self.path_policy(lease.snapshot.root)
            directory = policy.resolve(string_argument(invocation.arguments, "path", "."), must_exist=True)
            if not directory.is_dir():
                raise PolicyViolation(f"Not a directory: {directory}")

            def collect() -> list[str]:
                values: list[str] = []
                if not recursive:
                    candidates = sorted(directory.iterdir(), key=lambda item: item.name.lower())
                else:
                    candidates = []
                    for current, directories, files in os.walk(directory, followlinks=False):
                        current_path = Path(current)
                        depth = len(current_path.relative_to(directory).parts)
                        directories[:] = sorted(
                            (name for name in directories if not (current_path / name).is_symlink()), key=str.lower
                        )
                        if depth >= max_depth:
                            directories[:] = []
                        candidates.extend(current_path / name for name in directories)
                        candidates.extend(current_path / name for name in sorted(files, key=str.lower))
                for candidate in candidates:
                    try:
                        resolved = policy.resolve(str(candidate), must_exist=True)
                    except PolicyViolation:
                        continue
                    suffix = "/" if resolved.is_dir() else ""
                    values.append(resolved.relative_to(policy.root).as_posix() + suffix)
                    if len(values) >= self.max_entries:
                        values.append("[entry limit reached]")
                        break
                return values

            return (TextBlock("\n".join(await asyncio.to_thread(collect))),)


class SearchFileTool(_PathTool):
    def __init__(
        self,
        workspace: WorkspaceLeaseManager,
        *,
        max_search_bytes: int = 1_048_576,
        max_results: int = 100,
        timeout_seconds: float = 30.0,
        max_output_chars: int = 200_000,
    ) -> None:
        super().__init__(
            ToolDescriptor(
                "search_file",
                "Search text files inside the active workspace.",
                _schema(
                    {
                        "query": {"type": "string"},
                        "path": {"type": "string"},
                        "is_regex": {"type": "boolean"},
                        "max_depth": {"type": "integer"},
                    },
                    ("query",),
                ),
                ("read",),
            ),
            workspace,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )
        self.max_search_bytes = max(1, max_search_bytes)
        self.max_results = max(1, max_results)

    async def _classify(self, invocation: ToolInvocation) -> OperationScope:
        snapshot = await self.workspace.snapshot()
        return self.path_policy(snapshot.root).scope(string_argument(invocation.arguments, "path", "."))

    async def _run(self, invocation: ToolInvocation, context: ToolExecutionContext) -> tuple[ContentBlock, ...]:
        del context
        query = string_argument(invocation.arguments, "query")
        if not query:
            raise PolicyViolation("Search query must not be empty.")
        is_regex = bool_argument(invocation.arguments, "is_regex")
        pattern = re.compile(query) if is_regex else None
        max_depth = max(0, int_argument(invocation.arguments, "max_depth", 10))
        async with await self._lease() as lease:
            policy = self.path_policy(lease.snapshot.root)
            root = policy.resolve(string_argument(invocation.arguments, "path", "."), must_exist=True)

            def search() -> list[str]:
                files: list[Path] = [root] if root.is_file() else []
                if root.is_dir():
                    for current, directories, names in os.walk(root, followlinks=False):
                        current_path = Path(current)
                        depth = len(current_path.relative_to(root).parts)
                        directories[:] = [name for name in directories if not (current_path / name).is_symlink()]
                        if depth >= max_depth:
                            directories[:] = []
                        files.extend(current_path / name for name in names)
                matches: list[str] = []
                scanned = 0
                for candidate in files:
                    try:
                        path = policy.resolve(str(candidate), must_exist=True)
                        size = path.stat().st_size
                        if size > self.max_search_bytes - scanned or size < 0:
                            continue
                        raw = path.read_bytes()
                    except (OSError, PolicyViolation):
                        continue
                    scanned += len(raw)
                    if b"\0" in raw[:8192]:
                        continue
                    for line_number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                        found = bool(pattern.search(line)) if pattern else query in line
                        if found:
                            matches.append(f"{path.relative_to(policy.root).as_posix()}:{line_number}:{line}")
                            if len(matches) >= self.max_results:
                                matches.append("[result limit reached]")
                                return matches
                return matches

            values = await asyncio.to_thread(search)
            return (TextBlock("\n".join(values) if values else "No matches found."),)


class PatchFileTool(_PathTool):
    write_access = True

    def __init__(
        self,
        workspace: WorkspaceLeaseManager,
        *,
        max_patch_bytes: int = 1_048_576,
        timeout_seconds: float = 30.0,
        max_output_chars: int = 200_000,
    ) -> None:
        super().__init__(
            ToolDescriptor(
                "patch_file",
                "Replace exactly one matching text block in a workspace file.",
                _schema(
                    {
                        "path": {"type": "string"},
                        "search_block": {"type": "string"},
                        "replace_block": {"type": "string"},
                    },
                    ("path", "search_block", "replace_block"),
                ),
                ("write",),
            ),
            workspace,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )
        self.max_patch_bytes = max(1, max_patch_bytes)

    async def _run(self, invocation: ToolInvocation, context: ToolExecutionContext) -> tuple[ContentBlock, ...]:
        del context
        search = string_argument(invocation.arguments, "search_block")
        replacement = string_argument(invocation.arguments, "replace_block")
        if not search:
            raise PolicyViolation("search_block must not be empty.")
        async with await self._lease() as lease:
            policy = self.path_policy(lease.snapshot.root)
            path = policy.resolve(string_argument(invocation.arguments, "path"), must_exist=True)
            if path.stat().st_size > self.max_patch_bytes:
                raise PolicyViolation("File exceeds patch size limit.")
            content = await asyncio.to_thread(path.read_text, encoding="utf-8")
            count = content.count(search)
            if count != 1:
                raise PolicyViolation(f"search_block must match exactly once; found {count} matches.")
            updated = content.replace(search, replacement, 1)
            if len(updated.encode("utf-8")) > self.max_patch_bytes:
                raise PolicyViolation("Patched file exceeds size limit.")
            await asyncio.to_thread(_atomic_write, path, updated)
            return (TextBlock(f"Patched {path.relative_to(policy.root).as_posix()}."),)
