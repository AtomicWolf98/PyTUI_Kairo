from __future__ import annotations

import asyncio

from kairo_kernel.contracts.content import ToolCallBlock
from kairo_kernel.contracts.enums import EventType, ProviderStreamKind, TurnStatus
from kairo_kernel.contracts.events import ChangeEvent
from kairo_kernel.contracts.identifiers import KernelId, SessionId, ToolCallId
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.providers import ProviderStreamEvent
from kairo_kernel.contracts.tools import ToolDescriptor
from kairo_kernel.contracts.turns import TurnRequest
from kairo_kernel.engine import EngineOptions, TurnEngine
from kairo_kernel.runtime import EventBus, WorkspaceLeaseManager
from tests.kernel.engine.fakes import (
    FakeAuthorization,
    FakeInteractions,
    FakeProvider,
    FakeSessions,
    FakeTool,
    FakeTools,
    session,
)


class WriteTool(FakeTool):
    def __init__(self, name: str):
        super().__init__(name)
        self._descriptor = ToolDescriptor(name, name, JsonObject(), ("write",))


def _script(tool: str) -> tuple[tuple[ProviderStreamEvent, ...], ...]:
    return (
        (
            ProviderStreamEvent(
                ProviderStreamKind.TOOL_CALL,
                tool_call=ToolCallBlock(ToolCallId("call-1"), tool, JsonObject()),
            ),
            ProviderStreamEvent(ProviderStreamKind.COMPLETED),
        ),
        (ProviderStreamEvent(ProviderStreamKind.COMPLETED),),
    )


def _engine(provider: FakeProvider, tools: FakeTools, leases: WorkspaceLeaseManager) -> tuple[TurnEngine, EventBus]:
    bus = EventBus(KernelId("kernel"), max_buffer=1000)
    engine = TurnEngine(
        provider=provider,
        tools=tools,
        sessions=FakeSessions(session()),
        events=bus,
        interactions=FakeInteractions(),
        authorization=FakeAuthorization(),
        options=EngineOptions(default_session_id=SessionId("session-1")),
        workspace_leases=leases,
    )
    return engine, bus


def test_write_permission_tool_emits_workspace_changed() -> None:
    async def exercise() -> None:
        provider = FakeProvider(*_script("write_file"))
        leases = WorkspaceLeaseManager("C:/ws", revision=3)
        engine, bus = _engine(provider, FakeTools(WriteTool("write_file")), leases)
        accepted = await engine.submit(TurnRequest("go", SessionId("session-1")))
        assert accepted.value is not None
        result = await engine.wait(accepted.value.turn_id, 2)
        assert result.value is not None and result.value.status is TurnStatus.SUCCEEDED
        events = [event for event in (await bus.snapshot()).events if event.event_type is EventType.WORKSPACE_CHANGED]
        assert len(events) == 1
        assert isinstance(events[0].payload, ChangeEvent)
        assert events[0].payload.revision == 4  # seeded 3, bumped on tool success
        assert events[0].payload.subject_id == "write_file"
        assert (await leases.snapshot()).revision == 4  # revision actually grew

    asyncio.run(exercise())


def test_failed_write_tool_does_not_bump_revision_or_emit() -> None:
    async def exercise() -> None:
        provider = FakeProvider(*_script("write_file"))
        tool = WriteTool("write_file")
        tool.raises = True  # FakeTool.execute raises RuntimeError -> engine FAILED result
        leases = WorkspaceLeaseManager("C:/ws", revision=3)
        engine, bus = _engine(provider, FakeTools(tool), leases)
        accepted = await engine.submit(TurnRequest("go", SessionId("session-1")))
        assert accepted.value is not None
        result = await engine.wait(accepted.value.turn_id, 2)
        assert result.value is not None and result.value.status is TurnStatus.SUCCEEDED  # turn survives a failed tool
        assert not [event for event in (await bus.snapshot()).events if event.event_type is EventType.WORKSPACE_CHANGED]
        assert (await leases.snapshot()).revision == 3  # untouched

    asyncio.run(exercise())


def test_read_permission_tool_emits_nothing() -> None:
    async def exercise() -> None:
        provider = FakeProvider(*_script("read_file"))
        engine, bus = _engine(provider, FakeTools(FakeTool("read_file")), WorkspaceLeaseManager("C:/ws", revision=3))
        accepted = await engine.submit(TurnRequest("go", SessionId("session-1")))
        assert accepted.value is not None
        result = await engine.wait(accepted.value.turn_id, 2)
        assert result.value is not None and result.value.status is TurnStatus.SUCCEEDED
        assert not [event for event in (await bus.snapshot()).events if event.event_type is EventType.WORKSPACE_CHANGED]

    asyncio.run(exercise())
