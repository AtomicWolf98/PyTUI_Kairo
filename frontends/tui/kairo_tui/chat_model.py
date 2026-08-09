"""Pure chat timeline model: turn store state into ordered, display-ready items.

A Textual-free view-model: the Chat screen maps each TimelineItem to a widget
1:1 and never re-derives the ordering. User bubbles, messages and tool cards
merge by sequence (event order); pending interactions that were not attached
to a card/plan trail at the end because they are current state, not history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from kairo_kernel.contracts.content import (
    AudioBlock,
    ContentBlock,
    FileBlock,
    ImageBlock,
    Message,
    ReasoningBlock,
    ResourceBlock,
    TextBlock,
)
from kairo_kernel.contracts.enums import InteractionKind, MessageRole
from kairo_kernel.contracts.events import InteractionEvent, KernelEvent, MessageEvent, TurnEvent
from kairo_kernel.contracts.interactions import InteractionRequest
from kairo_kernel.contracts.turns import ActiveTurn

from kairo_tui.store import TERMINAL_STATUSES, AppState, ChatMessage, ToolCard


@dataclass(frozen=True)
class TextItem:
    message_id: str
    role: str                    # MessageRole.value ("user"/"assistant")
    text: str
    streaming: bool


@dataclass(frozen=True)
class ReasoningItem:
    message_id: str
    text: str
    streaming: bool
    index: int = 0        # ordinal of this reasoning block within its message


@dataclass(frozen=True)
class ToolItem:
    card: ToolCard
    interaction: InteractionRequest | None = None
    countdown: float | None = None


@dataclass(frozen=True)
class PlanItem:
    message_id: str
    text: str
    streaming: bool
    interaction: InteractionRequest | None = None
    countdown: float | None = None


@dataclass(frozen=True)
class InteractionItem:
    request: InteractionRequest
    countdown: float | None = None


@dataclass(frozen=True)
class MediaItem:
    message_id: str
    index: int
    kind: str                      # "image" | "audio" | "file" | "resource"
    media_type: str
    name: str                      # alt_text / transcript / name / description
    uri: str
    size_bytes: int | None
    sha256: str


@dataclass(frozen=True)
class UserItem:
    turn_id: str
    text: str
    status: str | None           # TurnStatus.value of this turn, for Retry affordance


TimelineItem = TextItem | ReasoningItem | ToolItem | PlanItem | InteractionItem | MediaItem | UserItem


def session_timeline(state: AppState, session_id: str) -> tuple[TimelineItem, ...]:
    """Ordered display items for one session: user/message/tool merged by sequence."""
    items: list[tuple[int, TimelineItem]] = []
    attached: set[str] = set()

    for turn in sorted(
        (t for t in state.user_turns.values() if t.session_id == session_id),
        key=lambda t: t.sequence,
    ):
        items.append((turn.sequence, UserItem(turn.turn_id, turn.text, state.turn_status.get(turn.turn_id))))

    for message in sorted(
        (m for m in state.messages if m.session_id == session_id),
        key=lambda m: m.sequence,
    ):
        if message.plan:
            interaction = _pending_for(state, session_id, message.turn_id, InteractionKind.PLAN_APPROVAL)
            if interaction is not None:
                attached.add(interaction.interaction_id)
            items.append((
                message.sequence,
                PlanItem(
                    message.message_id,
                    _text_of(message.content),
                    not message.complete,
                    interaction,
                    countdown_seconds(interaction.expires_at) if interaction is not None else None,
                ),
            ))
            continue
        text_parts: list[str] = []
        media_index = 0
        reasoning_index = 0
        for block in message.content:
            if isinstance(block, ReasoningBlock):
                items.append((
                    message.sequence,
                    ReasoningItem(message.message_id, block.text, not message.complete, reasoning_index),
                ))
                reasoning_index += 1
            elif isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, (ImageBlock, AudioBlock, FileBlock, ResourceBlock)):
                # Flush pending text first so text/media items follow content order.
                if text_parts:
                    items.append((
                        message.sequence,
                        TextItem(message.message_id, message.role.value, "".join(text_parts), not message.complete),
                    ))
                    text_parts = []
                items.append((message.sequence, _media_item(message.message_id, media_index, block)))
                media_index += 1
        if text_parts:
            items.append((
                message.sequence,
                TextItem(message.message_id, message.role.value, "".join(text_parts), not message.complete),
            ))

    for card in sorted(
        (c for c in state.tool_cards if c.session_id == session_id),
        key=lambda c: c.sequence,
    ):
        interaction = _pending_for(state, session_id, card.turn_id, InteractionKind.TOOL_APPROVAL)
        if interaction is not None:
            attached.add(interaction.interaction_id)
        items.append((
            card.sequence,
            ToolItem(card, interaction, countdown_seconds(interaction.expires_at) if interaction is not None else None),
        ))

    ordered = tuple(item for _, item in sorted(items, key=lambda pair: pair[0]))
    trailing = tuple(
        InteractionItem(request, countdown_seconds(request.expires_at))
        for request in state.pending_interactions
        if str(request.session_id) == session_id and request.interaction_id not in attached
    )
    return ordered + trailing


def last_user_text(state: AppState, session_id: str) -> str:
    """Text of the session's most recent user bubble (by sequence); "" when none."""
    turns = [t for t in state.user_turns.values() if t.session_id == session_id]
    if not turns:
        return ""
    return max(turns, key=lambda t: t.sequence).text


