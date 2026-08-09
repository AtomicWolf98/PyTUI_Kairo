"""Chat page: session timeline with Markdown bubbles and a status header.

The screen is store-driven: every store dispatch flows through ``_on_store``,
which either force-flushes (terminal boundary events) or marks the timeline
dirty for the 30 FPS flush timer. Recovery rebinds drop every mounted widget
and re-render from the store, so a session switch or replay-gap rebuild never
duplicates bubbles.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TypeAlias, cast

from kairo_kernel.contracts.content import (
    AudioBlock,
    FileBlock,
    ImageBlock,
    ResourceBlock,
    TextBlock,
)
from kairo_kernel.contracts.enums import InteractionAction
from kairo_kernel.contracts.interactions import InteractionRequest, InteractionResponse
from rich.markdown import Markdown
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Button, Collapsible, Input, Static

from kairo_tui.chat_model import (
    InteractionItem,
    MediaItem,
    PlanItem,
    ReasoningItem,
    TextItem,
    TimelineItem,
    ToolItem,
    UserItem,
    active_turn_for_session,
    countdown_seconds,
    last_user_text,
    session_timeline,
    should_force_flush,
)
from kairo_tui.store import AppState, SessionAction, UserTurnAction

MediaBlock: TypeAlias = ImageBlock | AudioBlock | FileBlock | ResourceBlock


def open_media(path: str) -> None:
    """Open ``path`` with the OS default application.

    Test seam: the UI only calls this after the user presses a media card's
    Open button and a local file exists; nothing auto-opens on render.
    """
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path])


def _sanitize_media_name(name: str, fallback: str) -> str:
    """Traversal-free basename for a saved media file (no separators, no ``..``)."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in base)
    while ".." in safe:
        safe = safe.replace("..", "_")
    return safe.strip(" .") or fallback


class TurnStatusBar(Container):
    """Active turn status (status · phase) plus Stop/Retry controls.

    Rendered from the store on every dispatch; Stop cancels the foreground
    session's running turn, Retry resubmits its last user input after a
    failed or cancelled turn.
    """

    def compose(self) -> ComposeResult:
        yield Static(id="turn-status-text")
        yield Button("Stop", id="turn-stop", variant="error", disabled=True)
        yield Button("Retry", id="turn-retry", variant="primary", disabled=True)

    def render_status(self, state: AppState) -> None:
        session_id = state.active_session_id
        turn = active_turn_for_session(state, session_id) if session_id is not None else None
        text = "idle"
        if turn is not None:
            text = f"{turn.status.value}" + (f" · {turn.phase.value}" if turn.phase else "")
        self.query_one("#turn-status-text", Static).update(text)
        self.query_one("#turn-stop", Button).disabled = turn is None
        last_status = state.turn_status.get(self._last_turn_id(state), "")
        self.query_one("#turn-retry", Button).disabled = last_status not in ("failed", "cancelled")

    def _last_turn_id(self, state: AppState) -> str:
        """Turn id of the active session's most recent user turn; "" when none."""
        session_id = state.active_session_id
        if session_id is None:
            return ""
        latest: tuple[int, str] | None = None
        for turn in state.user_turns.values():
            if turn.session_id != session_id:
                continue
            if latest is None or turn.sequence > latest[0]:
                latest = (turn.sequence, turn.turn_id)
        return latest[1] if latest is not None else ""


class PlanEditModal(ModalScreen[str]):
    """One-input modal for revising the plan instruction before approving it."""

    def compose(self) -> ComposeResult:
        with Vertical(id="plan-edit-modal"):
            yield Static("Edit the plan instructions, then submit:")
            yield Input(id="plan-edit-input", placeholder="Revised instructions…")
            yield Button("Submit", id="plan-edit-submit", variant="primary")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "plan-edit-submit":
            self.dismiss(self.query_one("#plan-edit-input", Input).value)


