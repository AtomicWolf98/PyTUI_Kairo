"""Pure chat timeline model: store state -> ordered, display-ready items."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kairo_kernel.contracts.content import (
    AudioBlock,
    FileBlock,
    ImageBlock,
    ReasoningBlock,
    ResourceBlock,
    TextBlock,
    ToolCallBlock,
)
from kairo_kernel.contracts.enums import (
    EventType,
    InteractionAction,
    InteractionKind,
    MessageKind,
    MessageRole,
    TurnStatus,
)
from kairo_kernel.contracts.events import (
    InteractionEvent,
    KernelEvent,
    MessageEvent,
    TurnEvent,
)
from kairo_kernel.contracts.identifiers import (
    EventId,
    InteractionId,
    KernelId,
    MessageId,
    ResourceId,
    SessionId,
    ToolCallId,
    TurnId,
)
from kairo_kernel.contracts.interactions import InteractionChoice, InteractionRequest
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.turns import ActiveTurn

from kairo_tui.chat_model import (
    InteractionItem,
    MediaItem,
    PlanItem,
    ReasoningItem,
    TextItem,
    ToolItem,
    UserItem,
    active_turn_for_session,
    last_user_text,
    session_timeline,
    should_force_flush,
)
from kairo_tui.store import AppState, ChatMessage, ToolCard, UserTurn

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


def _interaction(
    session: str,
    turn: str,
    kind: InteractionKind,
    interaction_id: str | None = None,
    expires: datetime | None = None,
) -> InteractionRequest:
    return InteractionRequest(
        InteractionId(interaction_id or f"i-{session}-{turn}-{kind.value}"),
        TurnId(turn),
        SessionId(session),
        kind,
        f"{kind.value} prompt",
        (InteractionChoice(InteractionAction.STOP, "Stop"),),
        expires,
        InteractionAction.STOP,
    )


def _message(
    message_id: str,
    session: str,
    turn: str,
    sequence: int,
    blocks,
    *,
    complete: bool = False,
    plan: bool = False,
) -> ChatMessage:
    return ChatMessage(
        message_id, session, turn, sequence, MessageRole.ASSISTANT, MessageKind.CHAT,
        blocks, complete=complete, plan=plan,
    )


def test_timeline_filters_by_session() -> None:
    state = AppState(
        user_turns={
            "t1": UserTurn("s1", "t1", "hello", 1),
            "t2": UserTurn("s2", "t2", "other", 2),
        },
        messages=(
            _message("m1", "s1", "t1", 3, (TextBlock("hi"),), complete=True),
            _message("m2", "s2", "t2", 4, (TextBlock("bye"),), complete=True),
        ),
        tool_cards=(
            ToolCard("tc1", "s1", "t1", "read_file", JsonObject(), "completed", sequence=5),
            ToolCard("tc2", "s2", "t2", "write_file", JsonObject(), "completed", sequence=6),
        ),
        active_turns=(ActiveTurn(TurnId("t1"), SessionId("s1"), TurnStatus.RUNNING),),
    )
    timeline = session_timeline(state, "s1")
    assert [type(item).__name__ for item in timeline] == ["UserItem", "TextItem", "ToolItem"]
    (user,) = [item for item in timeline if isinstance(item, UserItem)]
    assert (user.turn_id, user.text, user.status) == ("t1", "hello", None)
    (text,) = [item for item in timeline if isinstance(item, TextItem)]
    assert text.message_id == "m1"
    (card,) = [item for item in timeline if isinstance(item, ToolItem)]
    assert card.card.tool_call_id == "tc1"
    assert active_turn_for_session(state, "s1") == state.active_turns[0]
    assert active_turn_for_session(state, "s2") is None


def test_text_blocks_concatenate_in_order() -> None:
    state = AppState(
        messages=(_message("m1", "s1", "t1", 2, (TextBlock("Hello, "), TextBlock("world"), TextBlock("!"))),)
    )
    (item,) = session_timeline(state, "s1")
    assert isinstance(item, TextItem)
    assert item.text == "Hello, world!"
    assert item.role == "assistant"
    assert item.streaming is True  # incomplete message still streaming

    done = AppState(
        messages=(_message("m1", "s1", "t1", 2, (TextBlock("Hello, "), TextBlock("world")), complete=True),)
    )
    (item,) = session_timeline(done, "s1")
    assert isinstance(item, TextItem)
    assert item.text == "Hello, world"
    assert item.streaming is False


def test_reasoning_blocks_become_reasoning_items() -> None:
    state = AppState(
        messages=(
            _message(
                "m1", "s1", "t1", 2,
                (ReasoningBlock("step 1"), TextBlock("answer"), ReasoningBlock("step 2")),
                complete=True,
            ),
        )
    )
    timeline = session_timeline(state, "s1")
    reasoning = [item for item in timeline if isinstance(item, ReasoningItem)]
    assert [item.text for item in reasoning] == ["step 1", "step 2"]
    assert all(item.message_id == "m1" for item in reasoning)
    assert all(item.streaming is False for item in reasoning)
    (text,) = [item for item in timeline if isinstance(item, TextItem)]
    assert text.text == "answer"


def test_tool_call_blocks_do_not_generate_items() -> None:
    block = ToolCallBlock(ToolCallId("tc1"), "read_file", JsonObject.from_pairs(("path", "a.txt")))
    state = AppState(
        messages=(_message("m1", "s1", "t1", 2, (block,), complete=True),)
    )
    # assistant content only produces text/reasoning/plan; tool cards come from tool_cards
    assert session_timeline(state, "s1") == ()


def test_tool_card_attaches_pending_approval() -> None:
    expires = datetime.now(timezone.utc) + timedelta(seconds=30)
    state = AppState(
        tool_cards=(ToolCard("tc1", "s1", "t1", "read_file", JsonObject(), "requested", sequence=2),),
        pending_interactions=(_interaction("s1", "t1", InteractionKind.TOOL_APPROVAL, expires=expires),),
    )
    (item,) = session_timeline(state, "s1")
    assert isinstance(item, ToolItem)
    assert item.card.tool_call_id == "tc1"
    assert item.interaction is not None
    assert item.interaction.kind is InteractionKind.TOOL_APPROVAL
    assert item.countdown is not None
    assert 0.0 <= item.countdown <= 30.0


def test_plan_message_renders_plan_item() -> None:
    expires = datetime.now(timezone.utc) + timedelta(seconds=60)
    state = AppState(
        messages=(_message("m1", "s1", "t1", 4, (TextBlock("Step 1"), TextBlock("\nStep 2")), plan=True),),
        pending_interactions=(_interaction("s1", "t1", InteractionKind.PLAN_APPROVAL, expires=expires),),
    )
    (item,) = session_timeline(state, "s1")
    assert isinstance(item, PlanItem)
    assert item.message_id == "m1"
    assert item.text == "Step 1\nStep 2"
    assert item.streaming is True
    assert item.interaction is not None
    assert item.interaction.kind is InteractionKind.PLAN_APPROVAL
    assert item.countdown is not None
    assert 0.0 <= item.countdown <= 60.0


def test_unattached_pending_interaction_appends_interaction_item() -> None:
    state = AppState(
        pending_interactions=(_interaction("s1", "t1", InteractionKind.TEXT_INPUT),),
        turn_status={"t1": "waiting_input"},
    )
    (item,) = session_timeline(state, "s1")
    assert isinstance(item, InteractionItem)
    assert item.request.kind is InteractionKind.TEXT_INPUT
    assert item.countdown is None
    # a PLAN_APPROVAL whose plan message was lost (post-recovery) also trails
    lost = AppState(pending_interactions=(_interaction("s1", "t9", InteractionKind.PLAN_APPROVAL),))
    (item,) = session_timeline(lost, "s1")
    assert isinstance(item, InteractionItem)
    assert item.request.kind is InteractionKind.PLAN_APPROVAL


def test_last_user_text_returns_latest() -> None:
    state = AppState(
        user_turns={
            "t1": UserTurn("s1", "t1", "first", 1),
            "t2": UserTurn("s1", "t2", "second", 3),
        }
    )
    assert last_user_text(state, "s1") == "second"
    assert last_user_text(AppState(), "s1") == ""
    other = AppState(user_turns={"t9": UserTurn("s2", "t9", "other", 5)})
    assert last_user_text(other, "s1") == ""


def test_should_force_flush_matrix() -> None:
    assert should_force_flush(_event(1, EventType.TURN, TurnEvent(TurnStatus.SUCCEEDED, None)))
    assert should_force_flush(_event(2, EventType.TURN, TurnEvent(TurnStatus.CANCELLED, None)))
    assert should_force_flush(_event(3, EventType.TURN, TurnEvent(TurnStatus.FAILED, None)))
    assert should_force_flush(
        _event(4, EventType.MESSAGE, MessageEvent(MessageId("m1"), "completed", (TextBlock("x"),)))
    )
    assert should_force_flush(
        _event(5, EventType.INTERACTION, InteractionEvent("resolved", interaction_id=InteractionId("i1")))
    )
    assert not should_force_flush(_event(6, EventType.TURN, TurnEvent(TurnStatus.RUNNING, None)))
    assert not should_force_flush(
        _event(7, EventType.MESSAGE, MessageEvent(MessageId("m1"), "delta", (TextBlock("x"),)))
    )
    assert not should_force_flush(
        _event(8, EventType.MESSAGE, MessageEvent(MessageId("m1"), "plan_delta", (TextBlock("x"),)))
    )
    assert not should_force_flush(
        _event(9, EventType.INTERACTION, InteractionEvent("requested", request=_interaction("s1", "t1", InteractionKind.TOOL_APPROVAL)))
    )


def test_should_force_flush_terminal_turn_true() -> None:
    """A TURN event with a terminal status is a flush boundary."""
    for status in (TurnStatus.SUCCEEDED, TurnStatus.CANCELLED, TurnStatus.FAILED):
        assert should_force_flush(_event(1, EventType.TURN, TurnEvent(status, None)))


def test_should_force_flush_message_delta_false() -> None:
    """MESSAGE deltas coalesce: only the `completed` action flushes."""
    event = _event(1, EventType.MESSAGE, MessageEvent(MessageId("m1"), "delta", (TextBlock("x"),)))
    assert not should_force_flush(event)


def test_should_force_flush_interaction_requested_false() -> None:
    """INTERACTION `requested` coalesces: only the `resolved` action flushes."""
    event = _event(
        1, EventType.INTERACTION,
        InteractionEvent("requested", request=_interaction("s1", "t1", InteractionKind.TOOL_APPROVAL)),
    )
    assert not should_force_flush(event)


def test_reasoning_items_carry_per_message_index() -> None:
    """Each reasoning block within a message carries its own ordinal index
    (the key the Chat screen uses to render one Collapsible per block)."""
    state = AppState(
        messages=(
            _message(
                "m1", "s1", "t1", 2,
                (ReasoningBlock("step 1"), TextBlock("a"), ReasoningBlock("step 2"),
                 ReasoningBlock("step 3")),
                complete=True,
            ),
            _message("m2", "s1", "t1", 3, (ReasoningBlock("only"),), complete=True),
        )
    )
    timeline = session_timeline(state, "s1")
    reasoning = [item for item in timeline if isinstance(item, ReasoningItem)]
    assert [(item.message_id, item.index) for item in reasoning] == [
        ("m1", 0), ("m1", 1), ("m1", 2), ("m2", 0),
    ]


def test_media_blocks_become_media_items_with_kind_and_metadata() -> None:
    """Image/audio/file/resource blocks each emit a MediaItem with matching metadata."""
    blocks = (
        ImageBlock(media_type="image/png", base64_data="AA==", alt_text="chart"),
        AudioBlock(media_type="audio/mpeg", uri="/tmp/voice.mp3", transcript="spoken notes"),
        FileBlock(name="report.pdf", media_type="application/pdf", uri="/tmp/report.pdf",
                  size_bytes=2048, sha256="deadbeef"),
        ResourceBlock(ResourceId("r1"), "mcp://docs/reference", name="the reference",
                      description="docs", media_type="text/markdown"),
        ResourceBlock(ResourceId("r2"), "mcp://docs/other", description="plain description"),
    )
    state = AppState(
        messages=(_message("m1", "s1", "t1", 2, blocks, complete=True),)
    )
    media = [item for item in session_timeline(state, "s1") if isinstance(item, MediaItem)]
    assert [item.kind for item in media] == ["image", "audio", "file", "resource", "resource"]
    assert [item.message_id for item in media] == ["m1"] * 5
    image, audio, file, resource, fallback = media
    assert (image.media_type, image.name, image.uri, image.size_bytes, image.sha256) == (
        "image/png", "chart", "", None, ""
    )
    assert (audio.media_type, audio.name, audio.uri, audio.size_bytes, audio.sha256) == (
        "audio/mpeg", "spoken notes", "/tmp/voice.mp3", None, ""
    )
    assert (file.media_type, file.name, file.uri, file.size_bytes, file.sha256) == (
        "application/pdf", "report.pdf", "/tmp/report.pdf", 2048, "deadbeef"
    )
    assert (resource.media_type, resource.name, resource.uri, resource.size_bytes, resource.sha256) == (
        "text/markdown", "the reference", "mcp://docs/reference", None, ""
    )
    # Resource name falls back to the description when the name is empty.
    assert fallback.name == "plain description"


def test_media_items_carry_per_message_index() -> None:
    """The media counter is per message: each media block carries its ordinal."""
    state = AppState(
        messages=(
            _message("m1", "s1", "t1", 2,
                     (ImageBlock(media_type="image/png", alt_text="a"),
                      ImageBlock(media_type="image/png", alt_text="b")), complete=True),
            _message("m2", "s1", "t1", 3, (ImageBlock(media_type="image/png", alt_text="c"),), complete=True),
        )
    )
    media = [item for item in session_timeline(state, "s1") if isinstance(item, MediaItem)]
    assert [(item.message_id, item.index) for item in media] == [("m1", 0), ("m1", 1), ("m2", 0)]


def test_mixed_text_and_media_message_items_in_order() -> None:
    """A (text, image) message yields TextItem then MediaItem; reversed content flips the order."""
    state = AppState(
        messages=(_message(
            "m1", "s1", "t1", 2,
            (TextBlock("hi"), ImageBlock(media_type="image/png", alt_text="pic")), complete=True),)
    )
    timeline = session_timeline(state, "s1")
    assert [type(item).__name__ for item in timeline] == ["TextItem", "MediaItem"]
    (text,) = [item for item in timeline if isinstance(item, TextItem)]
    assert text.text == "hi"
    (media,) = [item for item in timeline if isinstance(item, MediaItem)]
    assert media.kind == "image"
    reversed_state = AppState(
        messages=(_message(
            "m2", "s1", "t1", 2,
            (ImageBlock(media_type="image/png", alt_text="pic"), TextBlock("bye")), complete=True),)
    )
    assert [type(item).__name__ for item in session_timeline(reversed_state, "s1")] == ["MediaItem", "TextItem"]
