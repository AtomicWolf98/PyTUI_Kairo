from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from tempfile import TemporaryDirectory

from kairo_kernel import KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.contracts.content import TextBlock, ToolCallBlock
from kairo_kernel.contracts.enums import (
    InteractionAction,
    OperationScope,
    ProviderStreamKind,
    ToolExecutionStatus,
)
from kairo_kernel.contracts.identifiers import ProfileId, ToolCallId
from kairo_kernel.contracts.interactions import InteractionResponse
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.providers import ProviderProfile, ProviderRequest, ProviderStreamEvent
from kairo_kernel.contracts.tools import (
    ToolDescriptor,
    ToolExecutionContext,
    ToolInvocation,
    ToolResult,
)
from kairo_kernel.contracts.turns import TurnRequest
from kairo_kernel.errors import KernelResult
from kairo_kernel.ports.control import CancellationToken
from kairo_kernel.ports.tools import ToolOutputSink
from kairo_kernel.tools.registry import BuiltinToolRegistry

from ._support import PROFILE, value


class ToolCallingProvider:
    def __init__(self) -> None:
        self.round = 0

    async def resolve_profile(self, profile_id: ProfileId | None, role: str) -> KernelResult[ProviderProfile]:
        del profile_id, role
        return KernelResult.success(PROFILE)

    async def probe(self, profile_id: ProfileId) -> KernelResult[ProviderProfile]:
        del profile_id
        return KernelResult.success(PROFILE)

    def stream(self, request: ProviderRequest, cancellation: CancellationToken) -> AsyncIterator[ProviderStreamEvent]:
        del request, cancellation
        return self._stream()

    async def _stream(self) -> AsyncIterator[ProviderStreamEvent]:
        self.round += 1
        if self.round == 1:
            call = ToolCallBlock(ToolCallId("approval-demo"), "demo_external", JsonObject())
            yield ProviderStreamEvent(ProviderStreamKind.TOOL_CALL, tool_call=call)
        else:
            yield ProviderStreamEvent(ProviderStreamKind.CONTENT, content=(TextBlock("approved"),))
        yield ProviderStreamEvent(ProviderStreamKind.COMPLETED, finish_reason="stop")


class ExternalDemoTool:
    descriptor = ToolDescriptor("demo_external", "Approval example", JsonObject(), ("external",))

    async def classify(self, invocation: ToolInvocation) -> KernelResult[OperationScope]:
        del invocation
        return KernelResult.success(OperationScope.EXTERNAL)

    async def execute(
        self,
        invocation: ToolInvocation,
        context: ToolExecutionContext,
        cancellation: CancellationToken,
        output: ToolOutputSink,
    ) -> ToolResult:
        del context, cancellation, output
        now = datetime.now(timezone.utc)
        return ToolResult(
            invocation.tool_call_id,
            invocation.name,
            ToolExecutionStatus.SUCCEEDED,
            (TextBlock("done"),),
            now,
            now,
        )


async def main() -> None:
    with TemporaryDirectory(prefix="kairo-approval-") as workspace:
        kernel = build_kernel(
            KernelConfig(workspace, database_path=":memory:", enable_builtin_tools=False),
            KernelDependencies(
                provider=ToolCallingProvider(),
                tools=BuiltinToolRegistry((ExternalDemoTool(),)),
            ),
        )
        async with kernel:
            session = value(await kernel.sessions.create("Approval"))
            accepted = value(await kernel.submit(TurnRequest("use the tool", session.session_id)))
            pending = ()
            for _ in range(100):
                pending = await kernel.interactions.pending()
                if pending:
                    break
                await asyncio.sleep(0.001)
            assert pending
            request = pending[0]
            receipt = await kernel.interactions.respond(
                InteractionResponse(
                    request.interaction_id,
                    request.turn_id,
                    InteractionAction.APPROVE_ONCE,
                )
            )
            assert receipt.ok
            result = value(await kernel.wait(accepted.turn_id, 2))
            assert result.status.value == "succeeded"


if __name__ == "__main__":
    asyncio.run(main())