class ToolCardWidget(Container):
    """Tool-call card: name (stage), arguments, output lines and result status.

    While a ``TOOL_APPROVAL`` is pending for the card's turn it also mounts
    approve/reject/stop/enable buttons (ids ``tool-{tool_call_id}-{action}``)
    plus a display-only countdown. ``render_item`` re-renders on every flush so
    the stage/output stay event-accurate; a 1 s self-interval ticks the
    countdown (text only — nothing is ever responded on expiry).
    """

    DEFAULT_CSS = """
    ToolCardWidget {
        height: auto;
        border: round $primary;
        padding: 0 1;
    }
    ToolCardWidget .tool-card-actions {
        height: auto;
    }
    """

    def __init__(self, kernel, item: ToolItem) -> None:
        super().__init__(classes="msg-tool")
        self._kernel = kernel
        self._item = item
        # Always start "not built": the first render flips to the real state and
        # mounts (or clears) the action buttons.
        self._pending = False
        self._tick: Timer | None = None

    @property
    def interaction(self) -> InteractionRequest | None:
        return self._item.interaction

    def compose(self) -> ComposeResult:
        yield Static(classes="tool-card-text")
        yield Static(classes="tool-countdown")
        yield Container(classes="tool-card-actions")

    def on_mount(self) -> None:
        self._tick = self.set_interval(1.0, self._on_tick)
        self.render_item(self._item)

    def on_unmount(self) -> None:
        if self._tick is not None:
            self._tick.stop()

    def _on_tick(self) -> None:
        # Display-only countdown: re-render, never respond.
        if self._item.interaction is not None:
            self.render_item(self._item)

    def render_item(self, item: ToolItem) -> None:
        self._item = item
        text = self.query_one_optional(".tool-card-text", Static)
        if text is None:
            # Compose children are not attached yet (mount is async); the next
            # flush/on_mount render fills them in.
            return
        card = item.card
        lines = [f"[b]{card.name}[/b] ({card.stage})"]
        if card.arguments:
            lines.append(str(card.arguments))
        for chunk in card.output:
            for block in chunk.content:
                if isinstance(block, TextBlock):
                    lines.append(block.text)
        if card.result is not None:
            lines.append(f"[{card.result.status.value}]")
        text.update("\n".join(lines))
        interaction = item.interaction
        if interaction is not None:
            remaining = countdown_seconds(interaction.expires_at)
            self.query_one(".tool-countdown", Static).update(
                f"expires in {remaining:g}s" if remaining is not None else ""
            )
        else:
            self.query_one(".tool-countdown", Static).update("")
        pending = interaction is not None
        if pending != self._pending:
            self._pending = pending
            self._rebuild_actions(pending)

    def _rebuild_actions(self, pending: bool) -> None:
        actions = self.query_one(".tool-card-actions", Container)
        actions.remove_children()
        if not pending:
            return
        tool_call_id = self._item.card.tool_call_id
        actions.mount(
            Button("Run once", id=f"tool-{tool_call_id}-approve", variant="primary"),
            Button("Reject", id=f"tool-{tool_call_id}-reject"),
            Button("Stop task", id=f"tool-{tool_call_id}-stop", variant="error"),
            Button("Enable broader", id=f"tool-{tool_call_id}-enable"),
        )


