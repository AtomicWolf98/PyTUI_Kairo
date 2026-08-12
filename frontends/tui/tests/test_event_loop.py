"""C0 acceptance: kernel event loop replay, gap, overflow, recovery."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from kairo_kernel.contracts.enums import EventType, InteractionAction, InteractionKind, TurnStatus
from kairo_kernel.contracts.events import (
    ChangeEvent,
    InteractionEvent,
    KernelEvent,
    NoticeEvent,
    TurnEvent,
)
from kairo_kernel.contracts.identifiers import (
    EventId,
    InteractionId,
    KernelId,
    SessionId,
    TurnId,
)
from kairo_kernel.contracts.interactions import InteractionChoice, InteractionRequest
from support.fakes import FakeKernel

from kairo_tui.event_loop import KernelEventLoop
from kairo_tui.state import AppState, WorkspaceView

SESSION = SessionId("session-1")
TURN = TurnId("turn-1")


def _event(
    sequence: int,
    event_type: EventType,
    payload: object,
    *,
    turn_id: TurnId | None = None,
    session_id: SessionId | None = None,
    workspace_revision: int = 0,
) -> KernelEvent:
    return KernelEvent(
        EventId(f"event-{sequence}"),
        KernelId("kernel-1"),
        sequence,
        datetime.now(timezone.utc),
        event_type,
        payload,  # type: ignore[arg-type]
        turn_id=turn_id,
        session_id=session_id,
        workspace_revision=workspace_revision,
    )


async def _run_until_idle() -> None:
    await asyncio.sleep(0.02)
    await asyncio.sleep(0)


async def test_replay_then_live_no_duplicates() -> None:
    kernel = FakeKernel()
    events = kernel.events
    for sequence in (1, 2, 3):
        await events.emit(_event(sequence, EventType.TURN, TurnEvent(TurnStatus.RUNNING, None), turn_id=TURN))

    seen: list[int] = []
    loop = KernelEventLoop(kernel, AppState(), emit=lambda state, actions: seen.append(state.last_sequence))  # type: ignore[arg-type]
    loop.start()
    await _run_until_idle()
    await events.emit(_event(4, EventType.TURN, TurnEvent(TurnStatus.SUCCEEDED, None), turn_id=TURN))
    await _run_until_idle()
    assert seen == [1, 2, 3, 4]
    assert loop.state.last_sequence == 4
    await loop.close()


async def test_gap_triggers_recovery() -> None:
    kernel = FakeKernel()
    events = kernel.events
    await events.emit(_event(1, EventType.TURN, TurnEvent(TurnStatus.RUNNING, None), turn_id=TURN))
    recovered: list[str] = []
    loop = KernelEventLoop(
        kernel,
        AppState(),
        emit=lambda state, actions: None,
        recover=lambda: recovered.append("recover"),
    )
    loop.start()
    await _run_until_idle()
    await events.emit(_event(3, EventType.TURN, TurnEvent(TurnStatus.SUCCEEDED, None), turn_id=TURN))
    await _run_until_idle()
    assert recovered == ["recover"]
    assert loop.state.last_sequence == 3
    await loop.close()


async def test_overflow_resubscribes_without_deadlock() -> None:
    kernel = FakeKernel()
    events = kernel.events
    for sequence in (1, 2):
        await events.emit(_event(sequence, EventType.TURN, TurnEvent(TurnStatus.RUNNING, None), turn_id=TURN))
    # Override the subscription to fail once after two delivered events.
    real_subscribe = events.subscribe

    async def failing_subscribe(after_sequence: int = 0, queue_size: int | None = None):
        sub = await real_subscribe(after_sequence, queue_size)
        sub._overflow_after = 2  # type: ignore[attr-defined]
        return sub

    events.subscribe = failing_subscribe  # type: ignore[method-assign]
    loop = KernelEventLoop(kernel, AppState(), emit=lambda state, actions: None)
    loop.start()
    await _run_until_idle()
    await events.emit(_event(3, EventType.TURN, TurnEvent(TurnStatus.SUCCEEDED, None), turn_id=TURN))
    await _run_until_idle()
    assert loop.state.last_sequence == 3
    assert len(events.subscribe_calls) >= 2  # re-subscribed after overflow
    await loop.close()


async def test_duplicate_events_are_noop() -> None:
    kernel = FakeKernel()
    events = kernel.events
    await events.emit(_event(1, EventType.TURN, TurnEvent(TurnStatus.RUNNING, None), turn_id=TURN))
    loop = KernelEventLoop(kernel, AppState(), emit=lambda state, actions: None)
    loop.start()
    await _run_until_idle()
    await events.emit(_event(1, EventType.TURN, TurnEvent(TurnStatus.RUNNING, None), turn_id=TURN))
    await _run_until_idle()
    assert loop.state.last_sequence == 1
    await loop.close()


async def test_unknown_event_no_crash() -> None:
    kernel = FakeKernel()
    events = kernel.events
    await events.emit(_event(1, EventType.CONFIG_CHANGED, None))  # type: ignore[arg-type]
    loop = KernelEventLoop(kernel, AppState(), emit=lambda state, actions: None)
    loop.start()
    await _run_until_idle()
    assert loop.state.last_sequence == 1
    await loop.close()


async def test_terminal_exactly_once() -> None:
    kernel = FakeKernel()
    events = kernel.events
    terminal_count: list[int] = []
    loop = KernelEventLoop(
        kernel,
        AppState(),
        emit=lambda state, actions: terminal_count.append(
            sum(1 for action in actions if getattr(action, "turn", None) is not None and action.turn.terminal)
        ),
    )
    loop.start()
    await _run_until_idle()
    await events.emit(_event(1, EventType.TURN, TurnEvent(TurnStatus.SUCCEEDED, None), turn_id=TURN, session_id=SESSION))
    await _run_until_idle()
    await events.emit(_event(2, EventType.TURN, TurnEvent(TurnStatus.CANCELLED, None), turn_id=TURN, session_id=SESSION))
    await _run_until_idle()
    assert terminal_count == [1, 0]
    await loop.close()


async def test_session_correlation() -> None:
    kernel = FakeKernel()
    events = kernel.events
    loop = KernelEventLoop(kernel, AppState(), emit=lambda state, actions: None)
    loop.start()
    await _run_until_idle()
    await events.emit(_event(1, EventType.TURN, TurnEvent(TurnStatus.RUNNING, None), turn_id=TURN, session_id=SESSION))
    await _run_until_idle()
    turn = next(item for item in loop.state.turns if item.turn_id == TURN)
    assert turn.session_id == SESSION
    await loop.close()


async def test_workspace_stale_revision_dropped() -> None:
    kernel = FakeKernel()
    events = kernel.events
    loop = KernelEventLoop(
        kernel,
        AppState(workspace=WorkspaceView("/root", 5)),
        emit=lambda state, actions: None,
    )
    loop.start()
    await _run_until_idle()
    await events.emit(_event(1, EventType.WORKSPACE_CHANGED, ChangeEvent(3), workspace_revision=3))
    await _run_until_idle()
    assert loop.state.last_sequence == 1
    await loop.close()


async def test_interaction_requested_and_resolved() -> None:
    kernel = FakeKernel()
    events = kernel.events
    loop = KernelEventLoop(kernel, AppState(), emit=lambda state, actions: None)
    loop.start()
    await _run_until_idle()
    request = InteractionRequest(
        InteractionId("i-1"),
        TURN,
        SESSION,
        InteractionKind.TOOL_APPROVAL,
        "Run?",
        (InteractionChoice(InteractionAction.APPROVE_ONCE, "Approve once"), InteractionChoice(InteractionAction.REJECT, "Reject")),
        None,
        InteractionAction.REJECT,
    )
    await events.emit(
        _event(
            1,
            EventType.INTERACTION,
            InteractionEvent("requested", request=request),
            turn_id=TURN,
            session_id=SESSION,
        )
    )
    await _run_until_idle()
    assert loop.state.pending_interactions == (request,)
    await events.emit(
        _event(
            2,
            EventType.INTERACTION,
            InteractionEvent("resolved", interaction_id=InteractionId("i-1")),
            turn_id=TURN,
            session_id=SESSION,
        )
    )
    await _run_until_idle()
    assert loop.state.pending_interactions == ()
    await loop.close()


async def test_notice_secret_scan_redacts() -> None:
    kernel = FakeKernel()
    events = kernel.events
    loop = KernelEventLoop(kernel, AppState(), emit=lambda state, actions: None)
    loop.start()
    await _run_until_idle()
    await events.emit(_event(1, EventType.NOTICE, NoticeEvent("error", "boom sk-abc123")))
    await _run_until_idle()
    assert loop.state.notice == "[redacted]"
    await loop.close()


async def test_close_leaves_no_task() -> None:
    kernel = FakeKernel()
    loop = KernelEventLoop(kernel, AppState(), emit=lambda state, actions: None)
    loop.start()
    await _run_until_idle()
    await loop.close()
    assert loop._task is None
    await asyncio.sleep(0.01)


async def test_fifty_emit_recover_rounds_no_deadlock() -> None:
    kernel = FakeKernel()
    events = kernel.events
    recovered: list[str] = []
    loop = KernelEventLoop(
        kernel,
        AppState(),
        emit=lambda state, actions: None,
        recover=lambda: recovered.append("recover"),
    )
    loop.start()
    sequence = 0
    for _ in range(50):
        sequence += 1
        await events.emit(_event(sequence, EventType.TURN, TurnEvent(TurnStatus.RUNNING, None), turn_id=TURN))
        await _run_until_idle()
        sequence += 2  # force a gap every round
        await events.emit(_event(sequence, EventType.TURN, TurnEvent(TurnStatus.SUCCEEDED, None), turn_id=TURN))
        await _run_until_idle()
    assert loop.state.last_sequence == sequence
    assert len(recovered) == 50
    await loop.close()
