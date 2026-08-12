"""Pure UI state transitions; no Textual imports, fully replayable."""

from __future__ import annotations

from dataclasses import dataclass, replace

from kairo_kernel.contracts.enums import EventType, TurnStatus
from kairo_kernel.contracts.events import KernelEvent
from kairo_kernel.contracts.identifiers import SessionId, TurnId
from kairo_kernel.contracts.lifecycle import KernelStatus

from kairo_tui_v2.state import (
    AppState,
    OverlayKind,
    SessionTranscript,
    SessionView,
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
        return replace(state, workspace=action.workspace)
    if isinstance(action, ProfileUpdated):
        return replace(state, profile_label=action.label)
    if isinstance(action, NoticeSet):
        return replace(state, notice=action.text)
    return state


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
        role = str(event.session_id)  # placeholder; C1 maps real roles
        entry = TranscriptEntry(message_id, role, payload.action, payload.content)  # type: ignore[union-attr]
        session_id = event.session_id or SessionId("")
        current = next(
            (item for item in next_state.transcripts if item.session_id == session_id),
            None,
        )
        entries = current.entries + (entry,) if current is not None else (entry,)
        next_state = _replace_transcript(next_state, session_id, entries)
        actions.append(TranscriptReplaced(session_id, entries))
    # LIFECYCLE, TOOL, USAGE, CONTEXT, SESSION_CHANGED, CONFIG_CHANGED,
    # SKILLS_CHANGED, PROVIDER_CHANGED, MEMORY_CHANGED: no state change in C0.
    return next_state, tuple(actions)


def _contains_secret_marker(text: str) -> bool:
    lower = text.lower()
    return "sk-" in lower or "api_key" in lower or "secret" in lower
