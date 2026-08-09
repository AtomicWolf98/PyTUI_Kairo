"""Immutable-name registry for built-in kernel tools."""

from __future__ import annotations

from datetime import datetime, timezone

from kairo_kernel.contracts.content import TextBlock
from kairo_kernel.contracts.enums import AuthorizationMode, ErrorCode, ToolExecutionStatus
from kairo_kernel.contracts.tools import ToolDescriptor, ToolExecutionContext, ToolInvocation, ToolResult
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.ports.control import CancellationToken
from kairo_kernel.ports.tools import ToolOutputSink, ToolPort, ToolRegistryPort
from kairo_kernel.tools.policy import AuthorizationPolicy


class AuthorizationGate:
    """Classify each invocation and enforce authorization before execution."""

    def __init__(self, policy: AuthorizationPolicy | None = None) -> None:
        self.policy = policy or AuthorizationPolicy()

    async def execute(
        self,
        tool: ToolPort,
        invocation: ToolInvocation,
        context: ToolExecutionContext,
        cancellation: CancellationToken,
        output: ToolOutputSink,
        *,
        approved_once: bool = False,
    ) -> ToolResult:
        started = datetime.now(timezone.utc)
        classified = await tool.classify(invocation)
        if classified.error is not None:
            return self._rejected(invocation, started, classified.error.message)
        scope = classified.value
        if scope is None:
            return self._rejected(invocation, started, "Tool classification returned no scope.")
        try:
            mode = AuthorizationMode(context.authorization_mode)
        except ValueError:
            return self._rejected(invocation, started, "Unknown authorization mode.")
        if not approved_once and not await self.policy.is_authorized(mode, scope):
            return self._rejected(
                invocation,
                started,
                f"{mode.value} mode does not authorize {scope.value} scope.",
            )
        return await tool.execute(invocation, context, cancellation, output)

    @staticmethod
    def _rejected(invocation: ToolInvocation, started: datetime, message: str) -> ToolResult:
        return ToolResult(
            invocation.tool_call_id,
            invocation.name,
            ToolExecutionStatus.REJECTED,
            (TextBlock(message),),
            started,
            datetime.now(timezone.utc),
            message,
        )


class BuiltinToolRegistry:
    def __init__(self, tools: tuple[ToolPort, ...]) -> None:
        values: dict[str, ToolPort] = {}
        for tool in tools:
            if not tool.descriptor.name or tool.descriptor.name in values:
                raise ValueError(f"Duplicate or empty tool name: {tool.descriptor.name!r}")
            values[tool.descriptor.name] = tool
        self._tools = values

    async def list(self) -> tuple[ToolDescriptor, ...]:
        return tuple(tool.descriptor for tool in self._tools.values())

    async def get(self, name: str) -> KernelResult[ToolPort]:
        tool = self._tools.get(name)
        if tool is None:
            return KernelResult.failure(KernelError(ErrorCode.TOOL_NOT_FOUND, f"Tool not found: {name}"))
        return KernelResult.success(tool)

    async def reload(self) -> KernelResult[tuple[ToolDescriptor, ...]]:
        return KernelResult.success(await self.list())


class CompositeToolRegistry:
    """Merge several registries; earlier registries win name collisions."""

    def __init__(self, registries: tuple[ToolRegistryPort, ...]) -> None:
        if not registries:
            raise ValueError("CompositeToolRegistry requires at least one registry.")
        self._registries = registries

    async def list(self) -> tuple[ToolDescriptor, ...]:
        descriptors: dict[str, ToolDescriptor] = {}
        for registry in self._registries:
            for descriptor in await registry.list():
                descriptors.setdefault(descriptor.name, descriptor)
        return tuple(descriptors.values())

    async def get(self, name: str) -> KernelResult[ToolPort]:
        for registry in self._registries:
            result = await registry.get(name)
            if result.ok:
                return result
        return KernelResult.failure(KernelError(ErrorCode.TOOL_NOT_FOUND, f"Tool not found: {name}"))

    async def reload(self) -> KernelResult[tuple[ToolDescriptor, ...]]:
        for registry in self._registries:
            reloaded = await registry.reload()
            if reloaded.error is not None:
                return KernelResult.failure(reloaded.error)
        return KernelResult.success(await self.list())
