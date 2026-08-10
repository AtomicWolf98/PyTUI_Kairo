"""ChatScreen: end-to-end submit → kernel → pump → store → bubbles.

The app is bootstrapped synchronously (outside any event loop) and driven via
the Pilot inside ``asyncio.run`` — the same pattern as test_app_layout.py and
test_commands.py, because pytest-asyncio's auto-mode loop rejects the nested
``asyncio.run`` inside ``build_running_kernel``.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest
from kairo_kernel.contracts.content import ImageBlock, ReasoningBlock, TextBlock, ToolCallBlock
from kairo_kernel.contracts.enums import (
    EventType,
    InteractionKind,
    MessageKind,
    MessageRole,
    ProviderFailureKind,
    ProviderStreamKind,
    TurnStatus,
)
from kairo_kernel.contracts.events import KernelEvent, MessageEvent
from kairo_kernel.contracts.identifiers import EventId, KernelId, MessageId, SessionId, ToolCallId, TurnId
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.preferences import PreferencesPatch
from kairo_kernel.contracts.providers import ProviderFailure, ProviderRequest, ProviderStreamEvent
from kairo_kernel.ports.control import CancellationToken
from rich.markdown import Markdown
from textual.containers import VerticalScroll
from textual.widgets import Button, Collapsible, Input, Static

from kairo_tui.app import KairoTuiApp
from kairo_tui.bootstrap import BootstrapOptions, build_running_kernel
from kairo_tui.chat_model import history_messages
from kairo_tui.config_document import ConfigDocument, ConfigDocumentAdapter, RoleMapping
from kairo_tui.keyring_store import SecretStore
from kairo_tui.screens.chat import MediaCard, PlanEditModal
from kairo_tui.store import ChatMessage, EventAction, RecoveryAction, SessionAction, SessionsAction
from kairo_tui.widgets import Composer
from tests.support.fakes import NOW_PROFILE, FakeProvider, FakeTool, FakeToolRegistry, GatedProvider


@pytest.fixture
def chat_app_factory(workspace: Path):
    """A booted KairoTuiApp on the Chat page with a seeded config + fake provider."""

    def make(*, provider=None, tools=None, size=(140, 40)):
        document = ConfigDocument(
            profiles=(NOW_PROFILE,),
            roles=(RoleMapping("chat", NOW_PROFILE.profile_id),),
            default_profile_id=NOW_PROFILE.profile_id,
        )
        ConfigDocumentAdapter(workspace.parent / "config-v1.json").save(document)
        bootstrap = build_running_kernel(
            BootstrapOptions(workspace_root=str(workspace), config_path=workspace.parent / "config-v1.json"),
            secret_store=SecretStore(None),
            provider=provider or FakeProvider(),
            tools=tools,
        )
        return KairoTuiApp(bootstrap)
    return make


def _content(text: str) -> ProviderStreamEvent:
    return ProviderStreamEvent(kind=ProviderStreamKind.CONTENT, content=(TextBlock(text),))


def _completed() -> ProviderStreamEvent:
    return ProviderStreamEvent(kind=ProviderStreamKind.COMPLETED)


def _failed() -> ProviderStreamEvent:
    return ProviderStreamEvent(
        kind=ProviderStreamKind.FAILED,
        failure=ProviderFailure(ProviderFailureKind.SERVER, "server exploded", retryable=True),
    )


def _tool_call_script(name: str) -> tuple[ProviderStreamEvent, ...]:
    """A provider round that requests one tool call (ToolCallId == the tool name)."""
    call = ToolCallBlock(ToolCallId(name), name, JsonObject.from_pairs(("path", "README.md")))
    return (
        ProviderStreamEvent(ProviderStreamKind.TOOL_CALL, tool_call=call),
        ProviderStreamEvent(ProviderStreamKind.COMPLETED),
    )


def _tool_then_chat(name: str, final: str = "done") -> tuple[tuple[ProviderStreamEvent, ...], ...]:
    """Tool round followed by a plain chat round (the engine loops after the tool)."""
    return (_tool_call_script(name), (_content(final), _completed()))


class _BlockFirstProvider(FakeProvider):
    """FakeProvider whose first stream blocks until cancelled (session A), while
    later streams (session B) stream normally — stopping A must leave B alone."""

    def __init__(self, *scripts: tuple[ProviderStreamEvent, ...], delay: float = 0.0) -> None:
        super().__init__(*scripts, delay=delay)
        self._first_stream = True

    def stream(self, request: ProviderRequest, cancellation: CancellationToken) -> AsyncIterator[ProviderStreamEvent]:
        self.requests.append(request)
        block = self._first_stream
        self._first_stream = False
        return self._stream(cancellation, block=block)

    async def _stream(self, cancellation: CancellationToken, *, block: bool = False) -> AsyncIterator[ProviderStreamEvent]:
        if block:
            await cancellation.wait()
            return
        if self.delay:
            with suppress(TimeoutError):
                await asyncio.wait_for(cancellation.wait(), timeout=self.delay)
        for event in self.scripts.pop(0) if self.scripts else ():
            await asyncio.sleep(0)
            yield event


class _PacedProvider(FakeProvider):
    """FakeProvider whose script events stream with an inter-event pause, so a
    test can interleave a recovery between deltas of the same message."""

    def __init__(
        self,
        *scripts: tuple[ProviderStreamEvent, ...],
        part_delay: float = 0.0,
        delay: float = 0.0,
        block: bool = False,
    ) -> None:
        super().__init__(*scripts, delay=delay, block=block)
        self.part_delay = part_delay

    async def _stream(self, cancellation: CancellationToken) -> AsyncIterator[ProviderStreamEvent]:
        if self.block:
            await cancellation.wait()
            return
        if self.delay:
            with suppress(TimeoutError):
                await asyncio.wait_for(cancellation.wait(), timeout=self.delay)
        for event in self.scripts.pop(0) if self.scripts else ():
            await asyncio.sleep(self.part_delay)
            yield event


async def _submit_via_composer(pilot, app: KairoTuiApp, text: str) -> None:
    await pilot.click("#composer")
    app.query_one("#composer", Composer).focus()
    await pilot.press(*tuple(text))
    await pilot.press("enter")


def _message_event(sequence: int, text: str) -> KernelEvent:
    return KernelEvent(
        EventId(f"e{sequence}"), KernelId("k1"), sequence, datetime.now(timezone.utc),
        EventType.MESSAGE, MessageEvent(MessageId(f"m{sequence}"), "delta", (TextBlock(text),)),
        turn_id=TurnId("t1"), session_id=SessionId("s1"),
    )


def _delta_event(sequence: int, message_id: str, text: str) -> KernelEvent:
    return KernelEvent(
        EventId(f"e{sequence}"), KernelId("k1"), sequence, datetime.now(timezone.utc),
        EventType.MESSAGE, MessageEvent(MessageId(message_id), "delta", (TextBlock(text),)),
        turn_id=TurnId("t1"), session_id=SessionId("s1"),
    )


def _completed_event(sequence: int, message_id: str, text: str) -> KernelEvent:
    return KernelEvent(
        EventId(f"e{sequence}"), KernelId("k1"), sequence, datetime.now(timezone.utc),
        EventType.MESSAGE, MessageEvent(MessageId(message_id), "completed", (TextBlock(text),)),
        turn_id=TurnId("t1"), session_id=SessionId("s1"),
    )


def _reasoning_event(sequence: int, message_id: str, text: str, action: str) -> KernelEvent:
    return KernelEvent(
        EventId(f"e{sequence}"), KernelId("k1"), sequence, datetime.now(timezone.utc),
        EventType.MESSAGE, MessageEvent(MessageId(message_id), action, (ReasoningBlock(text),)),
        turn_id=TurnId("t1"), session_id=SessionId("s1"),
    )


async def _wait_for(pilot, predicate, *, polls: int = 60, delay: float = 0.05, description: str = "condition") -> None:
    for _ in range(polls):
        await pilot.pause(delay)
        if predicate():
            return
    raise AssertionError(f"timed out waiting for: {description}")


def test_submit_streams_bubbles(chat_app_factory) -> None:
    provider = FakeProvider((_content("Hello "), _content("world"), _completed()))
    app = chat_app_factory(provider=provider)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_via_composer(pilot, app, "hi")
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            await _wait_for(pilot, lambda: timeline.query(".msg-assistant"))
            await pilot.pause(0.1)
            user = timeline.query_one(".msg-user", Static)
            assistant = timeline.query_one(".msg-assistant", Static)
            assert isinstance(user.content, Markdown)
            assert isinstance(assistant.content, Markdown)
            assert user.content.markup == "hi"
            assert assistant.content.markup == "Hello world"

    asyncio.run(drive())


def test_header_shows_session_and_badge(chat_app_factory) -> None:
    provider = FakeProvider((_content("Hello "), _content("world"), _completed()), delay=0.3)
    app = chat_app_factory(provider=provider)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_via_composer(pilot, app, "hi")
            header = app.query_one("#chat-header", Static)
            await _wait_for(pilot, lambda: "running" in str(header.content))
            # The pump only re-reads the session list on a replay gap, so the
            # store never sees create(); seed it the way recovery would.
            summaries = (await app.kernel.sessions.list()).value or ()
            app.store.dispatch(SessionsAction(summaries))
            await pilot.pause()
            assert "Chat" in str(header.content)
            assert "running" in str(header.content)
            await _wait_for(pilot, lambda: "succeeded" in str(header.content))
            assert "succeeded" in str(header.content)

    asyncio.run(drive())


def test_chat_page_mounts_and_switching_pages_preserves_store(chat_app_factory) -> None:
    provider = FakeProvider((_content("Hello world"), _completed()))
    app = chat_app_factory(provider=provider)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_via_composer(pilot, app, "hi")
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            await _wait_for(pilot, lambda: timeline.query(".msg-assistant"))
            await _wait_for(pilot, lambda: not app.store.state.active_turns)
            assert len(timeline.children) == 2
            app.action_page("sessions")
            await pilot.pause()
            assert app.query_one_optional("#chat-screen") is None
            app.action_page("chat")
            await pilot.pause()
            await pilot.pause()
            chat = app.query_one("#chat-screen")
            timeline = chat.query_one("#chat-timeline", VerticalScroll)
            # Rebound from the store: exactly the user + assistant bubbles, no duplicates.
            assert len(timeline.children) == 2
            assert isinstance(timeline.query_one(".msg-user", Static).content, Markdown)
            assert timeline.query_one(".msg-user", Static).content.markup == "hi"
            assert timeline.query_one(".msg-assistant", Static).content.markup == "Hello world"

    asyncio.run(drive())


def test_auto_scroll_to_end(chat_app_factory) -> None:
    app = chat_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.store.dispatch(SessionAction("s1"))
            for index in range(40):
                app.store.dispatch(EventAction(_message_event(index + 1, f"message {index}\nline two")))
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            await _wait_for(pilot, lambda: timeline.children)
            await pilot.pause(0.1)
            await pilot.pause(0.1)
            assert timeline.scroll_y == timeline.max_scroll_y

    asyncio.run(drive())


def test_delta_bursts_coalesce(chat_app_factory) -> None:
    """Ten back-to-back deltas coalesce: one bubble, one message, no lost chunks."""
    app = chat_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.store.dispatch(SessionAction("s1"))
            chunks = [f"chunk {index} " for index in range(10)]
            for index, chunk in enumerate(chunks):
                app.store.dispatch(EventAction(_delta_event(index + 1, "m1", chunk)))
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            # Coalesced: nothing was mounted during the synchronous burst.
            assert not timeline.query(".msg-assistant")
            (message,) = app.store.state.messages
            assert message.complete is False
            await _wait_for(pilot, lambda: timeline.query(".msg-assistant"))
            assert len(timeline.query(".msg-assistant")) == 1
            assistant = timeline.query_one(".msg-assistant", Static)
            assert isinstance(assistant.content, Markdown)
            assert assistant.content.markup == "".join(chunks)

    asyncio.run(drive())


def test_completed_flushes_immediately(chat_app_factory) -> None:
    """A `completed` boundary force-flushes: the final bubble exists right after dispatch."""
    app = chat_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.store.dispatch(SessionAction("s1"))
            app.store.dispatch(EventAction(_delta_event(1, "m1", "Hello ")))
            app.store.dispatch(EventAction(_delta_event(2, "m1", "world")))
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            assert not timeline.query(".msg-assistant")
            app.store.dispatch(EventAction(_completed_event(3, "m1", "Hello world")))
            # Force-flushed during the dispatch: no pilot.pause() needed.
            assistant = timeline.query_one(".msg-assistant", Static)
            assert isinstance(assistant.content, Markdown)
            assert assistant.content.markup == "Hello world"
            assert app.store.state.messages[0].complete is True

    asyncio.run(drive())


def test_reasoning_updates_inside_collapsible(chat_app_factory) -> None:
    """REASONING streams into a Collapsible; `completed` collapses it."""
    app = chat_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.store.dispatch(SessionAction("s1"))
            app.store.dispatch(EventAction(_reasoning_event(1, "m1", "step 1", "delta")))
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            await _wait_for(pilot, lambda: timeline.query(".msg-reasoning"))
            collapsible = timeline.query_one(".msg-reasoning", Collapsible)
            assert collapsible.title == "Reasoning"
            assert collapsible.collapsed is False  # expanded while streaming
            inner = collapsible.query_one(".reasoning-text", Static)
            assert isinstance(inner.content, Markdown)
            assert inner.content.markup == "step 1"
            app.store.dispatch(EventAction(_reasoning_event(2, "m1", "step 1", "completed")))
            collapsible = timeline.query_one(".msg-reasoning", Collapsible)
            assert collapsible.collapsed is True  # collapsed once complete
            inner = collapsible.query_one(".reasoning-text", Static)
            assert isinstance(inner.content, Markdown)
            assert inner.content.markup == "step 1"

    asyncio.run(drive())


def test_two_reasoning_blocks_render_two_collapsibles(chat_app_factory) -> None:
    """The `_key_for` collision regression: one message with two reasoning
    blocks must render two Collapsibles, each with its own content."""
    app = chat_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.store.dispatch(SessionAction("s1"))
            event = KernelEvent(
                EventId("e1"), KernelId("k1"), 1, datetime.now(timezone.utc),
                EventType.MESSAGE,
                MessageEvent(
                    MessageId("m1"), "completed",
                    (ReasoningBlock("step 1"), ReasoningBlock("step 2"), TextBlock("answer")),
                ),
                turn_id=TurnId("t1"), session_id=SessionId("s1"),
            )
            app.store.dispatch(EventAction(event))
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            await _wait_for(pilot, lambda: len(timeline.query(".msg-reasoning")) == 2)
            collapsibles = list(timeline.query(".msg-reasoning"))
            assert len(collapsibles) == 2
            first, second = collapsibles
            assert isinstance(first, Collapsible) and isinstance(second, Collapsible)
            first_text = first.query_one(".reasoning-text", Static)
            second_text = second.query_one(".reasoning-text", Static)
            assert isinstance(first_text.content, Markdown)
            assert isinstance(second_text.content, Markdown)
            assert first_text.content.markup == "step 1"
            assert second_text.content.markup == "step 2"
            # The text bubble still renders alongside the two collapsibles.
            text = timeline.query_one(".msg-assistant", Static)
            assert isinstance(text.content, Markdown)
            assert text.content.markup == "answer"

    asyncio.run(drive())


def test_recovery_rebind_no_duplicates(chat_app_factory) -> None:
    """RecoveryAction rebinds from history: widget count = unique items, no duplicates."""
    app = chat_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.store.dispatch(SessionAction("s1"))
            for index in range(2):
                app.store.dispatch(EventAction(_delta_event(index + 1, f"m{index + 1}", f"part {index + 1} ")))
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            await _wait_for(pilot, lambda: len(timeline.query(".msg-assistant")) == 2)
            history = (
                ChatMessage("m1", "s1", "t1", 3, MessageRole.ASSISTANT, MessageKind.CHAT,
                            (TextBlock("part 1 done"),), complete=True),
                ChatMessage("m2", "s1", "t1", 4, MessageRole.ASSISTANT, MessageKind.CHAT,
                            (TextBlock("part 2 done"),), complete=True),
            )
            app.store.dispatch(RecoveryAction(messages=history, last_event_sequence=4))
            # Rebound from the recovered history: prune settles on the next loop
            # pass, then the timeline holds exactly the unique items — no dupes.
            await _wait_for(pilot, lambda: len(timeline.query(".msg-assistant")) == 2)
            first, second = timeline.query(".msg-assistant")
            assert isinstance(first, Static) and isinstance(second, Static)
            first_content, second_content = first.content, second.content
            assert isinstance(first_content, Markdown) and isinstance(second_content, Markdown)
            assert (first_content.markup, second_content.markup) == ("part 1 done", "part 2 done")
            assert len(app.store.state.messages) == 2
            assert app.store.state.messages_epoch == 1

    asyncio.run(drive())


def test_stop_cancels_running_turn(chat_app_factory) -> None:
    provider = FakeProvider(block=True)
    app = chat_app_factory(provider=provider)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_via_composer(pilot, app, "hi")
            stop = app.query_one("#turn-stop", Button)
            status_text = app.query_one("#turn-status-text", Static)
            await _wait_for(pilot, lambda: not stop.disabled)
            assert "running" in str(status_text.content)
            await pilot.click("#turn-stop")
            await _wait_for(pilot, lambda: "cancelled" in app.store.state.turn_status.values())
            assert stop.disabled
            assert status_text.content == "idle"
            assert app.store.state.active_turns == ()
            retry = app.query_one("#turn-retry", Button)
            assert not retry.disabled  # a cancelled turn can be retried

    asyncio.run(drive())


def test_retry_resubmits_same_text_after_failure(chat_app_factory) -> None:
    provider = FakeProvider((_failed(),), (_content("Hello"), _completed()))
    app = chat_app_factory(provider=provider)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_via_composer(pilot, app, "hi")
            retry = app.query_one("#turn-retry", Button)
            await _wait_for(pilot, lambda: not retry.disabled)
            assert len(provider.requests) == 1
            await pilot.click("#turn-retry")
            await _wait_for(pilot, lambda: len(provider.requests) == 2)
            await _wait_for(pilot, lambda: not app.store.state.active_turns)
            assert "succeeded" in app.store.state.turn_status.values()
            assert len(provider.requests) == 2
            # The second request's first user message carries the original text.
            assert [block.text for block in provider.requests[-1].messages[0].content if isinstance(block, TextBlock)] == ["hi"]

    asyncio.run(drive())


def test_retry_hidden_while_running(chat_app_factory) -> None:
    provider = FakeProvider((_content("Hello"), _completed()), delay=0.3)
    app = chat_app_factory(provider=provider)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_via_composer(pilot, app, "hi")
            retry = app.query_one("#turn-retry", Button)
            await _wait_for(pilot, lambda: app.store.state.active_turns)
            assert retry.disabled  # no retry while a turn is running

    asyncio.run(drive())


def test_ctrl_up_recalls_previous_input(chat_app_factory) -> None:
    app = chat_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_via_composer(pilot, app, "hello")
            composer = app.query_one("#composer", Composer)
            assert composer.text == ""  # cleared after submit
            await pilot.press(*tuple("world"))
            assert composer.text == "world"
            await pilot.press("ctrl+up")
            assert composer.text == "hello"
            await pilot.press("ctrl+up")
            assert composer.text == "hello"  # boundary: no wrap past the oldest entry

    asyncio.run(drive())


def test_ctrl_down_cycles_forward(chat_app_factory) -> None:
    app = chat_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_via_composer(pilot, app, "hello")
            composer = app.query_one("#composer", Composer)
            await pilot.press(*tuple("world"))
            await pilot.press("ctrl+up")
            assert composer.text == "hello"
            await pilot.press("ctrl+down")
            assert composer.text == "world"  # the in-progress draft is restored
            await pilot.press("ctrl+down")
            assert composer.text == ""

    asyncio.run(drive())


def _countdown_seconds(content) -> float:
    """Parse 'expires in 3599.7s' → 3599.7."""
    return float(str(content).split("expires in", 1)[1].strip().rstrip("s"))


async def _set_plan_mode(app: KairoTuiApp) -> None:
    snapshot = await app.kernel.preferences.snapshot()
    result = await app.kernel.preferences.patch(PreferencesPatch(snapshot.revision, plan_mode=True))
    assert result.ok


def test_tool_approval_card_appears_with_buttons_and_countdown(chat_app_factory) -> None:
    """A pending TOOL_APPROVAL mounts the tool card with all four buttons + countdown."""
    provider = FakeProvider(*_tool_then_chat("read_file"))
    tool = FakeTool("read_file")
    app = chat_app_factory(provider=provider, tools=FakeToolRegistry(tool))

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_via_composer(pilot, app, "read the file")
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            await _wait_for(pilot, lambda: timeline.query("#tool-read_file-approve"))
            approve = timeline.query_one("#tool-read_file-approve", Button)
            assert approve.label.plain == "Run once"
            timeline.query_one("#tool-read_file-reject", Button)
            timeline.query_one("#tool-read_file-stop", Button)
            timeline.query_one("#tool-read_file-enable", Button)
            countdown = timeline.query_one(".tool-countdown", Static)
            assert "expires in" in str(countdown.content)
            pending = await app.kernel.interactions.pending()
            assert len(pending) == 1
            assert pending[0].kind is InteractionKind.TOOL_APPROVAL
            assert pending[0].expires_at is not None
            assert pending[0].expires_at > datetime.now(timezone.utc)

    asyncio.run(drive())


def test_approve_once_executes_tool(chat_app_factory) -> None:
    """Approve runs the tool once: the card shows succeeded and the fake recorded the call."""
    provider = FakeProvider(*_tool_then_chat("read_file"))
    tool = FakeTool("read_file")
    app = chat_app_factory(provider=provider, tools=FakeToolRegistry(tool))

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_via_composer(pilot, app, "read the file")
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            await _wait_for(
                pilot,
                lambda: timeline.query("#tool-read_file-approve"),
                polls=200,
                description="tool approval card appears",
            )
            await pilot.click("#tool-read_file-approve")
            await _wait_for(pilot, lambda: tool.calls == 1, polls=200, description="tool executed once")
            text = timeline.query_one(".tool-card-text", Static)
            await _wait_for(pilot, lambda: "succeeded" in str(text.content), polls=200, description="tool card shows succeeded")
            assert tool.calls == 1
            # The pending controls are removed once the interaction is resolved.
            await _wait_for(
                pilot,
                lambda: not timeline.query("#tool-read_file-approve"),
                polls=200,
                description="approval controls removed",
            )

    asyncio.run(drive())


def test_reject_marks_card_and_turn_continues(chat_app_factory) -> None:
    """Reject records a rejected result and the turn keeps going to SUCCEEDED."""
    provider = FakeProvider(*_tool_then_chat("read_file"))
    tool = FakeTool("read_file")
    app = chat_app_factory(provider=provider, tools=FakeToolRegistry(tool))

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_via_composer(pilot, app, "read the file")
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            await _wait_for(pilot, lambda: timeline.query("#tool-read_file-reject"))
            await pilot.click("#tool-read_file-reject")
            text = timeline.query_one(".tool-card-text", Static)
            await _wait_for(pilot, lambda: "rejected" in str(text.content))
            await _wait_for(pilot, lambda: not app.store.state.active_turns)
            assert "succeeded" in app.store.state.turn_status.values()
            assert tool.calls == 0

    asyncio.run(drive())


def test_stop_from_tool_card_cancels_turn(chat_app_factory) -> None:
    """Stop from the tool card cancels the turn (no tool execution)."""
    provider = FakeProvider(_tool_call_script("read_file"))
    tool = FakeTool("read_file")
    app = chat_app_factory(provider=provider, tools=FakeToolRegistry(tool))

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_via_composer(pilot, app, "read the file")
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            await _wait_for(pilot, lambda: timeline.query("#tool-read_file-stop"))
            await pilot.click("#tool-read_file-stop")
            await _wait_for(pilot, lambda: "cancelled" in app.store.state.turn_status.values())
            assert not app.store.state.active_turns
            assert tool.calls == 0

    asyncio.run(drive())


def test_plan_approval_card_and_approve(chat_app_factory) -> None:
    """Plan mode: the plan card appears; approving runs the chat round to SUCCEEDED."""
    provider = FakeProvider((_content("plan text"), _completed()), (_content("executed"), _completed()))
    app = chat_app_factory(provider=provider)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _set_plan_mode(app)
            await _submit_via_composer(pilot, app, "build a feature")
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            await _wait_for(pilot, lambda: timeline.query("#plan-approve"))
            assert timeline.query_one("#plan-approve", Button).label.plain == "Approve and run"
            timeline.query_one("#plan-edit", Button)
            timeline.query_one("#plan-stop", Button)
            await pilot.click("#plan-approve")
            await _wait_for(pilot, lambda: "succeeded" in app.store.state.turn_status.values())
            assert not app.store.state.active_turns
            assistant = timeline.query_one(".msg-assistant", Static)
            assert isinstance(assistant.content, Markdown)
            assert assistant.content.markup == "executed"

    asyncio.run(drive())


def test_plan_edit_submits_modified_instruction(chat_app_factory) -> None:
    """Edit plan opens the modal; submitting responds SUBMIT_TEXT with the revision."""
    provider = FakeProvider((_content("plan text"), _completed()), (_content("executed"), _completed()))
    app = chat_app_factory(provider=provider)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _set_plan_mode(app)
            await _submit_via_composer(pilot, app, "build a feature")
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            await _wait_for(pilot, lambda: timeline.query("#plan-edit"))
            await pilot.click("#plan-edit")
            await _wait_for(pilot, lambda: isinstance(app.screen, PlanEditModal))
            modal = cast(PlanEditModal, app.screen)
            modal.query_one("#plan-edit-input", Input).value = "add tests"
            modal.query_one("#plan-edit-input", Input).focus()
            await pilot.press("enter")
            await _wait_for(pilot, lambda: len(provider.requests) == 2)
            joined = " ".join(
                block.text for message in provider.requests[-1].messages for block in message.content
                if isinstance(block, TextBlock)
            )
            assert "[User Plan Modification]: add tests" in joined
            await _wait_for(pilot, lambda: not app.store.state.active_turns)
            assert "succeeded" in app.store.state.turn_status.values()

    asyncio.run(drive())


def test_countdown_display_only_never_auto_responds(chat_app_factory) -> None:
    """The countdown ticks down but nothing is responded or approved by the TUI."""
    provider = FakeProvider(_tool_call_script("read_file"))
    tool = FakeTool("read_file")
    app = chat_app_factory(provider=provider, tools=FakeToolRegistry(tool))

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_via_composer(pilot, app, "read the file")
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            await _wait_for(
                pilot,
                lambda: timeline.query(".tool-countdown")
                and "expires in" in str(timeline.query_one(".tool-countdown", Static).content),
                polls=200,
                description="countdown card appears",
            )
            countdown = timeline.query_one(".tool-countdown", Static)
            countdown_text = str(countdown.content)
            before = _countdown_seconds(countdown.content)
            assert len(await app.kernel.interactions.pending()) == 1
            # Wait past a 1 s tick for the display-only re-render. The tick
            # updates the SAME widget, so compare against the captured string.
            await _wait_for(
                pilot,
                lambda: str(timeline.query_one(".tool-countdown", Static).content) != countdown_text,
                polls=80,
                delay=0.1,
                description="countdown tick re-rendered",
            )
            after = _countdown_seconds(timeline.query_one(".tool-countdown", Static).content)
            assert after < before
            pending = await app.kernel.interactions.pending()
            assert len(pending) == 1  # still pending: the TUI never responded
            assert tool.calls == 0

    asyncio.run(drive())


def _joined(content) -> str:
    """Concatenated TextBlock text of a message's content tuple."""
    return "".join(block.text for block in content if isinstance(block, TextBlock))