def active_turn_for_session(state: AppState, session_id: str) -> ActiveTurn | None:
    """The session's running turn, or None. First match wins (one turn per session)."""
    for turn in state.active_turns:
        if str(turn.session_id) == session_id:
            return turn
    return None


def history_messages(messages: tuple[Message, ...], session_id: str, base: int = 0) -> tuple[ChatMessage, ...]:
    """History records are committed and complete. User messages are skipped:
    user bubbles come exclusively from user_turns (events never carry them)."""
    records = []
    for offset, message in enumerate(messages):
        if message.role is MessageRole.USER:
            continue
        records.append(ChatMessage(
            str(message.message_id), session_id, "", base + offset + 1,
            message.role, message.kind, message.content, complete=True,
        ))
    return tuple(records)


def should_force_flush(event: KernelEvent) -> bool:
    """True when the folded event marks a boundary the Chat screen must render now.

    Terminal TURN status, MESSAGE `completed` and INTERACTION `resolved` all
    end the current streaming state and must flush the timeline synchronously.
    """
    payload = event.payload
    if isinstance(payload, TurnEvent):
        return payload.status in TERMINAL_STATUSES
    if isinstance(payload, MessageEvent):
        return bool(payload.action == "completed")
    if isinstance(payload, InteractionEvent):
        return bool(payload.action == "resolved")
    return False


def countdown_seconds(expires_at: datetime | None) -> float | None:
    """Display-only seconds until a pending interaction expires (clamped at 0)."""
    if expires_at is None:
        return None
    return max(0.0, (expires_at - datetime.now(timezone.utc)).total_seconds())


def _pending_for(
    state: AppState, session_id: str, turn_id: str, kind: InteractionKind
) -> InteractionRequest | None:
    for request in state.pending_interactions:
        if str(request.session_id) == session_id and str(request.turn_id) == turn_id and request.kind == kind:
            return request
    return None


def _media_item(
    message_id: str, index: int, block: ImageBlock | AudioBlock | FileBlock | ResourceBlock
) -> MediaItem:
    """Map one multimedia content block to its display card item."""
    if isinstance(block, ImageBlock):
        return MediaItem(message_id, index, "image", block.media_type, block.alt_text, block.uri, None, "")
    if isinstance(block, AudioBlock):
        return MediaItem(message_id, index, "audio", block.media_type, block.transcript, block.uri, None, "")
    if isinstance(block, FileBlock):
        return MediaItem(
            message_id, index, "file", block.media_type, block.name, block.uri, block.size_bytes, block.sha256
        )
    return MediaItem(
        message_id, index, "resource", block.media_type, block.name or block.description, block.uri, None, ""
    )


def _text_of(content: tuple[ContentBlock, ...]) -> str:
    return "".join(block.text for block in content if isinstance(block, TextBlock))
