"""Pure UI state transitions; no Textual imports, fully replayable."""

from __future__ import annotations

from dataclasses import dataclass, replace

from kairo_kernel.contracts.content import ContentBlock
from kairo_kernel.contracts.enums import EventType, TurnStatus
from kairo_kernel.contracts.events import KernelEvent
from kairo_kernel.contracts.identifiers import MessageId, SessionId, TurnId
from kairo_kernel.contracts.lifecycle import KernelStatus

from kairo_tui_v2.state import (
    AppState,
    OverlayKind,
    PlanCardView,
    SessionTranscript,
    SessionView,
    ToolCardView,
    TranscriptEntry,
    TurnView,
    WorkspaceView,
)

TERMINAL_TURN_STATUSES = frozenset(
    {TurnStatus.SUCCEEDED, TurnStatus.CANCELLED, TurnStatus.FAILED}
)


@dataclass(frozen=True)
class DraftChanged:
    """The composer text changed; mirrors the draft."""

    text: str


@dataclass(frozen=True)
class SubmitDraft:
    """Enter was pressed; the draft is preserved until the kernel accepts."""

    text: str


@dataclass(frozen=True)
class DraftAccepted:
    """The controller confirmed the turn; only then may the draft clear."""

    session_id: SessionId | None = None


@dataclass(frozen=True)
class DraftRejected:
    """Submission failed; the draft stays and the composer must refocus."""

    notice: str = ""


@dataclass(frozen=True)
class KernelStatusChanged:
    status: KernelStatus


@dataclass(frozen=True)
class OpenConnectDialog:
    """Submission found no resolvable chat profile; keep the draft."""

    pending_draft: str


@dataclass(frozen=True)
class CloseOverlay:
    """Modal closed; optionally restore the pending draft."""

    restore_draft: bool = False


@dataclass(frozen=True)
class ConnectSaved:
    """Provider connected; optionally retry the pending draft (Save and send)."""

    send_after: bool = False


@dataclass(frozen=True)
class DraftReady:
    """P1 placeholder: a chat profile resolves; C1 starts the real turn."""

    text: str


@dataclass(frozen=True)
class SubmitFailed:
    """Submission failed before any turn was accepted; draft stays."""

    text: str
    notice: str = ""


@dataclass(frozen=True)
class SessionsLoaded:
    sessions: tuple[SessionView, ...]


@dataclass(frozen=True)
class SessionActivated:
    session_id: SessionId


@dataclass(frozen=True)
class TranscriptReplaced:
    session_id: SessionId
    entries: tuple[TranscriptEntry, ...]


@dataclass(frozen=True)
class TurnUpdated:
    turn: TurnView


@dataclass(frozen=True)
class InteractionsUpdated:
    requests: tuple[object, ...]  # InteractionRequest


@dataclass(frozen=True)
class SequenceAdvanced:
    sequence: int


@dataclass(frozen=True)
class WorkspaceUpdated:
    workspace: WorkspaceView


@dataclass(frozen=True)
class ProfileUpdated:
    label: str


@dataclass(frozen=True)
class NoticeSet:
    text: str


@dataclass(frozen=True)
class TurnStarted:
    """The kernel accepted a turn; the draft is cleared and the turn is active."""

    turn_id: TurnId
    session_id: SessionId


@dataclass(frozen=True)
class TranscriptMerged:
    """One assistant message accumulated by message_id; content is complete."""

    session_id: SessionId
    message_id: MessageId
    role: str
    content: tuple[ContentBlock, ...]


@dataclass(frozen=True)
class ToolCardUpdated:
    card: ToolCardView


@dataclass(frozen=True)
class PlanCardUpdated:
    card: PlanCardView


@dataclass(frozen=True)
class UsageRecorded:
    turn_id: TurnId
    stats: object  # ContextStats


@dataclass(frozen=True)
class StopRequested:
    turn_id: TurnId


@dataclass(frozen=True)
class StopFinished:
    turn_id: TurnId


@dataclass(frozen=True)
class RetryRequested:
    text: str