def test_two_sessions_run_in_parallel(chat_app_factory) -> None:
    """Two sessions run turns concurrently; the header shows both tasks; each
    session's timeline shows only its own bubbles."""
    started, release = asyncio.Event(), asyncio.Event()
    provider = GatedProvider(
        (_content("A says hi"), _completed()),
        (_content("B says yo"), _completed()),
        started=started,
        release=release,
    )
    app = chat_app_factory(provider=provider)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            created_a = await app.kernel.sessions.create("Alpha")
            created_b = await app.kernel.sessions.create("Beta")
            assert created_a.ok and created_a.value is not None
            assert created_b.ok and created_b.value is not None
            session_a = str(created_a.value.session_id)
            session_b = str(created_b.value.session_id)
            app.store.dispatch(SessionsAction((await app.kernel.sessions.list()).value or ()))
            app.store.dispatch(SessionAction(session_a))
            await _submit_via_composer(pilot, app, "hi A")
            app.store.dispatch(SessionAction(session_b))
            await _submit_via_composer(pilot, app, "hi B")
            header = app.query_one("#chat-header", Static)
            # Both turns overlap deterministically (gated): 2 concurrent tasks.
            await _wait_for(
                pilot,
                lambda: "(2 tasks)" in str(header.content),
                description="header shows 2 concurrent tasks",
            )
            release.set()
            await _wait_for(
                pilot,
                lambda: sum(1 for status in app.store.state.turn_status.values() if status == "succeeded") == 2,
                description="both turns succeeded",
            )
            assert not app.store.state.active_turns
            # Each session's timeline carries its own assistant bubble.
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            await pilot.click(f"#session-{session_a}")
            await _wait_for(pilot, lambda: len(timeline.query(".msg-assistant")) == 1, description="session A bubble rendered")
            assistant = timeline.query_one(".msg-assistant", Static)
            assert isinstance(assistant.content, Markdown)
            assert assistant.content.markup == "A says hi"
            await pilot.click(f"#session-{session_b}")
            await _wait_for(pilot, lambda: len(timeline.query(".msg-assistant")) == 1, description="session B bubble rendered")
            assistant = timeline.query_one(".msg-assistant", Static)
            assert isinstance(assistant.content, Markdown)
            assert assistant.content.markup == "B says yo"
            assert len(app.store.state.messages) == 2  # one per session, no duplicates

    asyncio.run(drive())


