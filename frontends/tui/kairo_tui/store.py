"""Typed application store: immutable AppState + pure reducer.

Normalization rule (tui_plan.md): every collection is keyed by kernel IDs
(session/turn/message/interaction/event); the UI never matches by display text.
Event folding keeps the last event sequence so the EventPump can resubscribe.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from kairo_kernel.contracts.content import ContentBlock
from kairo_kernel.contracts.enums import EventType, InteractionKind, MessageKind, MessageRole, TurnStatus
from kairo_kernel.contracts.events import ChangeEvent, InteractionEvent, KernelEvent, MessageEvent, ToolEvent, TurnEvent
from kairo_kernel.contracts.identifiers import SessionId
from kairo_kernel.contracts.interactions import InteractionRequest
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.lifecycle import KernelStatus
from kairo_kernel.contracts.support import SessionSummary
from kairo_kernel.contracts.tools import ToolOutputChunk, ToolResult
from kairo_kernel.contracts.turns import ActiveTurn

from kairo_tui.config_document import ConfigDocument

MAX_EVENT_LOG = 2000
TERMINAL_STATUSES = frozenset({TurnStatus.SUCCEEDED, TurnStatus.CANCELLED, TurnStatus.FAILED})


class PageId(str, Enum):
    CHAT = "chat"
    SESSIONS = "sessions"
    WORKSPACE = "workspace"
    MEMORY = "memory"
    EXTENSIONS = "extensions"
    SETTINGS = "settings"
    DOCTOR = "doctor"
    SETUP = "setup"


@dataclass(frozen=True)
class AppState:
    last_event_sequence: int = 0
    kernel_status: KernelStatus | None = None
    sessions: tuple[SessionSummary, ...] = ()
    active_turns: tuple[ActiveTurn, ...] = ()
    pending_interactions: tuple[InteractionRequest, ...] = ()
    workspace_root: str = ""
    workspace_revision: int = 0
    events: tuple[KernelEvent, ...] = ()  # bounded, sequence-ordered
    turn_status: dict[str, str] = field(default_factory=dict)
    document: ConfigDocument = field(default_factory=ConfigDocument)
    setup_complete: bool = False
    active_session_id: str | None = None
    page: PageId = PageId.SETUP
    inspector_visible: bool = False
    draft: str = ""
    safe_mode: bool = False
    reduced_motion: bool = False
    messages: tuple[ChatMessage, ...] = ()
    tool_cards: tuple[ToolCard, ...] = ()
    user_turns: dict[str, UserTurn] = field(default_factory=dict)
    messages_epoch: int = 0
    compat_mode: bool = False


class Action:
    """Marker base for all store intents."""


@dataclass(frozen=True)
class KernelStatusAction(Action):
    status: KernelStatus


@dataclass(frozen=True)
class SessionsAction(Action):
    sessions: tuple[SessionSummary, ...]


@dataclass(frozen=True)
class ActiveTurnsAction(Action):
    turns: tuple[ActiveTurn, ...]


@dataclass(frozen=True)
class InteractionsAction(Action):
    pending: tuple[InteractionRequest, ...]


@dataclass(frozen=True)
class WorkspaceAction(Action):
    root: str
    revision: int


@dataclass(frozen=True)
class EventAction(Action):
    event: KernelEvent


@dataclass(frozen=True)
class RecoveryAction(Action):
    """Result of the replay-gap / overflow re-read (EventPump recovery).

    last_event_sequence, when not None, advances the store's last sequence so
    the pump can resubscribe past a replay gap instead of re-hitting it.
    """

    status: KernelStatus | None = None
    sessions: tuple[SessionSummary, ...] = ()
    turns: tuple[ActiveTurn, ...] = ()
    pending: tuple[InteractionRequest, ...] = ()
    workspace_root: str = ""
    workspace_revision: int = 0
    last_event_sequence: int | None = None
    messages: tuple[ChatMessage, ...] = ()


@dataclass(frozen=True)
class ConfigAction(Action):
    document: ConfigDocument
    setup_complete: bool


@dataclass(frozen=True)
class PageAction(Action):
    page: PageId


@dataclass(frozen=True)
class CompatAction(Action):
    active: bool


@dataclass(frozen=True)
class InspectorAction(Action):
    visible: bool


@dataclass(frozen=True)
class DraftAction(Action):
    text: str


@dataclass(frozen=True)
class SessionAction(Action):
    session_id: str | None


@dataclass(frozen=True)
class ChatMessage:
    message_id: str
    session_id: str
    turn_id: str
    sequence: int          # KernelEvent.sequence of the last update (ordering key)
    role: MessageRole
    kind: MessageKind
    content: tuple[ContentBlock, ...]
    complete: bool = False  # a "completed" event was folded (or history re-read)
    plan: bool = False      # marked by a PLAN_APPROVAL interaction
    revision: int = 0       # bumped on every fold → widget re-render trigger


@dataclass(frozen=True)
class ToolCard:
    tool_call_id: str
    session_id: str
    turn_id: str
    name: str
    arguments: JsonObject
    stage: str             # "requested" | "started" | "output" | "completed"
    output: tuple[ToolOutputChunk, ...] = ()
    result: ToolResult | None = None
    sequence: int = 0


@dataclass(frozen=True)
class UserTurn:            # the TUI's own user bubble (events never carry user messages)
    session_id: str
    turn_id: str
    text: str
    sequence: int


@dataclass(frozen=True)
class UserTurnAction(Action):
    session_id: str
    turn_id: str
    text: str


def reduce(state: AppState, action: Action) -> AppState:
    if isinstance(action, KernelStatusAction):
        return _replace(state, kernel_status=action.status)
    if isinstance(action, SessionsAction):
        return _replace(state, sessions=action.sessions)
    if isinstance(action, ActiveTurnsAction):
        return _replace(state, active_turns=action.turns)
    if isinstance(action, InteractionsAction):
        return _replace(state, pending_interactions=action.pending)
    if isinstance(action, WorkspaceAction):
        return _replace(state, workspace_root=action.root, workspace_revision=action.revision)
    if isinstance(action, EventAction):
        return fold_event(state, action.event)
    if isinstance(action, RecoveryAction):
        state = _replace(
            state,
            kernel_status=action.status,
            sessions=action.sessions,
            active_turns=action.turns,
            pending_interactions=action.pending,
            workspace_root=action.workspace_root,
            workspace_revision=action.workspace_revision,
        )
        if action.last_event_sequence is not None:
            state = _replace(state, last_event_sequence=action.last_event_sequence)
        # Recovery re-read the sessions' conversation histories: rebind the timeline.
        state = _replace(state, messages=_merge_messages(state.messages, action.messages),
                         messages_epoch=state.messages_epoch + 1)
        return state
    if isinstance(action, ConfigAction):
        return _replace(state, document=action.document, setup_complete=action.setup_complete)
    if isinstance(action, PageAction):
        return _replace(state, page=action.page)
    if isinstance(action, CompatAction):
        return _replace(state, compat_mode=action.active)
    if isinstance(action, InspectorAction):
        return _replace(state, inspector_visible=action.visible)
    if isinstance(action, DraftAction):
        return _replace(state, draft=action.text)
    if isinstance(action, SessionAction):
        return _replace(state, active_session_id=action.session_id)
    if isinstance(action, UserTurnAction):
        user_turn = UserTurn(action.session_id, action.turn_id, action.text, state.last_event_sequence)
        return _replace(state, user_turns={**state.user_turns, action.turn_id: user_turn})
    return state


def fold_event(state: AppState, event: KernelEvent) -> AppState:
    """Fold one kernel event into normalized state (incremental rendering path)."""
    state = _replace(state, last_event_sequence=event.sequence, events=_push_event(state.events, event))
    payload = event.payload
    if isinstance(payload, TurnEvent):
        status = payload.status.value
        turn_status = {**state.turn_status, str(event.turn_id): status}
        active = _fold_turn_active(state.active_turns, event, payload)
        return _replace(state, turn_status=turn_status, active_turns=active)
    if isinstance(payload, MessageEvent):
        return _fold_message(state, event, payload)
    if isinstance(payload, ToolEvent):
        return _fold_tool(state, event, payload)
    if isinstance(payload, InteractionEvent) and payload.action == "requested" and payload.request is not None:
        state = _replace(
            state, pending_interactions=_upsert_interaction(state.pending_interactions, payload.request)
        )
        if payload.request.kind is InteractionKind.PLAN_APPROVAL:
            state = _replace(state, messages=_mark_plan_for_turn(state.messages, str(payload.request.turn_id)))
        return state
    if isinstance(payload, InteractionEvent) and payload.action == "resolved":
        # The engine emits resolved events carrying only the response (the
        # interaction_id field is never set); fall back to the response's id.
        resolved_id = payload.interaction_id
        if resolved_id is None and payload.response is not None:
            resolved_id = payload.response.interaction_id
        if resolved_id is not None:
            return _replace(
                state,
                pending_interactions=tuple(
                    item for item in state.pending_interactions if item.interaction_id != resolved_id
                ),
            )
        return state
    if isinstance(payload, ChangeEvent) and event.event_type is EventType.WORKSPACE_CHANGED:
        return _replace(state, workspace_revision=payload.revision)
    return state


def _fold_message(state: AppState, event: KernelEvent, payload: MessageEvent) -> AppState:
    if payload.action not in ("delta", "plan_delta", "completed"):
        return state
    if payload.action == "completed":
        record = ChatMessage(
            str(payload.message_id), str(event.session_id or ""), str(event.turn_id or ""),
            event.sequence, MessageRole.ASSISTANT, MessageKind.CHAT, payload.content,
            complete=True, revision=1,
        )
        return _replace(state, messages=_upsert_message(state.messages, record))
    existing = _find_message(state.messages, payload.message_id)
    if existing is None:
        record = ChatMessage(
            str(payload.message_id), str(event.session_id or ""), str(event.turn_id or ""),
            event.sequence, MessageRole.ASSISTANT, MessageKind.CHAT, payload.content,
            revision=1,
        )
        return _replace(state, messages=_upsert_message(state.messages, record))
    appended = ChatMessage(
        existing.message_id, existing.session_id, existing.turn_id, event.sequence,
        existing.role, existing.kind, existing.content + payload.content,
        existing.complete, existing.plan, existing.revision + 1,
    )
    return _replace(state, messages=_upsert_message(state.messages, appended))


def _fold_tool(state: AppState, event: KernelEvent, payload: ToolEvent) -> AppState:
    if payload.invocation is None or payload.action not in ("requested", "started", "output", "completed"):
        return state
    invocation = payload.invocation
    card = _find_tool_card(state.tool_cards, invocation.tool_call_id)
    if card is None:
        card = ToolCard(str(invocation.tool_call_id), str(invocation.session_id), str(invocation.turn_id),
                        invocation.name, invocation.arguments, payload.action, sequence=event.sequence)
    else:
        stage = payload.action
        card = ToolCard(card.tool_call_id, card.session_id, card.turn_id, card.name, card.arguments,
                        stage, card.output + ((payload.output,) if payload.output is not None else ()),
                        payload.result if payload.action == "completed" else card.result,
                        event.sequence)
    return _replace(state, tool_cards=_upsert_tool_card(state.tool_cards, card))


def _mark_plan_for_turn(messages: tuple[ChatMessage, ...], turn_id: str) -> tuple[ChatMessage, ...]:
    indexes = [i for i, m in enumerate(messages)
               if m.turn_id == turn_id and m.role is MessageRole.ASSISTANT and not m.plan]
    if not indexes:
        return messages
    i = indexes[-1]  # the turn's most recent assistant message = the plan
    m = messages[i]
    marked = ChatMessage(m.message_id, m.session_id, m.turn_id, m.sequence, m.role, m.kind,
                         m.content, m.complete, True, m.revision + 1)
    return messages[:i] + (marked,) + messages[i + 1:]


def _push_event(events: tuple[KernelEvent, ...], event: KernelEvent) -> tuple[KernelEvent, ...]:
    return events[-(MAX_EVENT_LOG - 1):] + (event,)


def _fold_turn_active(
    active: tuple[ActiveTurn, ...], event: KernelEvent, payload: TurnEvent
) -> tuple[ActiveTurn, ...]:
    """Keep active_turns event-accurate: non-terminal TURN events upsert the
    turn; terminal events remove it. This is what lets Esc and the exit-wait
    flow react without polling."""
    if event.turn_id is None:
        return active
    if payload.status in TERMINAL_STATUSES:
        return tuple(turn for turn in active if turn.turn_id != event.turn_id)
    session_id = event.session_id or SessionId("")
    for turn in active:
        if turn.turn_id == event.turn_id:
            replacement = ActiveTurn(turn.turn_id, turn.session_id, payload.status, payload.phase, turn.started_at)
            return tuple(replacement if t.turn_id == event.turn_id else t for t in active)
    return active + (ActiveTurn(event.turn_id, session_id, payload.status, payload.phase),)


def _upsert_interaction(
    pending: tuple[InteractionRequest, ...], request: InteractionRequest
) -> tuple[InteractionRequest, ...]:
    return tuple(item for item in pending if item.interaction_id != request.interaction_id) + (request,)


def _replace(state: AppState, **changes: object) -> AppState:
    return AppState(**{**state.__dict__, **changes})


def _find_message(messages: tuple[ChatMessage, ...], message_id: str) -> ChatMessage | None:
    for message in messages:
        if message.message_id == message_id:
            return message
    return None


def _upsert_message(messages: tuple[ChatMessage, ...], record: ChatMessage) -> tuple[ChatMessage, ...]:
    return tuple(message for message in messages if message.message_id != record.message_id) + (record,)


def _find_tool_card(cards: tuple[ToolCard, ...], tool_call_id: str) -> ToolCard | None:
    for card in cards:
        if card.tool_call_id == tool_call_id:
            return card
    return None


def _upsert_tool_card(cards: tuple[ToolCard, ...], card: ToolCard) -> tuple[ToolCard, ...]:
    return tuple(existing for existing in cards if existing.tool_call_id != card.tool_call_id) + (card,)


def _merge_messages(existing: tuple[ChatMessage, ...], recovered: tuple[ChatMessage, ...]) -> tuple[ChatMessage, ...]:
    by_id = {m.message_id: m for m in existing}
    for m in recovered:
        by_id[m.message_id] = m
    return tuple(sorted(by_id.values(), key=lambda m: m.sequence))


class AppStore:
    """Synchronous, UI-thread-safe (single asyncio loop) store."""

    def __init__(self, initial: AppState | None = None) -> None:
        self._state = initial or AppState()
        self._listeners: list[Callable[[AppState], None]] = []

    @property
    def state(self) -> AppState:
        return self._state

    def dispatch(self, action: Action) -> None:
        self._state = reduce(self._state, action)
        for listener in tuple(self._listeners):
            listener(self._state)

    def subscribe(self, listener: Callable[[AppState], None]) -> Callable[[AppState], None]:
        self._listeners.append(listener)
        return listener

    def unsubscribe(self, listener: Callable[[AppState], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)
