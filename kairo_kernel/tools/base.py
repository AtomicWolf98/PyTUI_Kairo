"""Shared async execution wrapper for built-in tools."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from contextlib import suppress
from datetime import datetime, timezone

from kairo_kernel.contracts.content import ContentBlock, TextBlock
from kairo_kernel.contracts.enums import ErrorCode, OperationScope, ToolExecutionStatus
from kairo_kernel.contracts.json import JsonArray, JsonObject, JsonValue
from kairo_kernel.contracts.tools import (
    ToolDescriptor,
    ToolExecutionContext,
    ToolInvocation,
    ToolOutputChunk,
    ToolResult,
)
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.ports.control import CancellationToken
from kairo_kernel.ports.tools import ToolOutputSink
from kairo_kernel.runtime.workspace import WorkspaceLeaseManager
from kairo_kernel.tools.policy import PolicyViolation, WorkspacePathPolicy


def string_argument(arguments: JsonObject, name: str, default: str = "") -> str:
    value = arguments.get(name)
    if value is None:
        return default
    if not isinstance(value, str):
        raise PolicyViolation(f"Argument {name!r} must be a string.")
    return value


def bool_argument(arguments: JsonObject, name: str, default: bool = False) -> bool:
    value = arguments.get(name)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise PolicyViolation(f"Argument {name!r} must be a boolean.")
    return value


def int_argument(arguments: JsonObject, name: str, default: int) -> int:
    value = arguments.get(name)
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool):
        raise PolicyViolation(f"Argument {name!r} must be an integer.")
    return value


def array_argument(arguments: JsonObject, name: str) -> tuple[JsonValue, ...]:
    value = arguments.get(name)
    if value is None:
        return ()
    if not isinstance(value, JsonArray):
        raise PolicyViolation(f"Argument {name!r} must be an array.")
    return value.items


class BuiltinTool(ABC):
    """ToolPort implementation with cancellation, timeout and typed results."""

    def __init__(
        self,
        descriptor: ToolDescriptor,
        workspace: WorkspaceLeaseManager,
        *,
        timeout_seconds: float = 30.0,
        max_output_chars: int = 200_000,
    ) -> None:
        self._descriptor = descriptor
        self.workspace = workspace
        self.timeout_seconds = max(0.001, timeout_seconds)
        self.max_output_chars = max(1, max_output_chars)

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    async def classify(self, invocation: ToolInvocation) -> KernelResult[OperationScope]:
        try:
            return KernelResult.success(await self._classify(invocation))
        except (PolicyViolation, ValueError) as exc:
            return KernelResult.failure(
                KernelError(ErrorCode.POLICY_DENIED, str(exc), operation=f"classify:{self.descriptor.name}")
            )

    async def execute(
        self,
        invocation: ToolInvocation,
        context: ToolExecutionContext,
        cancellation: CancellationToken,
        output: ToolOutputSink,
    ) -> ToolResult:
        started = datetime.now(timezone.utc)
        if invocation.name != self.descriptor.name:
            return self._failure(invocation, started, "Invocation tool name does not match adapter.")
        if cancellation.cancelled:
            return self._cancelled(invocation, started)
        task = asyncio.create_task(self._run(invocation, context))
        cancel_task = asyncio.create_task(cancellation.wait())
        try:
            done, _ = await asyncio.wait(
                (task, cancel_task), timeout=self.timeout_seconds, return_when=asyncio.FIRST_COMPLETED
            )
            if cancel_task in done and task not in done:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                return self._cancelled(invocation, started)
            if task not in done:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                return self._failure(invocation, started, f"Tool timed out after {self.timeout_seconds:g}s.")
            content = task.result()
            content = self._cap_content(content)
            if content:
                with suppress(Exception):
                    await output.write(ToolOutputChunk(invocation.tool_call_id, 1, content))
            return ToolResult(
                invocation.tool_call_id,
                invocation.name,
                ToolExecutionStatus.SUCCEEDED,
                content,
                started,
                datetime.now(timezone.utc),
            )
        except asyncio.CancelledError:
            task.cancel()
            raise
        except (PolicyViolation, OSError, ValueError, RuntimeError) as exc:
            return self._failure(invocation, started, str(exc))
        finally:
            cancel_task.cancel()

    def path_policy(self, root: str) -> WorkspacePathPolicy:
        return WorkspacePathPolicy(root)

    def _cap_content(self, content: tuple[ContentBlock, ...]) -> tuple[ContentBlock, ...]:
        remaining = self.max_output_chars
        capped: list[ContentBlock] = []
        truncated = False
        for block in content:
            if isinstance(block, TextBlock):
                text = block.text
                if len(text) > remaining:
                    text = text[:remaining]
                    truncated = True
                capped.append(TextBlock(text))
                remaining -= len(text)
            else:
                capped.append(block)
            if remaining <= 0:
                truncated = True
                break
        if truncated:
            capped.append(TextBlock("\n[output truncated]"))
        return tuple(capped)

    def _failure(self, invocation: ToolInvocation, started: datetime, message: str) -> ToolResult:
        return ToolResult(
            invocation.tool_call_id,
            invocation.name,
            ToolExecutionStatus.FAILED,
            (TextBlock(message[: self.max_output_chars]),),
            started,
            datetime.now(timezone.utc),
            message[: self.max_output_chars],
        )

    def _cancelled(self, invocation: ToolInvocation, started: datetime) -> ToolResult:
        return ToolResult(
            invocation.tool_call_id,
            invocation.name,
            ToolExecutionStatus.CANCELLED,
            (TextBlock("Tool execution cancelled."),),
            started,
            datetime.now(timezone.utc),
            "Tool execution cancelled.",
        )

    @abstractmethod
    async def _classify(self, invocation: ToolInvocation) -> OperationScope: ...

    @abstractmethod
    async def _run(self, invocation: ToolInvocation, context: ToolExecutionContext) -> tuple[ContentBlock, ...]: ...