def test_stop_one_turn_other_continues(chat_app_factory) -> None:
    """Stopping session A's turn leaves session B's concurrently running turn
    untouched: B SUCCEEDED while A went CANCELLED."""
    provider = _BlockFirstProvider((_content("B done"), _completed()))
    app = chat_app_factory(provider=provider)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            created_a = await app.kernel.sessions.create("Blocking")
            created_b = await app.kernel.sessions.create("Quick")
            assert created_a.ok and created_a.value is not None
            assert created_b.ok and created_b.value is not None
            session_a = str(created_a.value.session_id)
            session_b = str(created_b.value.session_id)
            app.store.dispatch(SessionsAction((await app.kernel.sessions.list()).value or ()))
            # Session A first: its stream blocks until cancelled.
            app.store.dispatch(SessionAction(session_a))
            await _submit_via_composer(pilot, app, "block me")
            # Session B second: streams normally and completes.
            app.store.dispatch(SessionAction(session_b))
            await _submit_via_composer(pilot, app, "quick one")
            await _wait_for(pilot, lambda: "succeeded" in app.store.state.turn_status.values())
            assert len(app.store.state.active_turns) == 1  # A still runs while B succeeded
            # Switch to A via its header chip, then stop it.
            await pilot.click(f"#session-{session_a}")
            stop = app.query_one("#turn-stop", Button)
            await _wait_for(pilot, lambda: not stop.disabled)
            await pilot.click("#turn-stop")
            await _wait_for(pilot, lambda: "cancelled" in app.store.state.turn_status.values())
            assert not app.store.state.active_turns
            assert set(app.store.state.turn_status.values()) == {"succeeded", "cancelled"}

    asyncio.run(drive())