UiAction = (
    DraftChanged
    | SubmitDraft
    | DraftAccepted
    | DraftRejected
    | KernelStatusChanged
    | OpenConnectDialog
    | CloseOverlay
    | ConnectSaved
    | DraftReady
    | SubmitFailed
    | SessionsLoaded
    | SessionActivated
    | TranscriptReplaced
    | TurnUpdated
    | InteractionsUpdated
    | SequenceAdvanced
    | WorkspaceUpdated
    | ProfileUpdated
    | NoticeSet
    | TurnStarted
    | TranscriptMerged
    | ToolCardUpdated
    | PlanCardUpdated
    | UsageRecorded
    | StopRequested
    | StopFinished
    | RetryRequested
)


def _replace_transcript(
    state: AppState,
    session_id: SessionId,
    entries: tuple[TranscriptEntry, ...],
) -> AppState:
    transcript = SessionTranscript(session_id, entries)
    rest = tuple(item for item in state.transcripts if item.session_id != session_id)
    return replace(state, transcripts=rest + (transcript,))


def _replace_turn(state: AppState, turn: TurnView) -> AppState:
    rest = tuple(item for item in state.turns if item.turn_id != turn.turn_id)
    return replace(state, turns=rest + (turn,))


def reduce(state: AppState, action: UiAction) -> AppState:
    """Apply one action; unknown actions are no-ops for forward compatibility."""
    if isinstance(action, DraftChanged):
        return replace(state, draft=action.text)
    if isinstance(action, SubmitDraft):
        # Never clear here: the controller clears via DraftAccepted only after
        # the kernel accepts the turn (C1). Pending drafts are a P1 concern.
        return state
    if isinstance(action, DraftAccepted):
        return replace(state, draft="")
    if isinstance(action, DraftRejected):
        return replace(state, notice=action.notice or state.notice)
    if isinstance(action, KernelStatusChanged):
        return replace(state, kernel_status=action.status)
    if isinstance(action, OpenConnectDialog):
        return replace(state, pending_draft=action.pending_draft, overlay=OverlayKind.CONNECT)
    if isinstance(action, CloseOverlay):
        draft = (state.pending_draft or state.draft) if action.restore_draft else state.draft
        return replace(state, draft=draft, pending_draft=None, overlay=None)
    if isinstance(action, ConnectSaved):
        pending = state.pending_draft if action.send_after else None
        return replace(state, pending_draft=pending, overlay=None)
    if isinstance(action, DraftReady):
        return replace(state, pending_draft=None)
    if isinstance(action, SubmitFailed):
        return replace(state, notice=action.notice or state.notice)
    if isinstance(action, SessionsLoaded):
        return replace(state, sessions=action.sessions)
    if isinstance(action, SessionActivated):
        return replace(state, active_session_id=action.session_id)
    if isinstance(action, TranscriptReplaced):
        return _replace_transcript(state, action.session_id, action.entries)
    if isinstance(action, TurnUpdated):
        return _replace_turn(state, action.turn)
    if isinstance(action, InteractionsUpdated):
        return replace(state, pending_interactions=action.requests)  # type: ignore[arg-type]
    if isinstance(action, SequenceAdvanced):
        return replace(state, last_sequence=action.sequence)
    if isinstance(action, WorkspaceUpdated):
        current = state.workspace
        if current is not None and action.workspace.revision < current.revision:
            return state  # stale workspace response dropped (M0)
        return replace(state, workspace=action.workspace)
    if isinstance(action, ProfileUpdated):
        return replace(state, profile_label=action.label)
    if isinstance(action, NoticeSet):
        return replace(state, notice=action.text)
    if isinstance(action, TurnStarted):
        return replace(state, draft="", active_turn_id=action.turn_id, pending_draft=None)
    if isinstance(action, TranscriptMerged):
        return _merge_transcript(state, action.session_id, action.message_id, action.role, action.content)
    if isinstance(action, ToolCardUpdated):
        tool_rest = tuple(card for card in state.tool_cards if card.tool_call_id != action.card.tool_call_id)
        return replace(state, tool_cards=tool_rest + (action.card,))
    if isinstance(action, PlanCardUpdated):
        plan_rest = tuple(card for card in state.plan_cards if card.message_id != action.card.message_id)
        return replace(state, plan_cards=plan_rest + (action.card,))
    if isinstance(action, UsageRecorded):
        if any(turn_id == action.turn_id for turn_id, _ in state.usage):
            return state  # usage recorded exactly once per turn
        return replace(state, usage=state.usage + ((action.turn_id, action.stats),))
    if isinstance(action, StopRequested):
        return replace(state, stopping_turn_id=action.turn_id)
    if isinstance(action, StopFinished):
        return replace(state, stopping_turn_id=None)
    if isinstance(action, RetryRequested):
        return state
    return state


