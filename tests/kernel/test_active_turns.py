from __future__ import annotations

import asyncio

from kairo_kernel import KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.contracts.content import ToolCallBlock
from kairo_kernel.contracts.enums import (
    InteractionAction,
    ProviderStreamKind,
    TurnPhase,
    TurnStatus,
)
from kairo_kernel.contracts.identifiers import SessionId, ToolCallId
from kairo_kernel.contracts.interactions import InteractionResponse
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.providers import ProviderStreamEvent
from kairo_kernel.contracts.turns import TurnRequest
from tests.kernel.engine.fakes import (
    FakeAuthorization,
    FakeProvider,
    FakeSessions,
    FakeTool,
    FakeTools,
    session,
)


def _config(tmp_path: object) -> KernelConfig:
    root = str(tmp_path)
    return KernelConfig(
        root,
        database_path=str(root + "/kernel.db"),
        default_session_id=SessionId("session-1"),
        enable_builtin_tools=False,
    )


def test_active_turns_reports_all_running_sessions(tmp_path: object) -> None:
    async def exercise() -> None:
        provider = FakeProvider((ProviderStreamEvent(ProviderStreamKind.COMPLETED),))
        provider.block = True
        sessions = FakeSessions(session("one"), session("two"))
        kernel = build_kernel(
            _config(tmp_path),
            KernelDependencies(provider=provider, tools=FakeTools(), sessions=sessions),
        )
        async with kernel:
            assert await kernel.active_turns() == ()
            first = await kernel.submit(TurnRequest("a", SessionId("one")))
            second = await kernel.submit(TurnRequest("b", SessionId("two")))
            assert first.ok and first.value is not None
            assert second.ok and second.value is not None
            active = await kernel.active_turns()
            assert {item.session_id for item in active} == {SessionId("one"), SessionId("two")}
            assert {item.turn_id for item in active} == {first.value.turn_id, second.value.turn_id}
            assert all(item.status in (TurnStatus.ACCEPTED, TurnStatus.RUNNING) for item in active)
            assert all(item.pending_interaction is None for item in active)

    asyncio.run(exercise())


def test_active_turns_surfaces_pending_interaction(tmp_path: object) -> None:
    async def exercise() -> None:
        script = (
            ProviderStreamEvent(
                ProviderStreamKind.TOOL_CALL,
                tool_call=ToolCallBlock(ToolCallId("call-1"), "read_file", JsonObject()),
            ),
            ProviderStreamEvent(ProviderStreamKind.COMPLETED),
        )
        provider = FakeProvider(script, (ProviderStreamEvent(ProviderStreamKind.COMPLETED),))
        kernel = build_kernel(
            _config(tmp_path),
            KernelDependencies(
                provider=provider,
                tools=FakeTools(FakeTool("read_file")),
                sessions=FakeSessions(session()),
                authorization=FakeAuthorization(allowed=False),
            ),
        )
        async with kernel:
            accepted = await kernel.submit(TurnRequest("go", SessionId("session-1")))
            assert accepted.ok and accepted.value is not None
            pending = ()
            for _ in range(200):
                pending = await kernel.interactions.pending()
                if pending:
                    break
                await asyncio.sleep(0.01)
            assert pending, "turn never requested approval"
            active = await kernel.active_turns()
            assert len(active) == 1
            assert active[0].phase is TurnPhase.WAITING_APPROVAL
            assert active[0].pending_interaction is not None
            assert active[0].pending_interaction.interaction_id == pending[0].interaction_id
            assert active[0].started_at is not None

            responded = await kernel.interactions.respond(
                InteractionResponse(pending[0].interaction_id, pending[0].turn_id, InteractionAction.APPROVE_ONCE)
            )
            assert responded.ok
            completed = await kernel.wait(accepted.value.turn_id, 2)
            assert completed.ok and completed.value is not None
            assert completed.value.status is TurnStatus.SUCCEEDED
            assert await kernel.active_turns() == ()

    asyncio.run(exercise())
