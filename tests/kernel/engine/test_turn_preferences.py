from __future__ import annotations

import asyncio

from kairo_kernel.contracts.content import TextBlock, ToolCallBlock
from kairo_kernel.contracts.enums import (
    AuthorizationMode,
    EventType,
    InteractionAction,
    ProviderStreamKind,
    TurnStatus,
)
from kairo_kernel.contracts.events import ChangeEvent
from kairo_kernel.contracts.identifiers import KernelId, SessionId, ToolCallId, TurnId
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.preferences import PreferencesSnapshot
from kairo_kernel.contracts.providers import ProviderStreamEvent
from kairo_kernel.contracts.turns import TurnRequest, TurnResult
from kairo_kernel.engine import EngineOptions, TurnEngine
from kairo_kernel.runtime import EventBus, WorkspaceLeaseManager
from kairo_kernel.services.preferences import PreferencesService
from tests.kernel.engine.fakes import (
    FakeAuthorization,
    FakeInteractions,
    FakeProvider,
    FakeSessions,
    FakeTool,
    FakeTools,
    session,
)


def _completed(text: str = "done") -> tuple[ProviderStreamEvent, ...]:
    return (
        ProviderStreamEvent(ProviderStreamKind.CONTENT, (TextBlock(text),)),
        ProviderStreamEvent(ProviderStreamKind.COMPLETED),
    )


def _engine(
    provider: FakeProvider,
    sessions: FakeSessions,
    *,
    tools: FakeTools | None = None,
    interactions: FakeInteractions | None = None,
    authorization: FakeAuthorization | None = None,
    options: EngineOptions | None = None,
    preferences: PreferencesService | None = None,
    workspace_leases: WorkspaceLeaseManager | None = None,
) -> tuple[TurnEngine, EventBus]:
    bus = EventBus(KernelId("kernel"), max_buffer=1000)
    engine = TurnEngine(
        provider=provider,
        tools=tools or FakeTools(),
        sessions=sessions,
        events=bus,
        interactions=interactions or FakeInteractions(),
        authorization=authorization or FakeAuthorization(),
        options=options or EngineOptions(default_session_id=SessionId("session-1")),
        preferences=preferences,
        workspace_leases=workspace_leases,
    )
    return engine, bus


async def _run(engine: TurnEngine, text: str = "hello") -> tuple[TurnId, TurnResult]:
    accepted = await engine.submit(TurnRequest(text, SessionId("session-1")))
    assert accepted.value is not None
    result = await engine.wait(accepted.value.turn_id, 2)
    assert result.value is not None
    return accepted.value.turn_id, result.value


def test_preferences_overlay_applies_per_turn_accept() -> None:
    async def exercise() -> None:
        provider = FakeProvider(_completed("plan"), _completed("answer"))
        preferences = PreferencesService(PreferencesSnapshot(0, plan_mode=True))
        interactions = FakeInteractions((InteractionAction.APPROVE_ONCE, ""))
        engine, _ = _engine(provider, FakeSessions(session()), preferences=preferences, interactions=interactions)

        _, result = await _run(engine)

        assert result.status is TurnStatus.SUCCEEDED
        assert provider.requests[0].role == "plan"
        assert provider.requests[1].role == "chat"
        assert engine.options.plan_mode is False  # build-time options untouched

    asyncio.run(exercise())


def test_workspace_snapshot_is_captured_at_accept() -> None:
    async def exercise() -> None:
        provider = FakeProvider(_completed())
        leases = WorkspaceLeaseManager("C:/workspace", revision=7)
        engine, bus = _engine(provider, FakeSessions(session()), workspace_leases=leases)

        turn_id, result = await _run(engine)

        assert result.status is TurnStatus.SUCCEEDED
        turn_events = [event for event in (await bus.snapshot()).events if event.turn_id == turn_id]
        assert turn_events
        assert all(event.workspace_revision == 7 for event in turn_events)

    asyncio.run(exercise())


def test_enable_auto_is_durable_for_turn_and_future_turns() -> None:
    class ModePolicy:
        async def is_authorized(self, mode: AuthorizationMode, scope: object) -> bool:
            return mode is not AuthorizationMode.MANUAL

    async def exercise() -> None:
        script = (
            ProviderStreamEvent(
                ProviderStreamKind.TOOL_CALL,
                tool_call=ToolCallBlock(ToolCallId("call-1"), "read_file", JsonObject()),
            ),
            ProviderStreamEvent(ProviderStreamKind.COMPLETED),
        )
        provider = FakeProvider(script, _completed("after"), script, _completed("again"))
        preferences = PreferencesService(PreferencesSnapshot(0))
        interactions = FakeInteractions((InteractionAction.ENABLE_AUTO, ""))
        engine, bus = _engine(
            provider,
            FakeSessions(session()),
            tools=FakeTools(FakeTool("read_file")),
            interactions=interactions,
            authorization=ModePolicy(),
            preferences=preferences,
        )

        _, first = await _run(engine)
        assert first.status is TurnStatus.SUCCEEDED
        snapshot = await preferences.snapshot()
        assert snapshot.authorization_mode is AuthorizationMode.AUTO
        change_events = [
            event for event in (await bus.snapshot()).events if event.event_type is EventType.CONFIG_CHANGED
        ]
        assert change_events
        assert isinstance(change_events[0].payload, ChangeEvent)
        assert change_events[0].payload.revision == 1

        _, second = await _run(engine, "again")
        assert second.status is TurnStatus.SUCCEEDED
        assert len(interactions.requests) == 1  # no second approval prompt

    asyncio.run(exercise())