def _merge_transcript(
    state: AppState,
    session_id: SessionId,
    message_id: MessageId,
    role: str,
    content: tuple[ContentBlock, ...],
) -> AppState:
    """Merge a delta into the session transcript by message_id."""
    transcript = next(
        (item for item in state.transcripts if item.session_id == session_id),
        None,
    )
    entries = list(transcript.entries) if transcript is not None else []
    existing = next((item for item in entries if item.message_id == message_id), None)
    if existing is None:
        entries.append(TranscriptEntry(message_id, role, "assistant", content))
    else:
        index = entries.index(existing)
        entries[index] = TranscriptEntry(message_id, existing.role, "assistant", existing.content + content)
    return _replace_transcript(state, session_id, tuple(entries))


def fold_event(state: AppState, event: KernelEvent) -> tuple[AppState, tuple[UiAction, ...]]:
    """Pure event fold (C0): typed KernelEvent -> (state, derived actions).

    Stale workspace revisions are dropped, per-turn terminal states are set
    exactly once, and unknown event types are no-ops. Secrets are never part
    of an event payload; a marker scan is applied to any text before it
    reaches the notice.
    """
    if event.sequence <= state.last_sequence:
        return state, ()
    if event.sequence > state.last_sequence + 1:
        # A gap means the event stream is incomplete; the event loop performs
        # a recovery snapshot instead of folding the lone event.
        return replace(state, last_sequence=event.sequence), ()
    actions: list[UiAction] = []
    next_state = replace(state, last_sequence=event.sequence)
    event_type = event.event_type
    payload = event.payload
    if event_type is EventType.TURN:
        turn = next_state.turns
        existing = next((item for item in turn if item.turn_id == event.turn_id), None)
        if existing is not None and existing.terminal:
            return next_state, ()  # terminal exactly once
        terminal = payload.status in TERMINAL_TURN_STATUSES  # type: ignore[union-attr]
        updated = TurnView(
            event.turn_id or TurnId(""),
            event.session_id or SessionId(""),
            payload.status,  # type: ignore[union-attr]
            payload.phase,  # type: ignore[union-attr]
            terminal,
        )
        next_state = _replace_turn(next_state, updated)
        actions.append(TurnUpdated(updated))
    elif event_type is EventType.INTERACTION:
        action = payload.action  # type: ignore[union-attr]
        requests = next_state.pending_interactions
        if action == "requested" and payload.request is not None:  # type: ignore[union-attr]
            requests = requests + (payload.request,)  # type: ignore[union-attr]
        elif action in ("resolved", "expired"):
            interaction_id = payload.interaction_id  # type: ignore[union-attr]
            requests = tuple(
                item for item in requests if item.interaction_id != interaction_id  # type: ignore[union-attr]
            )
        next_state = replace(next_state, pending_interactions=requests)
        actions.append(InteractionsUpdated(requests))
    elif event_type is EventType.NOTICE:
        message = payload.message  # type: ignore[union-attr]
        if _contains_secret_marker(message):
            message = "[redacted]"
        next_state = replace(next_state, notice=message)
        actions.append(NoticeSet(message))
    elif event_type is EventType.WORKSPACE_CHANGED:
        revision = payload.revision  # type: ignore[union-attr]
        workspace = next_state.workspace
        if workspace is not None and revision < workspace.revision:
            return next_state, ()  # stale revision dropped
    elif event_type is EventType.MESSAGE:
        message_id = payload.message_id  # type: ignore[union-attr]
        action = payload.action  # type: ignore[union-attr]
        content = payload.content  # type: ignore[union-attr]
        session_id = event.session_id or SessionId("")
        turn_id = event.turn_id
        if action == "plan_delta" or (action == "completed" and _turn_in_phase(next_state, turn_id, "planning")):
            # Plan output is a structured card, never plain content.
            plan_card = next((card for card in next_state.plan_cards if card.message_id == message_id), None)
            blocks = plan_card.blocks + content if plan_card is not None else content
            card = PlanCardView(session_id, turn_id or TurnId(""), message_id, "Plan", blocks)
            next_state = _fold_replace_plan(next_state, card)
            actions.append(PlanCardUpdated(card))
        else:
            merged = _merge_transcript(next_state, session_id, message_id, "assistant", content)
            next_state = merged
            actions.append(TranscriptMerged(session_id, message_id, "assistant", content))
    elif event_type is EventType.TOOL:
        tool_action = payload.action  # type: ignore[union-attr]
        invocation = payload.invocation  # type: ignore[union-attr]
        if invocation is None:
            return next_state, ()
        tool_call_id = str(invocation.tool_call_id)
        tool_card = next((card for card in next_state.tool_cards if card.tool_call_id == tool_call_id), None)
        if tool_action == "requested":
            tool_view = ToolCardView(tool_call_id, invocation.name, "requested", invocation.arguments)
        elif tool_action == "started":
            base = tool_card or ToolCardView(tool_call_id, invocation.name, "started")
            tool_view = replace(base, status="started")
        elif tool_action == "output":
            chunk = payload.output  # type: ignore[union-attr]
            base = tool_card or ToolCardView(tool_call_id, invocation.name, "running")
            output = base.output + chunk.content if chunk is not None else base.output
            tool_view = replace(base, status="running", output=output)
        else:  # completed
            result = payload.result  # type: ignore[union-attr]
            base = tool_card or ToolCardView(tool_call_id, invocation.name, "completed")
            tool_view = replace(
                base,
                status="completed",
                result_status=result.status.value if result is not None else "",
                error=result.error_message if result is not None else "",
            )
        next_state = _fold_replace_tool(next_state, tool_view)
        actions.append(ToolCardUpdated(tool_view))
    elif event_type is EventType.USAGE:
        stats = payload  # type: ignore[union-attr]
        turn_id = event.turn_id
        if turn_id is not None:
            next_state = replace(next_state, usage=next_state.usage + ((turn_id, stats),))
            actions.append(UsageRecorded(turn_id, stats))
    # LIFECYCLE, CONTEXT, SESSION_CHANGED, CONFIG_CHANGED, SKILLS_CHANGED,
    # PROVIDER_CHANGED, MEMORY_CHANGED: no state change in C1.
    return next_state, tuple(actions)


def _fold_replace_tool(state: AppState, card: ToolCardView) -> AppState:
    rest = tuple(item for item in state.tool_cards if item.tool_call_id != card.tool_call_id)
    return replace(state, tool_cards=rest + (card,))


def _fold_replace_plan(state: AppState, card: PlanCardView) -> AppState:
    rest = tuple(item for item in state.plan_cards if item.message_id != card.message_id)
    return replace(state, plan_cards=rest + (card,))


def _turn_in_phase(state: AppState, turn_id: TurnId | None, phase: str) -> bool:
    if turn_id is None:
        return False
    turn = next((item for item in state.turns if item.turn_id == turn_id), None)
    return turn is not None and turn.phase is not None and turn.phase.value == phase


def _contains_secret_marker(text: str) -> bool:
    lower = text.lower()
    return "sk-" in lower or "api_key" in lower or "secret" in lower
