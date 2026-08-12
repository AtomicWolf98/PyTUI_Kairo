"""C0 acceptance: pure reducer and event fold semantics."""

from __future__ import annotations

from datetime import datetime, timezone

from kairo_kernel.contracts.content import TextBlock
from kairo_kernel.contracts.enums import EventType, InteractionAction, InteractionKind, TurnStatus
from kairo_kernel.contracts.events import (
    ChangeEvent,
    InteractionEvent,
    KernelEvent,
    MessageEvent,
    NoticeEvent,
    TurnEvent,
)
from kairo_kernel.contracts.identifiers import (
    EventId,
    InteractionId,
    KernelId,
    MessageId,
    SessionId,
    TurnId,
)
from kairo_kernel.contracts.interactions import InteractionChoice, InteractionRequest

from kairo_tui_v2.reducer import (
    CloseOverlay,
    ConnectSaved,
    DraftAccepted,
    DraftChanged,
    InteractionsUpdated,
    NoticeSet,
    OpenConnectDialog,
    SessionsLoaded,
    SubmitDraft,
    TranscriptReplaced,
    TurnUpdated,
    fold_event,
    reduce,
)
from kairo_tui_v2.state import AppState, OverlayKind, SessionView, TurnView, WorkspaceView

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


def _interaction() -> InteractionRequest:
    return InteractionRequest(
        InteractionId("interaction-1"),
        TURN,
        SESSION,
        InteractionKind.TOOL_APPROVAL,
        "Run this tool?",
        (InteractionChoice(InteractionAction.APPROVE_ONCE, "Approve once"), InteractionChoice(InteractionAction.REJECT, "Reject")),
        None,
        InteractionAction.REJECT,
    )


def test_draft_lifecycle_actions() -> None:
    state = reduce(AppState(), DraftChanged("hello"))
    assert state.draft == "hello"
    state = reduce(state, SubmitDraft("hello"))
    assert state.draft == "hello"  # never cleared on submit
    state = reduce(state, OpenConnectDialog("hello"))
    assert state.overlay is OverlayKind.CONNECT
    assert state.pending_draft == "hello"
    state = reduce(state, CloseOverlay(restore_draft=True))
    assert state.draft == "hello"
    assert state.overlay is None
    state = reduce(state, DraftChanged("bye"))
    state = reduce(state, ConnectSaved(send_after=False))
    assert state.overlay is None
    state = reduce(state, DraftAccepted())
    assert state.draft == ""


def test_sessions_loaded_replaces_view() -> None:
    session = SessionView(SESSION, "Notes", 3)
    state = reduce(AppState(), SessionsLoaded((session,)))
    assert state.sessions == (session,)


def test_transcript_replaced_keeps_sessions_separate() -> None:
    entry = TextBlock("hi")
    state = AppState()
    state = reduce(
        state,
        TranscriptReplaced(SESSION, (entry,)),  # type: ignore[arg-type]
    )
    state = reduce(
        state,
        TranscriptReplaced(SessionId("session-2"), (TextBlock("x"),)),  # type: ignore[arg-type]
    )
    assert len(state.transcripts) == 2
    first = next(item for item in state.transcripts if item.session_id == SESSION)
    assert first.entries[0] is entry


def test_fold_turn_terminal_exactly_once() -> None:
    state = AppState()
    state, actions = fold_event(
        state,
        _event(1, EventType.TURN, TurnEvent(TurnStatus.RUNNING, None), turn_id=TURN, session_id=SESSION),
    )
    assert actions == (TurnUpdated(TurnView(TURN, SESSION, TurnStatus.RUNNING, None, False)),)
    state, actions = fold_event(
        state,
        _event(2, EventType.TURN, TurnEvent(TurnStatus.SUCCEEDED, None), turn_id=TURN, session_id=SESSION),
    )
    assert actions[0].turn.terminal is True
    # A second terminal event for the same turn is a no-op.
    state, actions = fold_event(
        state,
        _event(3, EventType.TURN, TurnEvent(TurnStatus.CANCELLED, None), turn_id=TURN, session_id=SESSION),
    )
    assert actions == ()


def test_fold_duplicate_and_gap_sequences() -> None:
    state = AppState()
    state, _ = fold_event(state, _event(2, EventType.TURN, TurnEvent(TurnStatus.RUNNING, None), turn_id=TURN))
    assert state.last_sequence == 2
    state, actions = fold_event(state, _event(2, EventType.TURN, TurnEvent(TurnStatus.RUNNING, None), turn_id=TURN))
    assert actions == ()  # duplicate dropped
    # A gap advances the sequence but folds nothing; recovery is the loop's job.
    state, actions = fold_event(state, _event(5, EventType.TURN, TurnEvent(TurnStatus.SUCCEEDED, None), turn_id=TURN))
    assert actions == ()
    assert state.last_sequence == 5


def test_fold_unknown_event_no_crash() -> None:
    state = AppState()
    state, actions = fold_event(
        state,
        _event(1, EventType.CONFIG_CHANGED, None),  # type: ignore[arg-type]
    )
    assert actions == ()


def test_fold_workspace_stale_revision_dropped() -> None:
    state = AppState(workspace=WorkspaceView("/root", 5))
    state, actions = fold_event(
        state,
        _event(1, EventType.WORKSPACE_CHANGED, ChangeEvent(3), workspace_revision=3),
    )
    assert actions == ()


def test_fold_interaction_requested_and_resolved() -> None:
    state = AppState()
    state, actions = fold_event(
        state,
        _event(
            1,
            EventType.INTERACTION,
            InteractionEvent("requested", request=_interaction()),
            turn_id=TURN,
            session_id=SESSION,
        ),
    )
    assert actions == (InteractionsUpdated((_interaction(),)),)
    assert state.pending_interactions == (_interaction(),)
    state, actions = fold_event(
        state,
        _event(
            2,
            EventType.INTERACTION,
            InteractionEvent("resolved", interaction_id=InteractionId("interaction-1")),
            turn_id=TURN,
            session_id=SESSION,
        ),
    )
    assert state.pending_interactions == ()


def test_fold_message_appends_transcript() -> None:
    state = AppState()
    state, actions = fold_event(
        state,
        _event(
            1,
            EventType.MESSAGE,
            MessageEvent(MessageId("message-1"), "append", (TextBlock("hi"),)),
            session_id=SESSION,
        ),
    )
    assert actions and isinstance(actions[0], TranscriptReplaced)
    assert len(state.transcripts) == 1


def test_fold_notice_secret_marker_redacted() -> None:
    state = AppState()
    state, actions = fold_event(
        state,
        _event(1, EventType.NOTICE, NoticeEvent("error", "failed with sk-abc123secret")),
    )
    assert actions == (NoticeSet("[redacted]"),)
    assert state.notice == "[redacted]"


def test_reduce_unknown_action_is_noop() -> None:
    state = AppState(draft="x")
    reduced = reduce(state, object())  # type: ignore[arg-type]
    assert reduced is state
