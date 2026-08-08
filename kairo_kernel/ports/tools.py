"""Tool registry, authorization and execution ports."""

from __future__ import annotations

from typing import Protocol

from kairo_kernel.contracts.enums import AuthorizationMode, OperationScope
from kairo_kernel.contracts.tools import (
    ToolDescriptor,
    ToolExecutionContext,
    ToolInvocation,
    ToolOutputChunk,
    ToolResult,
)
from kairo_kernel.errors import KernelResult
from kairo_kernel.ports.control import CancellationToken


class ToolOutputSink(Protocol):
    async def write(self, chunk: ToolOutputChunk) -> None: ...


class ToolPort(Protocol):
    @property
    def descriptor(self) -> ToolDescriptor: ...

    async def classify(self, invocation: ToolInvocation) -> KernelResult[OperationScope]: ...

    async def execute(
        self,
        invocation: ToolInvocation,
        context: ToolExecutionContext,
        cancellation: CancellationToken,
        output: ToolOutputSink,
    ) -> ToolResult: ...


class ToolRegistryPort(Protocol):
    async def list(self) -> tuple[ToolDescriptor, ...]: ...

    async def get(self, name: str) -> KernelResult[ToolPort]: ...

    async def reload(self) -> KernelResult[tuple[ToolDescriptor, ...]]: ...


class AuthorizationPolicyPort(Protocol):
    async def is_authorized(self, mode: AuthorizationMode, scope: OperationScope) -> bool: ...
