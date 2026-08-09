"""Typed store + pure reducer + event folding."""

from __future__ import annotations

from datetime import datetime, timezone

from kairo_kernel.contracts.content import Message, TextBlock, ToolCallBlock
from kairo_kernel.contracts.enums import (  # noqa: F401
    EventType,
    InteractionAction,
    InteractionKind,
    LifecycleState,
    MessageKind,
    MessageRole,
    OperationScope,
    ToolExecutionStatus,
    TurnStatus,
)
from kairo_kernel.contracts.events import (  # noqa: F401
    InteractionEvent,
    KernelEvent,
    LifecycleEvent,
    MessageEvent,
    ToolEvent,
    TurnEvent,
)
from kairo_kernel.contracts.identifiers import (
    EventId,
    InteractionId,
    KernelId,
    MessageId,
    SessionId,
    ToolCallId,
    TurnId,
)
from kairo_kernel.contracts.interactions import InteractionChoice, InteractionRequest
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.lifecycle import ContextStats, KernelStatus  # noqa: F401
from kairo_kernel.contracts.tools import ToolInvocation, ToolOutputChunk, ToolResult

from kairo_tui.store import (
    MAX_EVENT_LOG,
    AppState,
    AppStore,
    ChatMessage,
    DraftAction,
    EventAction,
    PageAction,  # noqa: F401
    PageId,
    RecoveryAction,
    SessionsAction,  # noqa: F401
    UserTurnAction,
    WorkspaceAction,  # noqa: F401
    fold_event,
    reduce,
)

NOW = datetime(2026, 8, 8, tzinfo=timezone.utc)


def _event(
    sequence: int,
    event_type: EventType,
    payload,
    session: str = "s1",
    turn: str = "t1",
) -> KernelEvent:
    return KernelEvent(
        EventId(f"e{sequence}"),
        KernelId("k1"),
        sequence,
        NOW,
        event_type,
        payload,
        turn_id=TurnId(turn),
        session_id=SessionId(session),
    )


def test_initial_state() -> None:
    state = AppState()
    assert state.page is PageId.SETUP
    assert state.last_event_sequence == 0
    assert not state.setup_complete


def test_dispatch_notifies_listeners_in_order() -> None:
    store = AppStore(AppState())
    seen: list[str] = []
    listener = lambda state: seen.append(state.draft)  # noqa: E731
    store.subscribe(listener)
    store.dispatch(DraftAction("hello"))
    assert seen == ["hello"]


def test_fold_turn_event_updates_turn_status() -> None:
    state = reduce(AppState(), EventAction(_event(1, EventType.TURN, TurnEvent(TurnStatus.RUNNING, None))))
    assert state.turn_status["t1"] == "running"
    assert state.last_event_sequence == 1
    state = fold_event(state, _event(2, EventType.TURN, TurnEvent(TurnStatus.SUCCEEDED, None)))
    assert state.turn_status["t1"] == "succeeded"


def test_fold_terminal_turn_removes_active_turn() -> None:
    from kairo_kernel.contracts.turns import ActiveTurn

    state = AppState(active_turns=(ActiveTurn(TurnId("t1"), SessionId("s1"), TurnStatus.RUNNING),))
    state = fold_event(state, _event(3, EventType.TURN, TurnEvent(TurnStatus.SUCCEEDED, None)))
    assert state.active_turns == ()


def test_fold_message_event_keeps_normalized_log() -> None:
    message = Message(MessageId("m1"), MessageRole.ASSISTANT, MessageKind.CHAT, (TextBlock("hi"),))
    state = AppState()
    state = fold_event(state, _event(4, EventType.MESSAGE, MessageEvent(message.message_id, "append", message.content)))
    assert len(state.events) == 1
    assert state.events[0].sequence == 4


def test_event_log_is_bounded() -> None:
    from kairo_kernel.contracts.events import NoticeEvent

    state = AppState()
    for sequence in range(1, MAX_EVENT_LOG + 5):
        state = fold_event(state, _event(sequence, EventType.NOTICE, NoticeEvent("info", "tick")))
    assert len(state.events) == MAX_EVENT_LOG
    assert state.events[0].sequence == 5


def test_workspace_changed_bumps_revision() -> None:
    from kairo_kernel.contracts.events import ChangeEvent

    state = reduce(
        AppState(),
        EventAction(_event(9, EventType.WORKSPACE_CHANGED, ChangeEvent(7, "C:/ws", "Workspace moved."))),
    )
    assert state.workspace_revision == 7


def _plan_approval_request(turn: str = "t1") -> InteractionRequest:
    return InteractionRequest(
        InteractionId(f"i{turn}"),
        TurnId(turn),
        SessionId("s1"),
        InteractionKind.PLAN_APPROVAL,
        "Approve the plan?",
        (InteractionChoice(InteractionAction.STOP, "Stop"),),
        None,
        InteractionAction.STOP,
    )


def test_delta_events_append_to_same_message() -> None:
    state = AppState()
    state = fold_event(
        state, _event(1, EventType.MESSAGE, MessageEvent(MessageId("m1"), "delta", (TextBlock("a"),)))
    )
    state = fold_event(
        state, _event(2, EventType.MESSAGE, MessageEvent(MessageId("m1"), "delta", (TextBlock("b"),)))
    )
    assert len(state.messages) == 1
    message = state.messages[0]
    assert message.content == (TextBlock("a"), TextBlock("b"))
    assert message.complete is False
    assert message.revision == 2