def test_switching_sessions_does_not_cancel_background_turn(chat_app_factory) -> None:
    """Clicking another session's chip rebinds the timeline without a kernel call:
    session A's turn stays RUNNING across the switch (gate-controlled) and then
    completes after release -- deterministic, no wall-clock delay anywhere."""
    started, release = asyncio.Event(), asyncio.Event()
    provider = GatedProvider(started=started, release=release)
    app = chat_app_factory(provider=provider)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            created_a = await app.kernel.sessions.create("Slow")
            created_b = await app.kernel.sessions.create("Quiet")
            assert created_a.ok and created_a.value is not None
            assert created_b.ok and created_b.value is not None
            session_a = str(created_a.value.session_id)
            session_b = str(created_b.value.session_id)
            app.store.dispatch(SessionsAction((await app.kernel.sessions.list()).value or ()))
            app.store.dispatch(SessionAction(session_a))
            await _submit_via_composer(pilot, app, "slow turn")
            await _wait_for(pilot, lambda: started.is_set(), description="provider stream started")
            await _wait_for(
                pilot,
                lambda: any(str(t.session_id) == session_a for t in app.store.state.active_turns),
                description=f"turn for session {session_a} is active",
            )
            running = next(t for t in app.store.state.active_turns if str(t.session_id) == session_a)
            kernel_active = await app.kernel.active_turns()
            assert any(str(turn.turn_id) == str(running.turn_id) for turn in kernel_active)
            # Switch to B via its header chip -- no kernel call, turn unaffected.
            await pilot.click(f"#session-{session_b}")
            assert app.store.state.active_session_id == session_b
            assert any(str(t.session_id) == session_a for t in app.store.state.active_turns)
            # The chip strip is rebuilt asynchronously (remove + remount), so the
            # highlighted chip must be re-queried on every poll -- a reference
            # captured before the rebuild is a detached widget that never changes.
            def chip_b_primary() -> bool:
                chip = app.query_one_optional(f"#session-{session_b}")
                return chip is not None and chip.variant == "primary"

            await _wait_for(pilot, chip_b_primary, description="session B chip highlighted")
            # No kernel.cancel was ever called.
            assert "cancelled" not in app.store.state.turn_status.values()
            # Release the gate: A's turn completes on its own.
            release.set()
            await _wait_for(
                pilot,
                lambda: app.store.state.turn_status.get(str(running.turn_id)) == TurnStatus.SUCCEEDED.value,
                description=f"turn {running.turn_id} reached succeeded",
            )
            assert not app.store.state.active_turns
            assert (await app.kernel.active_turns()) == ()

    asyncio.run(drive())


