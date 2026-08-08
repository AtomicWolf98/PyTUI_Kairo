"""Bounded shell and isolated Python process tools."""

from __future__ import annotations

import ast
import asyncio
import os
import sys

from kairo_kernel.contracts.content import ContentBlock, TextBlock
from kairo_kernel.contracts.enums import OperationScope
from kairo_kernel.contracts.json import JsonObject, freeze_json
from kairo_kernel.contracts.tools import ToolDescriptor, ToolExecutionContext, ToolInvocation
from kairo_kernel.runtime.workspace import WorkspaceLeaseManager
from kairo_kernel.tools.base import BuiltinTool, string_argument
from kairo_kernel.tools.policy import CommandPolicy, PolicyViolation, WorkspacePathPolicy


def _schema(properties: object, required: tuple[str, ...]) -> JsonObject:
    frozen = freeze_json({"type": "object", "properties": properties, "required": list(required)})
    if not isinstance(frozen, JsonObject):
        raise TypeError("Tool schema must be an object.")
    return frozen


def _environment(names: tuple[str, ...]) -> dict[str, str]:
    required = {"PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP", "WINDIR", "PATHEXT"}
    allowed = required | {name for name in names if name}
    return {name: value for name, value in os.environ.items() if name.upper() in {item.upper() for item in allowed}}


async def _communicate(process: asyncio.subprocess.Process) -> tuple[bytes, int]:
    try:
        stdout, _ = await process.communicate()
        return stdout or b"", process.returncode or 0
    except asyncio.CancelledError:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 1.0)
            except TimeoutError:
                process.kill()
                await process.wait()
        raise


class RunCommandTool(BuiltinTool):
    def __init__(
        self,
        workspace: WorkspaceLeaseManager,
        *,
        deny_patterns: tuple[str, ...] = (),
        timeout_seconds: float = 30.0,
        max_output_chars: int = 200_000,
    ) -> None:
        super().__init__(
            ToolDescriptor(
                "run_command",
                "Run one bounded shell command in the active workspace.",
                _schema({"command": {"type": "string"}}, ("command",)),
                ("execute",),
            ),
            workspace,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )
        self.deny_patterns = deny_patterns

    async def _classify(self, invocation: ToolInvocation) -> OperationScope:
        snapshot = await self.workspace.snapshot()
        policy = CommandPolicy(WorkspacePathPolicy(snapshot.root), self.deny_patterns)
        return policy.classify(string_argument(invocation.arguments, "command"))

    async def _run(self, invocation: ToolInvocation, context: ToolExecutionContext) -> tuple[ContentBlock, ...]:
        command = string_argument(invocation.arguments, "command")
        async with await self.workspace.write() as lease:
            policy = CommandPolicy(WorkspacePathPolicy(lease.snapshot.root), self.deny_patterns)
            policy.classify(command)
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=lease.snapshot.root,
                env=_environment(context.environment_names),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, code = await _communicate(process)
            text = output.decode("utf-8", errors="replace")
            if code != 0:
                raise RuntimeError(f"Command exited with code {code}.\n{text}")
            return (TextBlock(text),)


class PythonCodePolicy:
    def __init__(
        self,
        workspace: WorkspacePathPolicy,
        *,
        deny_builtins: tuple[str, ...] = ("exec", "eval", "compile", "__import__"),
        deny_modules: tuple[str, ...] = ("subprocess", "socket", "ctypes"),
    ) -> None:
        self.workspace = workspace
        self.deny_builtins = set(deny_builtins)
        self.deny_modules = set(deny_modules)

    def classify(self, code: str) -> OperationScope:
        if not code.strip():
            raise PolicyViolation("Python code must not be empty.")
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            raise PolicyViolation(f"Invalid Python syntax: {exc}") from exc
        scope = OperationScope.INTERNAL
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name.split(".")[0] for alias in node.names] if isinstance(node, ast.Import) else [(node.module or "").split(".")[0]]
                if any(name in self.deny_modules for name in names):
                    return OperationScope.SYSTEM
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in self.deny_builtins:
                return OperationScope.SYSTEM
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    candidate_scope = self.workspace.scope(first.value)
                    if candidate_scope is OperationScope.EXTERNAL:
                        scope = OperationScope.EXTERNAL
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"unlink", "rmdir", "rmtree"}:
                scope = OperationScope.DESTRUCTIVE
        return scope


class RunPythonCodeTool(BuiltinTool):
    def __init__(
        self,
        workspace: WorkspaceLeaseManager,
        *,
        deny_builtins: tuple[str, ...] = ("exec", "eval", "compile", "__import__"),
        deny_modules: tuple[str, ...] = ("subprocess", "socket", "ctypes"),
        timeout_seconds: float = 30.0,
        max_output_chars: int = 200_000,
    ) -> None:
        super().__init__(
            ToolDescriptor(
                "run_python_code",
                "Run bounded Python code in an isolated child interpreter.",
                _schema({"code": {"type": "string"}}, ("code",)),
                ("execute",),
            ),
            workspace,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )
        self.deny_builtins = deny_builtins
        self.deny_modules = deny_modules

    async def _classify(self, invocation: ToolInvocation) -> OperationScope:
        snapshot = await self.workspace.snapshot()
        return PythonCodePolicy(
            WorkspacePathPolicy(snapshot.root),
            deny_builtins=self.deny_builtins,
            deny_modules=self.deny_modules,
        ).classify(string_argument(invocation.arguments, "code"))

    async def _run(self, invocation: ToolInvocation, context: ToolExecutionContext) -> tuple[ContentBlock, ...]:
        code = string_argument(invocation.arguments, "code")
        async with await self.workspace.write() as lease:
            policy = PythonCodePolicy(
                WorkspacePathPolicy(lease.snapshot.root),
                deny_builtins=self.deny_builtins,
                deny_modules=self.deny_modules,
            )
            policy.classify(code)
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-c",
                code,
                cwd=lease.snapshot.root,
                env=_environment(context.environment_names),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, return_code = await _communicate(process)
            text = output.decode("utf-8", errors="replace")
            if return_code != 0:
                raise RuntimeError(f"Python exited with code {return_code}.\n{text}")
            return (TextBlock(text),)