class PlanCardWidget(Container):
    """Plan bubble: the plan markdown plus, while pending, approve/edit/cancel.

    The Edit button opens ``PlanEditModal`` and responds ``SUBMIT_TEXT`` with
    the entered text; approve/cancel are routed by the ChatScreen.
    """

    DEFAULT_CSS = """
    PlanCardWidget {
        height: auto;
        border: round $secondary;
        padding: 0 1;
    }
    PlanCardWidget .plan-card-actions {
        height: auto;
    }
    """

    def __init__(self, app, kernel, item: PlanItem) -> None:
        super().__init__(classes="msg-plan")
        self._app = app
        self._kernel = kernel
        self._item = item
        # Always start "not built": the first render flips to the real state and
        # mounts (or clears) the action buttons.
        self._pending = False

    @property
    def interaction(self) -> InteractionRequest | None:
        return self._item.interaction

    def compose(self) -> ComposeResult:
        yield Static(Markdown(self._item.text), classes="plan-text")
        yield Container(classes="plan-card-actions")

    def on_mount(self) -> None:
        self.render_item(self._item)

    def render_item(self, item: PlanItem) -> None:
        self._item = item
        text = self.query_one_optional(".plan-text", Static)
        if text is None:
            return
        text.update(Markdown(item.text))
        pending = item.interaction is not None
        if pending != self._pending:
            self._pending = pending
            self._rebuild_actions(pending)

    def _rebuild_actions(self, pending: bool) -> None:
        actions = self.query_one(".plan-card-actions", Container)
        actions.remove_children()
        if not pending:
            return
        actions.mount(
            Button("Approve and run", id="plan-approve", variant="primary"),
            Button("Edit plan", id="plan-edit"),
            Button("Cancel", id="plan-stop", variant="error"),
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "plan-edit":
            event.stop()
            self.run_worker(self._edit_plan())

    async def _edit_plan(self) -> None:
        request = self._item.interaction
        if request is None:
            return
        text = await self._app.push_screen_wait(PlanEditModal())
        if text:
            await self._respond(request, InteractionAction.SUBMIT_TEXT, text)

    async def _respond(self, request: InteractionRequest, action: InteractionAction, text: str = "") -> None:
        await self._kernel.interactions.respond(
            InteractionResponse(request.interaction_id, request.turn_id, action, text)
        )


class InteractionCardWidget(Container):
    """Pending ``TEXT_INPUT`` interaction: prompt + Input + Submit.

    Submit (button or Enter) responds ``SUBMIT_TEXT`` with the entered text.
    """

    DEFAULT_CSS = """
    InteractionCardWidget {
        height: auto;
        border: round $accent;
        padding: 0 1;
    }
    """

    def __init__(self, kernel, item: InteractionItem) -> None:
        super().__init__(classes="msg-interaction")
        self._kernel = kernel
        self._item = item

    def compose(self) -> ComposeResult:
        request = self._item.request
        yield Static(request.prompt, classes="interaction-prompt")
        yield Input(placeholder="Type a response…", id=f"interaction-{request.interaction_id}-input")
        yield Button("Submit", id=f"interaction-{request.interaction_id}-submit", variant="primary")

    def render_item(self, item: InteractionItem) -> None:
        self._item = item
        prompt = self.query_one_optional(".interaction-prompt", Static)
        if prompt is not None:
            prompt.update(item.request.prompt)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == f"interaction-{self._item.request.interaction_id}-submit":
            self.run_worker(self._submit_text())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.run_worker(self._submit_text())

    async def _submit_text(self) -> None:
        request = self._item.request
        text = self.query_one(f"#interaction-{request.interaction_id}-input", Input).value
        if text.strip():
            await self._kernel.interactions.respond(
                InteractionResponse(request.interaction_id, request.turn_id, InteractionAction.SUBMIT_TEXT, text)
            )


class MediaCard(Container):
    """Metadata card for an ImageBlock/AudioBlock/FileBlock/ResourceBlock.

    Shows kind/name/type/size only — raw bytes are never rendered and nothing
    auto-opens on render. ``Save`` decodes a base64 payload or copies a local
    ``uri`` into the workspace ``kairo_media/`` directory; ``Open`` calls the
    ``open_media`` seam only after the user presses the button and only when a
    local file exists.
    """

    DEFAULT_CSS = """
    MediaCard {
        height: auto;
        border: round $accent;
        padding: 0 1;
    }
    MediaCard .media-actions {
        height: auto;
    }
    """

    def __init__(self, app, item: MediaItem, block: MediaBlock | None) -> None:
        super().__init__(classes="msg-media")
        self._app = app
        self._item = item
        self._block = block

    def compose(self) -> ComposeResult:
        yield Static(self._card_text(), classes="media-text")
        yield Horizontal(
            Button("Save", id=f"media-{self._item.message_id}-{self._item.index}-save"),
            Button("Open", id=f"media-{self._item.message_id}-{self._item.index}-open"),
            classes="media-actions",
        )

    def on_mount(self) -> None:
        self.render_item(self._item)

    def render_item(self, item: MediaItem) -> None:
        self._item = item
        text = self.query_one_optional(".media-text", Static)
        if text is not None:
            text.update(self._card_text())

    def _card_text(self) -> str:
        item = self._item
        size = item.size_bytes
        if size is None and isinstance(self._block, (ImageBlock, AudioBlock)) and self._block.base64_data:
            try:
                size = len(base64.b64decode(self._block.base64_data))
            except ValueError:
                size = None
        meta = f"({item.media_type}" + (f", {size} B" if size is not None else "") + ")"
        return f"[b]{item.kind}[/b] {item.name or item.kind} {meta}"

    def _media_target(self) -> Path | None:
        """Canonical kairo_media path for this card; None when it escapes the workspace."""
        root = self._app.store.state.workspace_root
        if not root:
            return None
        media_dir = Path(root) / "kairo_media"
        name = _sanitize_media_name(self._item.name, self._item.kind)
        target = media_dir / f"{self._item.message_id}-{self._item.index}-{name}"
        try:
            if target.resolve().parent != media_dir.resolve():
                return None
        except OSError:
            return None
        return target

    async def media_save(self) -> None:
        """Persist the media payload into the workspace kairo_media/ directory."""
        block = self._block
        if block is None:
            self._app.notify("No local payload to save.")
            return
        if isinstance(block, ResourceBlock):
            self._app.notify("Resource is not locally saveable.")
            return
        target = self._media_target()
        if target is None:
            self._app.notify("Save rejected: unsafe filename.")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if isinstance(block, FileBlock):
                if not block.uri:
                    self._app.notify("File has no local payload to save.")
                    return
                shutil.copy2(block.uri, target)
            elif block.base64_data:
                target.write_bytes(base64.b64decode(block.base64_data))
            elif block.uri:
                shutil.copy2(block.uri, target)
            else:
                self._app.notify("No local payload to save.")
                return
        except (OSError, ValueError) as exc:
            self._app.notify(f"Save failed: {exc}")
            return
        self._app.notify(str(target))

    async def media_open(self) -> None:
        """Open a local file with the OS opener; inert until one exists."""
        path = self._local_path()
        if path is None:
            self._app.notify("No local file to open.")
            return
        open_media(str(path))

    def _local_path(self) -> Path | None:
        target = self._media_target()
        if target is not None and target.exists():
            return target
        block = self._block
        if block is not None and block.uri:
            source = Path(block.uri)
            if source.is_file():
                return source
        return None


class ChatScreen(Container):
    """Message timeline for the active session, rendered from the AppStore."""

    DEFAULT_CSS = """
    ChatScreen {
        height: 1fr;
    }
    #chat-timeline {
        height: 1fr;
    }
    #session-strip {
        height: auto;
        padding: 0 0 1 0;
    }
    .session-chip {
        margin: 0 1 0 0;
    }
    """

    def __init__(self, app) -> None:
        super().__init__(id="chat-screen")
        # Widget.app is a read-only Textual property, so the host app reference
        # lives at _app (same convention as SetupScreen).
        self._app = app
        self.kernel = app.kernel
        self.store = app.store
        self._dirty = False
        self._last_epoch = -1
        self._flush_timer: Timer
        self._widgets: dict[str, Widget] = {}
        self._chips_signature: tuple[tuple[str, ...], str | None, frozenset[str]] | None = None
        self._chips_dirty = False
        self._chips_building = False

    def compose(self) -> ComposeResult:
        yield Static(id="chat-header")
        yield Horizontal(id="session-strip")
        yield TurnStatusBar(id="turn-status-bar")
        yield VerticalScroll(id="chat-timeline")

    def on_mount(self) -> None:
        self._flush_timer = self.set_interval(1 / 30, self._flush)
        self.store.subscribe(self._on_store)
        self._render_header()
        self._render_status()
        self._rebind()

    def on_unmount(self) -> None:
        self.store.unsubscribe(self._on_store)
        self._flush_timer.stop()

    def _on_store(self, state: AppState) -> None:
        if state.messages_epoch != self._last_epoch:
            self._last_epoch = state.messages_epoch
            self._rebind()
            return
        newest = state.events[-1] if state.events else None
        if newest is not None and should_force_flush(newest):
            self._dirty = False
            self._render_timeline()
        else:
            self._dirty = True
        self._render_header()
        self._render_status()

    def _flush(self) -> None:
        if self._chips_dirty:
            self.run_worker(self._rebuild_chips())
        if self._dirty:
            self._dirty = False
            self._render_timeline()

    def _rebind(self) -> None:
        """Full rebuild: recovery rebind or session switch. Drop and re-mount every widget."""
        self._widgets.clear()
        timeline = self.query_one("#chat-timeline", VerticalScroll)
        timeline.remove_children()
        self._render_timeline()

    def _render_header(self) -> None:
        state = self.store.state
        session_id = state.active_session_id
        name = next((s.name for s in state.sessions if str(s.session_id) == session_id), session_id or "—")
        turn = active_turn_for_session(state, session_id) if session_id is not None else None
        if turn is not None:
            badge = f"[b]{turn.status.value}[/b] {turn.phase.value if turn.phase else ''}".strip()
        else:
            # No running turn: show the session's last terminal status (the
            # running turn's SUCCEEDED/FAILED/CANCELLED), idle when it never ran.
            badge = self._last_turn_status(state, session_id) or "idle"
        concurrent = len(state.active_turns)
        self.query_one("#chat-header", Static).update(f"{name} — {badge}" + (f"  ({concurrent} tasks)" if concurrent > 1 else ""))
        self._render_session_chips(state, session_id)

    def _render_session_chips(self, state: AppState, active_session_id: str | None) -> None:
        """Track the chip signature; the 30 FPS flush runs the rebuild worker.

        mount/remove are async in Textual (AwaitMount/AwaitRemove), so mutating
        the strip inside a store dispatch would queue overlapping inserts and
        clash on duplicate ids. The worker awaits each DOM op and the dirty flag
        coalesces rapid signature changes.
        """
        running = frozenset(str(turn.session_id) for turn in state.active_turns)
        signature = (tuple(str(s.session_id) for s in state.sessions), active_session_id, running)
        if signature == self._chips_signature:
            return
        self._chips_signature = signature
        self._chips_dirty = True

    async def _rebuild_chips(self) -> None:
        """(Re)build the session-chip strip; the active chip is highlighted and a
        running session carries a ● badge. Serialized: a build in flight skips a
        re-entry and the dirty flag re-runs the loop until the store is reflected."""
        if self._chips_building:
            return
        self._chips_building = True
        try:
            while self._chips_dirty:
                self._chips_dirty = False
                state = self.store.state
                active = state.active_session_id
                running = frozenset(str(turn.session_id) for turn in state.active_turns)
                strip = self.query_one("#session-strip", Horizontal)
                await strip.remove_children()
                for summary in state.sessions:
                    sid = str(summary.session_id)
                    label = f"● {summary.name}" if sid in running else summary.name
                    await strip.mount(Button(
                        label,
                        id=f"session-{sid}",
                        classes="session-chip",
                        variant="primary" if sid == active else "default",
                    ))
        finally:
            self._chips_building = False

    def _render_status(self) -> None:
        self.query_one("#turn-status-bar", TurnStatusBar).render_status(self.store.state)

    def _last_turn_status(self, state: AppState, session_id: str | None) -> str:
        """Status of the session's most recent user turn; "" when it has none."""
        if session_id is None:
            return ""
        latest: tuple[int, str] | None = None
        for turn in state.user_turns.values():
            if turn.session_id != session_id:
                continue
            if latest is None or turn.sequence > latest[0]:
                latest = (turn.sequence, turn.turn_id)
        if latest is None:
            return ""
        return state.turn_status.get(latest[1], "")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "turn-stop":
            self.run_worker(self._stop_turn())
        elif button_id == "turn-retry":
            self.run_worker(self._retry_turn())
        elif button_id.startswith("session-"):
            # Session switch is purely a store/rebind operation: no kernel call,
            # so background turns of other sessions keep running.
            session_id = button_id[len("session-"):]
            self.store.dispatch(SessionAction(session_id))
            self._rebind()
        elif button_id.startswith("tool-"):
            self._route_tool_card_button(event.button, button_id)
        elif button_id.startswith("plan-"):
            self._route_plan_card_button(event.button, button_id)
        elif button_id.startswith("media-"):
            self._route_media_card_button(event.button, button_id)

    def _route_media_card_button(self, button: Button, button_id: str) -> None:
        """media-{message_id}-{index}-{save|open} → the owning MediaCard's handler."""
        action = button_id.rsplit("-", 1)[-1]
        if action not in ("save", "open"):
            return
        for widget in button.ancestors:
            if isinstance(widget, MediaCard):
                self.run_worker(widget.media_save() if action == "save" else widget.media_open())
                return

    def _route_tool_card_button(self, button: Button, button_id: str) -> None:
        """tool-{tool_call_id}-{approve|reject|stop|enable} → respond on the card's request."""
        action_name = button_id.rsplit("-", 1)[-1]
        action = {
            "approve": InteractionAction.APPROVE_ONCE,
            "reject": InteractionAction.REJECT,
            "stop": InteractionAction.STOP,
            "enable": InteractionAction.ENABLE_AUTO,
        }.get(action_name)
        if action is None:
            return
        request = self._card_interaction(button, ToolCardWidget)
        if request is not None:
            self.run_worker(self._respond(request, action))

    def _route_plan_card_button(self, button: Button, button_id: str) -> None:
        """plan-{approve|stop} → respond on the plan card's request (edit is handled by the card)."""
        action_name = button_id.rsplit("-", 1)[-1]
        action = (
            InteractionAction.APPROVE_ONCE if action_name == "approve"
            else InteractionAction.STOP if action_name == "stop"
            else None
        )
        if action is None:
            return
        request = self._card_interaction(button, PlanCardWidget)
        if request is not None:
            self.run_worker(self._respond(request, action))

    def _card_interaction(self, button: Button, card_type: type[ToolCardWidget] | type[PlanCardWidget]) -> InteractionRequest | None:
        """The pending interaction carried by the card that owns ``button``."""
        for widget in button.ancestors:
            if isinstance(widget, card_type):
                return widget.interaction
        return None

    async def _respond(self, request: InteractionRequest, action: InteractionAction, text: str = "") -> None:
        await self.kernel.interactions.respond(
            InteractionResponse(request.interaction_id, request.turn_id, action, text)
        )

    async def _stop_turn(self) -> None:
        session_id = self.store.state.active_session_id
        turn = active_turn_for_session(self.store.state, session_id) if session_id is not None else None
        if turn is None:
            return
        await self.kernel.cancel(turn.turn_id, "User pressed Stop.")

    async def _retry_turn(self) -> None:
        from kairo_kernel.contracts.identifiers import SessionId
        from kairo_kernel.contracts.turns import TurnRequest

        session_id = self.store.state.active_session_id
        if session_id is None:
            return
        text = last_user_text(self.store.state, session_id)
        if not text:
            return
        accepted = await self.kernel.submit(TurnRequest(text, session_id=SessionId(session_id)))
        if accepted.ok and accepted.value is not None:
            self.store.dispatch(UserTurnAction(session_id, str(accepted.value.turn_id), text))

    def _render_timeline(self) -> None:
        state = self.store.state
        session_id = state.active_session_id
        if session_id is None:
            return
        timeline = self.query_one("#chat-timeline", VerticalScroll)
        seen: set[str] = set()
        for item in session_timeline(state, session_id):
            key = self._key_for(item)
            seen.add(key)
            widget = self._widgets.get(key)
            expected = self._widget_type(item)
            if widget is None or not isinstance(widget, expected):
                # Rebuild when the item changed shape (e.g. a streaming message
                # that becomes a plan card) or the widget is unknown.
                if widget is not None:
                    self._widgets.pop(key).remove()
                widget = self._make_widget(item)
                self._widgets[key] = widget
                timeline.mount(widget)
            else:
                self._update_widget(widget, item)
        # Prune widgets whose items no longer exist (e.g. a resolved trailing
        # interaction card): resolution removes the card from the timeline.
        for key in tuple(self._widgets):
            if key not in seen:
                self._widgets.pop(key).remove()
        # Auto-follow the newest message. The session-change gate alone is not
        # enough: the SessionAction that activates a session triggers a rebind
        # on an empty timeline, which would consume the scroll before any
        # content mounts (leaving the view at the top). Scrolling on every
        # render keeps the bottom pinned; a later gate skips this while the
        # user has scrolled up.
        timeline.scroll_end(animate=False)

    @staticmethod
    def _widget_type(item: TimelineItem) -> type[Widget]:
        match item:
            case TextItem() | UserItem():
                return Static
            case ReasoningItem():
                return Collapsible
            case ToolItem():
                return ToolCardWidget
            case PlanItem():
                return PlanCardWidget
            case InteractionItem():
                return InteractionCardWidget
            case MediaItem():
                return MediaCard

    def _key_for(self, item: TimelineItem) -> str:
        match item:
            case TextItem() | PlanItem():
                return item.message_id
            case ReasoningItem():
                # The block index disambiguates multiple reasoning blocks that
                # share a message_id, so each renders its own Collapsible.
                return f"{item.message_id}#{item.index}"
            case ToolItem():
                return item.card.tool_call_id
            case InteractionItem():
                return str(item.request.interaction_id)
            case MediaItem():
                return f"media-{item.message_id}-{item.index}"
            case UserItem():
                return item.turn_id

    def _make_widget(self, item: TimelineItem) -> Widget:
        match item:
            case TextItem():
                return Static(Markdown(item.text), classes=f"msg-{item.role}")
            case UserItem():
                return Static(Markdown(item.text), classes="msg-user")
            case ReasoningItem():
                return Collapsible(
                    Static(Markdown(item.text), classes="reasoning-text"),
                    title="Reasoning",
                    collapsed=not item.streaming,
                    classes="msg-reasoning",
                )
            case ToolItem():
                return ToolCardWidget(self.kernel, item)
            case PlanItem():
                return PlanCardWidget(self._app, self.kernel, item)
            case InteractionItem():
                return InteractionCardWidget(self.kernel, item)
            case MediaItem():
                return MediaCard(self._app, item, self._media_block(item))

    def _media_block(self, item: MediaItem) -> MediaBlock | None:
        """The content block a MediaItem was derived from (its index-th media block)."""
        for message in self.store.state.messages:
            if message.message_id != item.message_id:
                continue
            media = [block for block in message.content
                     if isinstance(block, (ImageBlock, AudioBlock, FileBlock, ResourceBlock))]
            if item.index < len(media):
                return media[item.index]
        return None

    def _update_widget(self, widget: Widget, item: TimelineItem) -> None:
        match item:
            case TextItem() | UserItem():
                cast(Static, widget).update(Markdown(item.text))
            case ReasoningItem():
                collapsible = cast(Collapsible, widget)
                collapsible.collapsed = not item.streaming
                collapsible.query_one(".reasoning-text", Static).update(Markdown(item.text))
            case ToolItem():
                cast(ToolCardWidget, widget).render_item(item)
            case PlanItem():
                cast(PlanCardWidget, widget).render_item(item)
            case InteractionItem():
                cast(InteractionCardWidget, widget).render_item(item)
            case MediaItem():
                cast(MediaCard, widget).render_item(item)