def test_gap_mid_stream_timeline_catches_up_without_duplication(chat_app_factory) -> None:
    """A recovery mid-stream re-reads the committed subset; the remaining deltas
    of the in-flight message then arrive — every message renders exactly once."""
    provider = _PacedProvider(
        (_content("first "), _content("turn"), _completed()),
        (_content("part 1 "), _content("part 2"), _completed()),
        part_delay=0.3,
    )
    app = chat_app_factory(provider=provider)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_via_composer(pilot, app, "first")
            await _wait_for(pilot, lambda: app.store.state.messages and app.store.state.messages[-1].complete)
            await _submit_via_composer(pilot, app, "second")
            # Wait until the in-flight turn's first delta has been folded.
            await _wait_for(
                pilot,
                lambda: any(not m.complete and "part 1" in _joined(m.content) for m in app.store.state.messages),
            )
            session_id = app.store.state.active_session_id
            assert session_id is not None
            # Recovery mid-stream: the committed subset (turn 1's message) is
            # re-read; the in-flight message is not committed yet.
            history = (await app.kernel.conversations.history(SessionId(session_id))).value or ()
            app.store.dispatch(RecoveryAction(
                messages=history_messages(history, session_id),
                last_event_sequence=app.store.state.last_event_sequence,
            ))
            # The remaining deltas arrive after the recovery rebind.
            await _wait_for(pilot, lambda: all(m.complete for m in app.store.state.messages))
            messages = app.store.state.messages
            assert len(messages) == 2
            assert len({m.message_id for m in messages}) == 2  # deduped: each id once
            assert sorted(_joined(m.content) for m in messages) == ["first turn", "part 1 part 2"]
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            await _wait_for(pilot, lambda: len(timeline.query(".msg-assistant")) == 2)
            bubbles = list(timeline.query(".msg-assistant"))
            assert len(bubbles) == 2  # no duplicated bubbles after recovery
            contents = sorted(str(b.content.markup) for b in bubbles if isinstance(b.content, Markdown))
            assert contents == ["first turn", "part 1 part 2"]

    asyncio.run(drive())


