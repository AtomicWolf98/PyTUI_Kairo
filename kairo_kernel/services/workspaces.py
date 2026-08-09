"""Transactional workspace state, previews and bookmark management."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import SessionId, TurnId
from kairo_kernel.contracts.support import WorkspaceRecord
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.ports.repositories import WorkspaceRepositoryPort
from kairo_kernel.runtime.workspace import WorkspaceLease, WorkspaceLeaseManager, WorkspaceSnapshot


@dataclass(frozen=True)
class WorkspaceBookmark:
    name: str
    path: str


@dataclass(frozen=True)
class WorkspaceState:
    root: str
    revision: int
    bookmarks: tuple[WorkspaceBookmark, ...] = ()


@dataclass(frozen=True)
class WorkspacePreview:
    root: str
    revision: int
    relative_path: str
    is_directory: bool
    size_bytes: int
    text: str = ""
    children: tuple[str, ...] = ()
    truncated: bool = False


@dataclass(frozen=True)
class WorkspaceEntry:
    name: str
    relative_path: str
    is_directory: bool
    size_bytes: int = 0


@dataclass(frozen=True)
class WorkspaceTree:
    root: str
    revision: int
    relative_path: str
    entries: tuple[WorkspaceEntry, ...]
    truncated: bool = False


@dataclass(frozen=True)
class ChangedFile:
    relative_path: str
    status: str  # "modified" | "added" | "deleted" | "renamed" | "untracked"


@dataclass(frozen=True)
class ChangedFiles:
    root: str
    revision: int
    is_git_repository: bool
    files: tuple[ChangedFile, ...] = ()


@dataclass(frozen=True)
class WorkspaceDiff:
    root: str
    revision: int
    relative_path: str
    status: str
    unified_diff: str
    truncated: bool = False


class WorkspaceBookmarkRepository(Protocol):
    async def list(self) -> tuple[WorkspaceBookmark, ...]: ...

    async def replace(self, bookmarks: tuple[WorkspaceBookmark, ...]) -> None: ...


class WorkspaceParticipant(Protocol):
    async def apply_workspace(self, workspace: WorkspaceRecord) -> None: ...

    async def rollback_workspace(self, workspace: WorkspaceRecord) -> None: ...


class DegradedSignal(Protocol):
    async def mark_degraded(self, reason: str) -> None: ...


class BookmarkMutation(Protocol):
    def __call__(self, current: tuple[WorkspaceBookmark, ...]) -> tuple[WorkspaceBookmark, ...]: ...


ActiveTurns = Callable[[], Awaitable[tuple[tuple[SessionId, TurnId], ...]]]


class InMemoryWorkspaceBookmarks:
    def __init__(self, bookmarks: tuple[WorkspaceBookmark, ...] = ()) -> None:
        self._bookmarks = bookmarks

    async def list(self) -> tuple[WorkspaceBookmark, ...]:
        return self._bookmarks

    async def replace(self, bookmarks: tuple[WorkspaceBookmark, ...]) -> None:
        self._bookmarks = bookmarks


class WorkspaceService:
    """Coordinate workspace identity as one revisioned, rollback-safe unit."""

    def __init__(
        self,
        repository: WorkspaceRepositoryPort,
        leases: WorkspaceLeaseManager,
        *,
        bookmarks: WorkspaceBookmarkRepository | None = None,
        participants: tuple[WorkspaceParticipant, ...] = (),
        degraded: DegradedSignal | None = None,
        active_turns: ActiveTurns | None = None,
        preview_limit_bytes: int = 256 * 1024,
        preview_child_limit: int = 500,
    ) -> None:
        self._repository = repository
        self._leases = leases
        self._bookmarks = bookmarks or InMemoryWorkspaceBookmarks()
        self._participants = participants
        self._degraded = degraded
        self._active_turns = active_turns
        self._preview_limit_bytes = max(1, preview_limit_bytes)
        self._preview_child_limit = max(1, preview_child_limit)
        self._degraded_reason = ""
        self._mutation_lock = asyncio.Lock()

    @property
    def degraded_reason(self) -> str:
        return self._degraded_reason

    async def snapshot(self) -> WorkspaceState:
        lease = await self._leases.read()
        async with lease:
            bookmarks = await self._bookmarks.list()
            return WorkspaceState(lease.snapshot.root, lease.snapshot.revision, bookmarks)

    @asynccontextmanager
    async def turn_snapshot(self) -> AsyncIterator[WorkspaceSnapshot]:
        """Pin the workspace revision until an active turn releases it."""

        lease = await self._leases.read()
        async with lease:
            yield lease.snapshot

    async def preview(self, relative_path: str = ".") -> KernelResult[WorkspacePreview]:
        lease = await self._leases.read()
        async with lease:
            try:
                root = Path(lease.snapshot.root).resolve(strict=True)
                target = (root / relative_path).resolve(strict=True)
                target.relative_to(root)
                if target.is_dir():
                    names: list[str] = []
                    truncated = False
                    for child in sorted(target.iterdir(), key=lambda item: item.name.casefold()):
                        if len(names) >= self._preview_child_limit:
                            truncated = True
                            break
                        suffix = "/" if child.is_dir() else ""
                        names.append(f"{child.name}{suffix}")
                    return KernelResult.success(
                        WorkspacePreview(
                            str(root),
                            lease.snapshot.revision,
                            target.relative_to(root).as_posix() or ".",
                            True,
                            0,
                            children=tuple(names),
                            truncated=truncated,
                        )
                    )
                size = target.stat().st_size
                with target.open("rb") as handle:
                    payload = handle.read(self._preview_limit_bytes + 1)
                truncated = len(payload) > self._preview_limit_bytes
                payload = payload[: self._preview_limit_bytes]
                return KernelResult.success(
                    WorkspacePreview(
                        str(root),
                        lease.snapshot.revision,
                        target.relative_to(root).as_posix(),
                        False,
                        size,
                        text=payload.decode("utf-8", errors="replace"),
                        truncated=truncated,
                    )
                )
            except (OSError, RuntimeError, ValueError) as exc:
                return _failure(ErrorCode.WORKSPACE_INVALID, "Workspace preview is unavailable.", "workspace.preview", exc)

    async def tree(self, relative_path: str = ".", *, limit: int = 200) -> KernelResult[WorkspaceTree]:
        lease = await self._leases.read()
        async with lease:
            try:
                root = Path(lease.snapshot.root).resolve(strict=True)
                target = (root / relative_path).resolve(strict=True)
                target.relative_to(root)
                if not target.is_dir():
                    return _failure(
                        ErrorCode.WORKSPACE_INVALID, "Workspace tree target is not a directory.", "workspace.tree"
                    )
                entries: list[WorkspaceEntry] = []
                truncated = False
                for child in sorted(target.iterdir(), key=lambda item: item.name.casefold()):
                    if len(entries) >= max(1, limit):
                        truncated = True
                        break
                    entries.append(
                        WorkspaceEntry(
                            child.name,
                            child.relative_to(root).as_posix(),
                            child.is_dir(),
                            0 if child.is_dir() else child.stat().st_size,
                        )
                    )
                return KernelResult.success(
                    WorkspaceTree(
                        str(root),
                        lease.snapshot.revision,
                        target.relative_to(root).as_posix() or ".",
                        tuple(entries),
                        truncated,
                    )
                )
            except (OSError, RuntimeError, ValueError):
                return _failure(ErrorCode.WORKSPACE_INVALID, "Workspace tree is unavailable.", "workspace.tree")

    async def changed_files(self) -> KernelResult[ChangedFiles]:
        lease = await self._leases.read()
        async with lease:
            root = lease.snapshot.root
            revision = lease.snapshot.revision
        result = await _git(root, "status", "--porcelain=v1", "-z")
        if result.error is not None:
            return KernelResult.failure(result.error)
        assert result.value is not None
        code, output = result.value
        if code == 128:
            return KernelResult.success(ChangedFiles(root, revision, False))
        if code != 0:
            return _failure(ErrorCode.WORKSPACE_INVALID, "git status failed.", "workspace.changed_files")
        return KernelResult.success(ChangedFiles(root, revision, True, _parse_porcelain(output)))

    async def diff(self, relative_path: str, *, max_bytes: int = 65_536) -> KernelResult[WorkspaceDiff]:
        lease = await self._leases.read()
        async with lease:
            try:
                root = Path(lease.snapshot.root).resolve(strict=True)
                target = (root / relative_path).resolve(strict=True)
                target.relative_to(root)
            except (OSError, RuntimeError, ValueError):
                return _failure(ErrorCode.WORKSPACE_INVALID, "Workspace diff path is invalid.", "workspace.diff")
            revision = lease.snapshot.revision
            root_text = str(root)
            relative = target.relative_to(root).as_posix()
        status_result = await _git(root_text, "status", "--porcelain=v1", "-z", "--", relative)
        if status_result.error is not None:
            return KernelResult.failure(status_result.error)
        assert status_result.value is not None
        code, status_output = status_result.value
        if code == 128:
            return _failure(ErrorCode.WORKSPACE_INVALID, "Workspace is not a git repository.", "workspace.diff")
        if code != 0:
            return _failure(ErrorCode.WORKSPACE_INVALID, "git status failed.", "workspace.diff")
        statuses = {entry.relative_path: entry.status for entry in _parse_porcelain(status_output)}
        status = statuses.get(relative, "modified")
        if status == "untracked":
            return KernelResult.success(WorkspaceDiff(root_text, revision, relative, status, ""))
        diff_result = await _git(root_text, "diff", "HEAD", "--", relative)
        if diff_result.error is not None:
            return KernelResult.failure(diff_result.error)
        assert diff_result.value is not None
        code, diff_output = diff_result.value
        if code != 0:
            return _failure(ErrorCode.WORKSPACE_INVALID, "git diff failed.", "workspace.diff")
        limit = max(1, max_bytes)
        truncated = len(diff_output) > limit
        text = diff_output[:limit].decode("utf-8", errors="replace")
        return KernelResult.success(WorkspaceDiff(root_text, revision, relative, status, text, truncated))

    async def move(self, target: str, expected_revision: int) -> KernelResult[WorkspaceState]:
        if self._degraded_reason:
            return _failure(ErrorCode.KERNEL_DEGRADED, "Workspace mutations are disabled.", "workspace.move")
        clean = target.strip()
        if not clean:
            return _failure(ErrorCode.INVALID_ARGUMENT, "Workspace target is required.", "workspace.move")
        async with self._mutation_lock:
            lease = await self._leases.write()
            async with lease:
                busy = await self._active_turn_check()
                if busy is not None:
                    return busy
                conflict = self._revision_conflict(lease, expected_revision, "workspace.move")
                if conflict is not None:
                    return conflict
                resolved = await self._resolve_target(clean)
                validated = await self._repository.validate(resolved)
                if not validated.ok:
                    return _copy_failure(validated, "workspace.move")
                old = WorkspaceRecord(lease.snapshot.root, lease.snapshot.revision)
                assert validated.value is not None
                candidate = WorkspaceRecord(validated.value.root, old.revision + 1, old.root)
                applied: list[WorkspaceParticipant] = []
                persistence_attempted = False
                try:
                    persistence_attempted = True
                    persisted_result = await self._repository.apply(candidate)
                    if not persisted_result.ok:
                        raise _TransactionFailure(persisted_result.error)
                    for participant in self._participants:
                        applied.append(participant)
                        await participant.apply_workspace(candidate)
                    snapshot = await self._leases.update(lease, candidate.root)
                    bookmarks = await self._bookmarks.list()
                    return KernelResult.success(WorkspaceState(snapshot.root, snapshot.revision, bookmarks))
                except Exception as exc:
                    rollback_ok = await self._rollback_move(old, tuple(applied), persistence_attempted)
                    if not rollback_ok:
                        await self._mark_degraded("Workspace rollback failed.")
                        return _failure(
                            ErrorCode.KERNEL_DEGRADED,
                            "Workspace move failed and recovery was incomplete.",
                            "workspace.move",
                        )
                    if isinstance(exc, _TransactionFailure) and exc.error is not None:
                        return KernelResult.failure(_sanitized_error(exc.error, "workspace.move"))
                    return _failure(ErrorCode.RUNTIME_SYNC_FAILED, "Workspace move failed.", "workspace.move", exc)

    async def save_bookmark(
        self,
        name: str,
        path: str,
        expected_revision: int,
    ) -> KernelResult[WorkspaceState]:
        clean_name = name.strip()
        clean_path = path.strip()
        if not clean_name or not clean_path:
            return _failure(ErrorCode.INVALID_ARGUMENT, "Bookmark name and path are required.", "workspace.bookmark.save")
        return await self._mutate_bookmarks(
            expected_revision,
            "workspace.bookmark.save",
            lambda current: tuple(item for item in current if item.name != clean_name)
            + (WorkspaceBookmark(clean_name, clean_path),),
        )

    async def remove_bookmark(self, name: str, expected_revision: int) -> KernelResult[WorkspaceState]:
        clean_name = name.strip()
        if not clean_name:
            return _failure(ErrorCode.INVALID_ARGUMENT, "Bookmark name is required.", "workspace.bookmark.remove")

        def remove(current: tuple[WorkspaceBookmark, ...]) -> tuple[WorkspaceBookmark, ...]:
            return tuple(item for item in current if item.name != clean_name)

        current = await self._bookmarks.list()
        if all(item.name != clean_name for item in current):
            return _failure(ErrorCode.NOT_FOUND, "Workspace bookmark was not found.", "workspace.bookmark.remove")
        return await self._mutate_bookmarks(expected_revision, "workspace.bookmark.remove", remove)

    async def _mutate_bookmarks(
        self,
        expected_revision: int,
        operation: str,
        mutate: BookmarkMutation,
    ) -> KernelResult[WorkspaceState]:
        if self._degraded_reason:
            return _failure(ErrorCode.KERNEL_DEGRADED, "Workspace mutations are disabled.", operation)
        async with self._mutation_lock:
            lease = await self._leases.write()
            async with lease:
                conflict = self._revision_conflict(lease, expected_revision, operation)
                if conflict is not None:
                    return conflict
                old_bookmarks = await self._bookmarks.list()
                new_bookmarks = mutate(old_bookmarks)
                old = WorkspaceRecord(lease.snapshot.root, lease.snapshot.revision)
                candidate = WorkspaceRecord(old.root, old.revision + 1, old.root)
                persistence_attempted = False
                bookmark_changed = False
                try:
                    persistence_attempted = True
                    result = await self._repository.apply(candidate)
                    if not result.ok:
                        raise _TransactionFailure(result.error)
                    await self._bookmarks.replace(new_bookmarks)
                    bookmark_changed = True
                    snapshot = await self._leases.update(lease, old.root)
                    return KernelResult.success(WorkspaceState(snapshot.root, snapshot.revision, new_bookmarks))
                except Exception as exc:
                    rollback_ok = True
                    if bookmark_changed:
                        try:
                            await self._bookmarks.replace(old_bookmarks)
                        except Exception:
                            rollback_ok = False
                    if persistence_attempted:
                        restored = await self._repository.rollback(old)
                        rollback_ok = rollback_ok and restored.ok
                    if not rollback_ok:
                        await self._mark_degraded("Workspace bookmark rollback failed.")
                        return _failure(
                            ErrorCode.KERNEL_DEGRADED,
                            "Workspace bookmark update failed and recovery was incomplete.",
                            operation,
                        )
                    if isinstance(exc, _TransactionFailure) and exc.error is not None:
                        return KernelResult.failure(_sanitized_error(exc.error, operation))
                    return _failure(ErrorCode.RUNTIME_SYNC_FAILED, "Workspace bookmark update failed.", operation, exc)

    async def _resolve_target(self, target: str) -> str:
        for bookmark in await self._bookmarks.list():
            if bookmark.name == target:
                return bookmark.path
        return target

    def _revision_conflict(
        self,
        lease: WorkspaceLease,
        expected_revision: int,
        operation: str,
    ) -> KernelResult[WorkspaceState] | None:
        if expected_revision == lease.snapshot.revision:
            return None
        return _failure(ErrorCode.CONFLICT, "Workspace revision has changed.", operation)

    async def _active_turn_check(self) -> KernelResult[WorkspaceState] | None:
        if self._active_turns is None:
            return None
        active = await self._active_turns()
        if active:
            return _failure(
                ErrorCode.KERNEL_BUSY,
                "Cannot move the workspace while a turn is active.",
                "workspace.move",
                retryable=True,
            )
        return None

    async def _rollback_move(
        self,
        old: WorkspaceRecord,
        applied: tuple[WorkspaceParticipant, ...],
        persistence_attempted: bool,
    ) -> bool:
        ok = True
        for participant in reversed(applied):
            try:
                await participant.rollback_workspace(old)
            except Exception:
                ok = False
        if persistence_attempted:
            restored = await self._repository.rollback(old)
            ok = ok and restored.ok
        return ok

    async def _mark_degraded(self, reason: str) -> None:
        self._degraded_reason = reason
        if self._degraded is not None:
            await self._degraded.mark_degraded(reason)


class _TransactionFailure(RuntimeError):
    def __init__(self, error: KernelError | None) -> None:
        self.error = error
        super().__init__("Workspace transaction failed.")


def _sanitized_error(error: KernelError, operation: str) -> KernelError:
    return KernelError(error.code, "Workspace persistence failed.", error.retryable, operation)


def _copy_failure(result: KernelResult[WorkspaceRecord], operation: str) -> KernelResult[WorkspaceState]:
    assert result.error is not None
    return KernelResult.failure(KernelError(result.error.code, "Workspace validation failed.", result.error.retryable, operation))


ResultT = TypeVar("ResultT")


def _failure(
    code: ErrorCode,
    message: str,
    operation: str,
    cause: BaseException | None = None,
    *,
    retryable: bool = False,
) -> KernelResult[ResultT]:
    del cause
    return KernelResult.failure(KernelError(code, message, retryable, operation))


_GIT_TIMEOUT_SECONDS = 10.0


async def _git(root: str, *arguments: str) -> KernelResult[tuple[int, bytes]]:
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            root,
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError):
        return _failure(ErrorCode.WORKSPACE_INVALID, "The git executable was not found.", "workspace.git")
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), _GIT_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()
        return _failure(ErrorCode.WORKSPACE_INVALID, "git timed out.", "workspace.git")
    return KernelResult.success((int(process.returncode or 0), stdout))


def _parse_porcelain(raw: bytes) -> tuple[ChangedFile, ...]:
    entries = raw.decode("utf-8", errors="replace").split("\0")
    files: list[ChangedFile] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry.strip():
            continue
        status = entry[:2]
        path = entry[3:]
        if "R" in status or "C" in status:
            index += 1  # rename/copy records carry the source path in a second entry
            files.append(ChangedFile(path, "renamed"))
        elif status == "??":
            files.append(ChangedFile(path, "untracked"))
        elif "A" in status:
            files.append(ChangedFile(path, "added"))
        elif "D" in status:
            files.append(ChangedFile(path, "deleted"))
        else:
            files.append(ChangedFile(path, "modified"))
    return tuple(files)