def test_completed_event_replaces_content_and_sets_complete() -> None:
    state = AppState()
    state = fold_event(
        state, _event(1, EventType.MESSAGE, MessageEvent(MessageId("m1"), "delta", (TextBlock("partial"),)))
    )
    completed = (
        TextBlock("full"),
        ToolCallBlock(ToolCallId("tc1"), "read_file", JsonObject.from_pairs(("path", "a.txt"))),
    )
    state = fold_event(
        state, _event(2, EventType.MESSAGE, MessageEvent(MessageId("m1"), "completed", completed))
    )
    assert len(state.messages) == 1
    message = state.messages[0]
    assert message.content == completed
    assert message.complete is True
    assert message.revision == 1


def test_plan_delta_appends_like_delta() -> None:
    state = AppState()
    state = fold_event(
        state, _event(1, EventType.MESSAGE, MessageEvent(MessageId("m1"), "plan_delta", (TextBlock("plan"),)))
    )
    state = fold_event(
        state,
        _event(2, EventType.MESSAGE, MessageEvent(MessageId("m1"), "plan_delta", (TextBlock(" step"),))),
    )
    assert len(state.messages) == 1
    message = state.messages[0]
    assert message.content == (TextBlock("plan"), TextBlock(" step"))
    assert message.complete is False


def test_tool_events_build_card_stages() -> None:
    invocation = ToolInvocation(
        ToolCallId("tc1"),
        TurnId("t1"),
        SessionId("s1"),
        "read_file",
        JsonObject.from_pairs(("path", "a.txt")),
        OperationScope.EXTERNAL,
    )
    state = AppState()
    state = fold_event(
        state, _event(1, EventType.TOOL, ToolEvent("requested", invocation=invocation))
    )
    state = fold_event(
        state, _event(2, EventType.TOOL, ToolEvent("started", invocation=invocation))
    )
    chunk = ToolOutputChunk(ToolCallId("tc1"), 1, (TextBlock("data"),))
    state = fold_event(
        state, _event(3, EventType.TOOL, ToolEvent("output", invocation=invocation, output=chunk))
    )
    result = ToolResult(
        ToolCallId("tc1"), "read_file", ToolExecutionStatus.SUCCEEDED, (TextBlock("data"),), NOW, NOW
    )
    state = fold_event(
        state, _event(4, EventType.TOOL, ToolEvent("completed", invocation=invocation, result=result))
    )
    assert len(state.tool_cards) == 1
    card = state.tool_cards[0]
    assert card.stage == "completed"
    assert card.output == (chunk,)
    assert card.result == result
    assert card.sequence == 4


def test_plan_approval_marks_latest_assistant_message() -> None:
    state = AppState()
    state = fold_event(
        state, _event(1, EventType.MESSAGE, MessageEvent(MessageId("m1"), "completed", (TextBlock("plan"),)))
    )
    state = fold_event(
        state, _event(2, EventType.INTERACTION, InteractionEvent("requested", request=_plan_approval_request()))
    )
    assert state.messages[0].plan is True
    assert state.messages[0].revision == 2


def test_recovery_merge_history_wins_by_id() -> None:
    state = AppState()
    state = fold_event(
        state, _event(1, EventType.MESSAGE, MessageEvent(MessageId("m1"), "delta", (TextBlock("partial"),)))
    )
    state = fold_event(
        state, _event(2, EventType.MESSAGE, MessageEvent(MessageId("m2"), "delta", (TextBlock("inflight"),)))
    )
    full_m1 = ChatMessage(
        "m1", "s1", "t1", 1, MessageRole.ASSISTANT, MessageKind.CHAT,
        (TextBlock("full"), TextBlock("content")), complete=True,
    )
    state = reduce(state, RecoveryAction(messages=(full_m1,)))
    by_id = {m.message_id: m for m in state.messages}
    assert set(by_id) == {"m1", "m2"}
    assert by_id["m1"].content == full_m1.content
    assert by_id["m1"].complete is True
    assert by_id["m2"].content == (TextBlock("inflight"),)


def test_recovery_bumps_messages_epoch() -> None:
    state = reduce(AppState(), RecoveryAction(messages=()))
    assert state.messages_epoch == 1
    state = reduce(state, RecoveryAction(messages=()))
    assert state.messages_epoch == 2


def test_user_turn_action_records_bubble() -> None:
    state = fold_event(AppState(), _event(5, EventType.TURN, TurnEvent(TurnStatus.RUNNING, None)))
    state = reduce(state, UserTurnAction("s1", "t1", "hello"))
    assert state.user_turns["t1"].text == "hello"
    assert state.user_turns["t1"].sequence == state.last_event_sequence


def test_terminal_turn_keeps_messages() -> None:
    state = AppState()
    state = fold_event(
        state, _event(1, EventType.MESSAGE, MessageEvent(MessageId("m1"), "completed", (TextBlock("done"),)))
    )
    state = fold_event(state, _event(2, EventType.TURN, TurnEvent(TurnStatus.SUCCEEDED, None)))
    assert len(state.messages) == 1
    assert state.messages[0].message_id == "m1"


def test_messages_ordered_by_sequence_across_sessions() -> None:
    state = AppState()
    for seq, session, mid, text in (
        (1, "s1", "m1", "a1"),
        (2, "s2", "m2", "b1"),
        (3, "s1", "m1", "a2"),
        (4, "s2", "m2", "b2"),
        (5, "s1", "m3", "a3"),
    ):
        state = fold_event(
            state,
            _event(seq, EventType.MESSAGE, MessageEvent(MessageId(mid), "delta", (TextBlock(text),)),
                   session=session),
        )
    assert [m.message_id for m in state.messages] == ["m1", "m2", "m3"]
    assert [m.sequence for m in state.messages] == [3, 4, 5]
    s1_messages = [m for m in state.messages if m.session_id == "s1"]
    assert [m.sequence for m in s1_messages] == [3, 5]
    assert s1_messages[0].content == (TextBlock("a1"), TextBlock("a2"))