def _media_completed_event(sequence: int, message_id: str, block) -> KernelEvent:
    return KernelEvent(
        EventId(f"e{sequence}"), KernelId("k1"), sequence, datetime.now(timezone.utc),
        EventType.MESSAGE, MessageEvent(MessageId(message_id), "completed", (block,)),
        turn_id=TurnId("t1"), session_id=SessionId("s1"),
    )


def test_image_block_renders_media_card_without_auto_open(chat_app_factory, monkeypatch) -> None:
    """Rendering an ImageBlock shows a metadata card (type + size); nothing auto-opens."""
    opens: list[str] = []
    monkeypatch.setattr("kairo_tui.screens.chat.open_media", lambda path: opens.append(str(path)))
    payload = b"fake-png-bytes-1234"
    block = ImageBlock(media_type="image/png", base64_data=base64.b64encode(payload).decode(), alt_text="photo")
    app = chat_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.store.dispatch(SessionAction("s1"))
            app.store.dispatch(EventAction(_media_completed_event(1, "m1", block)))
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            await _wait_for(pilot, lambda: timeline.query(".msg-media"))
            card = timeline.query_one(".msg-media", MediaCard)
            rendered = str(card.query_one(".media-text", Static).content)
            assert "image/png" in rendered
            assert f"{len(payload)} B" in rendered
            card.query_one("#media-m1-0-save", Button)
            card.query_one("#media-m1-0-open", Button)
            assert opens == []  # the seam was never called: no auto-open on render

    asyncio.run(drive())


def test_media_save_writes_base64_to_kairo_media(chat_app_factory, workspace) -> None:
    """Save decodes the base64 payload and writes the bytes under <workspace>/kairo_media/."""
    payload = b"fake-png-bytes-1234"
    block = ImageBlock(media_type="image/png", base64_data=base64.b64encode(payload).decode(), alt_text="photo")
    app = chat_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.store.dispatch(SessionAction("s1"))
            app.store.dispatch(EventAction(_media_completed_event(1, "m1", block)))
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            await _wait_for(pilot, lambda: timeline.query("#media-m1-0-save"))
            await pilot.click("#media-m1-0-save")
            saved = workspace / "kairo_media" / "m1-0-photo"
            await _wait_for(pilot, lambda: saved.exists())
            assert saved.read_bytes() == payload

    asyncio.run(drive())


def test_media_open_after_save_calls_seam_with_saved_path(chat_app_factory, workspace, monkeypatch) -> None:
    """Open is inert without a local file; after Save it calls open_media with the saved path."""
    opens: list[str] = []
    monkeypatch.setattr("kairo_tui.screens.chat.open_media", lambda path: opens.append(str(path)))
    payload = b"fake-png-bytes-1234"
    block = ImageBlock(media_type="image/png", base64_data=base64.b64encode(payload).decode(), alt_text="photo")
    app = chat_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.store.dispatch(SessionAction("s1"))
            app.store.dispatch(EventAction(_media_completed_event(1, "m1", block)))
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            await _wait_for(pilot, lambda: timeline.query("#media-m1-0-open"))
            # No local file yet: Open must not launch anything.
            await pilot.click("#media-m1-0-open")
            assert opens == []
            await pilot.click("#media-m1-0-save")
            saved = workspace / "kairo_media" / "m1-0-photo"
            await _wait_for(pilot, lambda: saved.exists())
            await pilot.click("#media-m1-0-open")
            await _wait_for(pilot, lambda: opens)
            assert opens == [str(saved)]

    asyncio.run(drive())
