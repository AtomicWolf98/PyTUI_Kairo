import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

from rich.markdown import Markdown
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import Static, TextArea

from agent.commands import build_help_markdown, CommandDispatcher, get_command_map
from agent.core import Agent
from agent.ui.events import AgentEvent, EventConsole
from agent.ui.mascot import KaiMascot
from agent.ui.widgets import (
    BrandHeader,
    ChoiceModal,
    CommandPalette,
    Composer,
    ConversationView,
    StatusDock,
    TextPromptModal,
    WorkspaceFileSelected,
    WorkspaceModal,
    WorkspacePanel,
    WorkspaceTree,
)
from agent.workspace import WorkspaceMonitor, WorkspaceSnapshot
from agent.workspace_context import WorkspaceContext


COMMANDS: Dict[str, str] = get_command_map()


class KairoApp(App):
    TITLE = "Kairo"
    SUB_TITLE = "Terminal Agent"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        ("ctrl+b", "workspace", "Workspace"),
        ("ctrl+a", "toggle_auth", "Auth"),
        ("ctrl+p", "toggle_plan", "Plan"),
        ("ctrl+t", "toggle_think", "Think"),
    ]

    CSS = """
    $bg: #101214;
    $surface: #181b1f;
    $surface-2: #20242a;
    $text: #f5f7fa;
    $muted: #7f849c;
    $cyan: #66d9ef;
    $amber: #f6c177;
    $mint: #8bd5ca;
    $coral: #ed8796;

    Screen {
        background: $bg;
        color: $text;
    }

    #brand-header {
        height: 7;
        padding: 1 2;
        background: $bg;
        border-bottom: solid #2a2f36;
    }

    #header-kai {
        width: 13;
        height: 5;
        margin-right: 2;
        text-style: bold;
    }

    #brand-meta {
        width: 1fr;
        height: 5;
        padding-top: 0;
    }

    #workspace {
        height: 1fr;
        layout: horizontal;
    }

    #chat-column {
        width: 1fr;
        height: 100%;
    }

    #conversation {
        width: 100%;
        height: 1fr;
        padding: 1 2;
        scrollbar-color: #3a414b;
        scrollbar-background: $bg;
    }

    .message {
        width: 100%;
        height: auto;
        margin-bottom: 1;
        padding: 0 1;
        overflow-x: auto;
    }

    .user-message {
        border-left: thick $cyan;
        background: #151a1e;
    }

    .assistant-message {
        border-left: thick #3a414b;
    }

    .thought-message {
        color: #a5adcb;
        border-left: thick $amber;
        padding-left: 2;
    }

    .collapsed-thought {
        max-height: 5;
        overflow: hidden;
    }

    .notice-message {
        color: $muted;
        border-left: thick #3a414b;
    }

    .tool-message {
        color: #c6a0f6;
        border-left: thick #c6a0f6;
        background: #17151d;
    }

    .error-message {
        color: $coral;
        border-left: thick $coral;
    }

    .success-message {
        color: $mint;
        border-left: thick $mint;
    }

    .hidden {
        display: none;
    }

    #suggestions {
        display: none;
        height: auto;
        max-height: 7;
        margin: 0 2;
        color: #a5adcb;
        background: $surface;
        border-left: thick $cyan;
    }

    #suggestions.visible {
        display: block;
    }

    #suggestions > ListItem {
        height: 1;
        padding: 0 1;
    }

    #suggestions > ListItem.--highlight {
        background: #29323a;
        color: $text;
    }

    #composer-wrap {
        height: auto;
        min-height: 5;
        max-height: 12;
        padding: 0 2 1 2;
        background: $bg;
    }

    #prompt-label {
        width: 9;
        height: 3;
        color: $cyan;
        text-style: bold;
        padding-top: 1;
    }

    #composer {
        width: 1fr;
        height: 3;
        min-height: 3;
        max-height: 10;
        background: $surface;
        border: solid #343b44;
        padding: 0 1;
    }

    #composer:focus {
        border: solid $cyan;
    }

    #status-dock {
        width: 33%;
        min-width: 36;
        max-width: 64;
        height: 100%;
        padding: 1;
        background: $surface;
        border-left: solid #2a2f36;
    }

    #dock-workspace {
        width: 100%;
        height: 1fr;
        min-height: 12;
    }

    .workspace-heading {
        height: 1;
        color: $muted;
        text-style: bold;
    }

    #workspace-tree {
        width: 100%;
        height: 35%;
        min-height: 3;
        background: $surface;
        scrollbar-size: 1 1;
    }

    #changed-files {
        width: 100%;
        height: 4;
        min-height: 2;
        background: $surface;
        scrollbar-size: 1 1;
    }

    #changed-files > ListItem {
        height: 1;
    }

    #changed-files > ListItem.--highlight {
        background: #29323a;
    }

    #diff-viewer {
        width: 100%;
        height: 1fr;
        min-height: 2;
        border: solid #343b44;
        scrollbar-size: 1 1;
    }

    #diff-content {
        width: auto;
        height: auto;
    }

    #workspace-note {
        height: auto;
        max-height: 2;
        color: $amber;
    }

    #dock-status-footer {
        width: 100%;
        height: auto;
        max-height: 8;
        padding-top: 0;
        border-top: solid #343b44;
    }

    #dock-state {
        height: 1;
        color: $amber;
        text-style: bold;
        text-align: left;
    }

    #dock-model, #dock-session, #dock-context, #dock-usage, #dock-modes {
        height: auto;
        color: #a5adcb;
    }

    #context-bar {
        height: 1;
        margin-top: 0;
    }

    .context-normal Bar > .bar--bar { color: $mint; }
    .context-warning Bar > .bar--bar { color: $amber; }
    .context-danger Bar > .bar--bar { color: $coral; }

    #workspace-modal-shell {
        width: 92%;
        height: 90%;
        padding: 1 2;
        background: $surface;
        border: solid #3a414b;
    }

    #workspace-modal-title {
        height: 2;
        color: $cyan;
        text-style: bold;
    }

    #modal-workspace {
        height: 1fr;
        width: 100%;
    }

    .narrow #workspace {
        layout: vertical;
    }

    .narrow #chat-column {
        height: 1fr;
        width: 100%;
    }

    .narrow #status-dock {
        width: 100%;
        min-width: 0;
        max-width: 100%;
        height: 5;
        padding: 0 2;
        border-left: none;
        border-top: solid #2a2f36;
        layout: horizontal;
    }

    .narrow #dock-workspace, .narrow #dock-model,
    .narrow #dock-session, .narrow #dock-usage,
    .narrow #context-bar {
        display: none;
    }

    .narrow #dock-status-footer {
        height: 4;
        max-height: 4;
        padding: 0;
        border-top: none;
        layout: horizontal;
    }

    .narrow #dock-state {
        width: 14;
        height: 3;
        padding-top: 1;
        text-align: left;
    }

    .narrow #dock-context {
        width: 1fr;
        height: 3;
        padding-top: 1;
        margin: 0;
    }

    .narrow #dock-modes {
        width: auto;
        height: 3;
        padding-top: 1;
        margin: 0;
    }

    ChoiceModal, TextPromptModal, WorkspaceModal {
        align: center middle;
        background: #000000 55%;
    }

    #choice-title, #prompt-title {
        width: 64;
        height: 3;
        padding: 1 2;
        background: $surface-2;
        color: $cyan;
        text-style: bold;
        border: solid #3a414b;
    }

    #choice-list {
        width: 64;
        height: auto;
        max-height: 16;
        background: $surface;
        border: solid #3a414b;
    }

    #prompt-input {
        width: 64;
        background: $surface;
        border: solid $cyan;
    }
    """

    def __init__(self, config, registry, *, animation: bool = True, reduced_motion: bool = False):
        super().__init__()
        self.config = config
        self.registry = registry
        self.animation = animation
        self.reduced_motion = reduced_motion or not animation
        self.workspace_context = WorkspaceContext(
            config.workspace_root,
            allow_absolute_outside=config.policy.get("workspace_path", {}).get("allow_absolute_outside", False),
        )
        self.agent = Agent(
            config,
            registry,
            console=EventConsole(self.emit_from_worker),
            workspace_context=self.workspace_context,
        )
        self.agent.workspace_changed = self._on_workspace_changed
        self.registry.set_output_callback(lambda chunk: self.emit_from_worker("tool_output", chunk))
        self.busy = False
        self.current_state = "idle"
        self.input_history: List[str] = []
        self.history_index = 0
        self.command_matches: List[str] = []
        self.workspace_monitor = self._make_workspace_monitor()
        self.workspace_snapshot = WorkspaceSnapshot(root=str(self.workspace_monitor.root))
        self.workspace_selected_file = ""
        self.workspace_active_tool = ""
        self.workspace_scan_running = False
        self.workspace_scan_pending = False
        self.workspace_generation = 0
        self.workspace_modal = None
        self._main_screen = None
        self._ui_thread_id = None
        self._delta_lock = threading.Lock()
        self._delta_buffers = {"content_delta": "", "thought_delta": ""}
        self._delta_flush_scheduled = False

    def compose(self) -> ComposeResult:
        yield BrandHeader(
            self.config.model,
            self.config.active_model_profile,
            str(self.workspace_monitor.root),
            reduced_motion=self.reduced_motion,
            id="brand-header",
        )
        with Horizontal(id="workspace"):
            with Vertical(id="chat-column"):
                yield ConversationView(id="conversation")
                yield CommandPalette(COMMANDS, id="suggestions")
                with Horizontal(id="composer-wrap"):
                    yield Static("kairo >", id="prompt-label")
                    yield Composer(
                        id="composer",
                        placeholder="Ask Kairo anything...",
                        compact=True,
                        show_line_numbers=False,
                    )
            yield StatusDock(reduced_motion=self.reduced_motion, id="status-dock")

    def main_query(self, selector, expect_type=None):
        """Query the default screen even while a modal is active."""
        screen = self._main_screen or self.screen
        return screen.query_one(selector, expect_type)

    def on_mount(self):
        self._ui_thread_id = threading.get_ident()
        self._main_screen = self.screen
        self._apply_responsive_layout(self.size.width)
        if not self.config.ui.get("mascot", True):
            for mascot in self.query(KaiMascot):
                mascot.display = False
        self.main_query("#composer", Composer).focus()
        self.refresh_dock()
        if self.config.ui.get("workspace_enabled", True):
            self.request_workspace_refresh()
            self.set_interval(
                max(0.5, float(self.config.ui.get("workspace_refresh_seconds", 2.0))),
                self.request_workspace_refresh,
            )
        if self.animation and not self.reduced_motion:
            self.set_kai_state("connecting")
            self.set_timer(0.8, lambda: self.set_kai_state("idle"))
        else:
            self.set_kai_state("idle")

    def on_resize(self, event: events.Resize):
        self._apply_responsive_layout(event.size.width)

    def _apply_responsive_layout(self, width: int):
        breakpoint = int(self.config.ui.get("dock_breakpoint", 120))
        narrow = width < breakpoint
        main_screen = self._main_screen or self.screen
        main_screen.set_class(narrow, "narrow")
        dock = main_screen.query_one("#status-dock", StatusDock)
        if narrow:
            dock.styles.width = "100%"
            return
        ratio = float(self.config.ui.get("dock_width_ratio", 0.333))
        minimum = int(self.config.ui.get("dock_min_width", 36))
        maximum = int(self.config.ui.get("dock_max_width", 64))
        dock.styles.width = max(minimum, min(maximum, round(width * ratio)))

    def set_kai_state(self, state: str):
        self.current_state = state
        root = self._main_screen or self.screen
        for mascot in root.query(KaiMascot):
            mascot.set_state(state)
        self.refresh_dock()
        if state in ("success", "error") and not self.reduced_motion:
            self.set_timer(0.8, lambda: self.set_kai_state("idle") if not self.busy else None)

    def refresh_dock(self):
        if not self.is_mounted:
            return
        tracker = self.agent.token_tracker
        level = self.config.authorization_level.upper()
        level_style = {
            "MANUAL": "white",
            "AUTO": "yellow",
            "YOLO": "red",
        }.get(level, "white")
        modes = (
            f"[{level_style}]{level}[/{level_style}]  "
            f"Think {'ON' if self.config.thinking_mode else 'OFF'}  "
            f"Plan {'ON' if self.config.plan_mode else 'OFF'}"
        )
        task = self.agent.current_task if self.agent.current_task != "Idle" else "Ready"
        self.main_query("#status-dock", StatusDock).update_status(
            state=self.current_state,
            model=self.config.model,
            profile=self.config.active_model_profile,
            session=self.agent.active_session_name,
            context_used=tracker.context_used_tokens,
            context_limit=tracker.context_window,
            context_trigger=float(self.config.context_management["trigger_percent"]),
            input_tokens=tracker.session_input_tokens,
            output_tokens=tracker.session_output_tokens,
            modes=modes,
            task=task,
            active_file=self.workspace_snapshot.active_file,
            active_tool=self.workspace_active_tool,
        )

    def emit_from_worker(self, kind: str, payload: Any = None):
        on_ui_thread = threading.get_ident() == self._ui_thread_id
        if kind in self._delta_buffers:
            with self._delta_lock:
                self._delta_buffers[kind] += str(payload)
                if self._delta_flush_scheduled:
                    return
                self._delta_flush_scheduled = True
            if on_ui_thread:
                self.set_timer(1 / 30, self._flush_deltas)
            else:
                self.call_from_thread(self.set_timer, 1 / 30, self._flush_deltas)
            return
        event = AgentEvent(kind, payload)
        if on_ui_thread:
            self.post_message(event)
        else:
            self.call_from_thread(self.post_message, event)

    def _flush_deltas(self):
        buffered = self._take_deltas()
        for kind, value in buffered.items():
            if value:
                self.post_message(AgentEvent(kind, value))

    def _take_deltas(self):
        with self._delta_lock:
            buffered = dict(self._delta_buffers)
            self._delta_buffers = {"content_delta": "", "thought_delta": ""}
            self._delta_flush_scheduled = False
        return buffered

    async def on_agent_event(self, event: AgentEvent):
        kind, payload = event.kind, event.payload
        if kind == "workspace_snapshot":
            if isinstance(payload, tuple):
                generation, snapshot = payload
                if generation != self.workspace_generation:
                    # Stale worker result; ignore.
                    return
            else:
                # Backwards-compatible path for empty snapshots posted directly.
                snapshot = payload
            self.workspace_snapshot = snapshot
            self.workspace_selected_file = snapshot.selected_file
            try:
                await self.main_query("#status-dock", StatusDock).update_workspace(snapshot)
            except NoMatches:
                return
            if self.workspace_modal and self.workspace_modal.is_mounted:
                await self.workspace_modal.query_one(WorkspacePanel).update_snapshot(snapshot)
            self.refresh_dock()
            return
        view = self.main_query("#conversation", ConversationView)
        if kind == "state":
            self.set_kai_state(str(payload))
        elif kind == "message_started":
            await view.start_assistant()
        elif kind == "content_delta":
            view.append_content(str(payload))
        elif kind == "thought_delta":
            view.append_thought(str(payload))
        elif kind == "message_finished":
            buffered = self._take_deltas()
            if buffered["thought_delta"]:
                view.append_thought(buffered["thought_delta"])
            if buffered["content_delta"]:
                view.append_content(buffered["content_delta"])
            view.finish_assistant()
        elif kind == "console":
            await view.add_notice(payload)
        elif kind == "notice":
            await view.add_notice(Text(str(payload), style="#a5adcb"))
        elif kind == "error":
            await view.add_notice(Text(str(payload), style="bold #ed8796"), "error-message")
            self.set_kai_state("error")
        elif kind == "tool_requested":
            self.workspace_active_tool = str(payload["name"])
            self.workspace_monitor.begin_tool(payload["name"], payload.get("arguments", ""))
            self.workspace_snapshot = replace(
                self.workspace_snapshot,
                active_file=self.workspace_monitor.active_file,
            )
            self.refresh_dock()
            self.request_workspace_refresh()
            await view.add_notice(
                Text(f"Tool request  {payload['name']}\n{payload['arguments']}", style="#c6a0f6"),
                "tool-message",
            )
        elif kind == "tool_started":
            await view.add_notice(Text(f"Running {payload['name']}...", style="#f6c177"), "tool-message")
        elif kind == "tool_output":
            await view.add_notice(Text(str(payload), style="#a5adcb"), "tool-message")
        elif kind == "tool_finished":
            self.workspace_monitor.finish_tool(
                payload["name"], payload.get("arguments", ""), bool(payload.get("success", False))
            )
            self.request_workspace_refresh()
            self.workspace_active_tool = ""
            result = str(payload["result"])
            await view.add_tool_result(payload["name"], result)
        elif kind in ("usage_updated", "task_status"):
            self.refresh_dock()
        elif kind == "conversation_selected":
            await view.render_history(self.agent.history)
            await view.add_notice(Text(f"Switched to {payload}", style="#8bd5ca"))
            self.refresh_dock()
        elif kind == "model_selected":
            await view.add_notice(Text(f"Model profile: {payload}", style="#8bd5ca"))
            self.refresh_dock()

    async def on_composer_submitted(self, event: Composer.Submitted):
        if self.busy:
            return
        composer = self.main_query("#composer", Composer)
        composer.text = ""
        self._close_command_palette()
        self.input_history.append(event.value)
        self.history_index = len(self.input_history)
        await self.main_query("#conversation", ConversationView).add_user(event.value)
        if event.value.startswith("/"):
            await self.handle_command(event.value)
            composer.focus()
            return
        self.busy = True
        self.set_kai_state("listening")
        self.run_agent(event.value)

    def on_text_area_changed(self, event: TextArea.Changed):
        if event.text_area.id != "composer":
            return
        raw_text = event.text_area.text
        text = raw_text.strip()
        suggestions = self.main_query("#suggestions", CommandPalette)
        if raw_text.startswith("/") and " " not in raw_text and "\n" not in raw_text:
            self.command_matches = [command for command in COMMANDS if command.startswith(text)]
        else:
            self.command_matches = []
        suggestions.set_matches(self.command_matches, COMMANDS)
        event.text_area.palette_open = bool(self.command_matches)

    def on_composer_complete_requested(self, _event: Composer.CompleteRequested):
        command = self.main_query("#suggestions", CommandPalette).selected_command
        if command:
            self._complete_command(command)

    def on_composer_palette_navigate(self, event: Composer.PaletteNavigate):
        self.main_query("#suggestions", CommandPalette).move_selection(event.direction)

    def on_composer_palette_accepted(self, _event: Composer.PaletteAccepted):
        palette = self.main_query("#suggestions", CommandPalette)
        command = palette.selected_command
        composer = self.main_query("#composer", Composer)
        if not command:
            return
        if composer.text.strip().lower() == command.lower():
            self._close_command_palette()
            composer.post_message(Composer.Submitted(composer.text.strip()))
        else:
            self._complete_command(command)

    def on_composer_palette_dismissed(self, _event: Composer.PaletteDismissed):
        self._close_command_palette()

    def on_command_palette_chosen(self, event: CommandPalette.Chosen):
        self._complete_command(event.command)

    def _complete_command(self, command: str):
        composer = self.main_query("#composer", Composer)
        self._close_command_palette()
        composer.text = command + " "
        composer.focus()
        composer.move_cursor((0, len(composer.text)))

    def _close_command_palette(self):
        palette = self.main_query("#suggestions", CommandPalette)
        palette.set_matches([], COMMANDS)
        self.command_matches = []
        self.main_query("#composer", Composer).palette_open = False

    def on_composer_history_requested(self, event: Composer.HistoryRequested):
        if not self.input_history:
            return
        self.history_index = max(0, min(len(self.input_history), self.history_index + event.direction))
        value = "" if self.history_index == len(self.input_history) else self.input_history[self.history_index]
        self.main_query("#composer", Composer).text = value

    @work(thread=True, exclusive=True, group="agent")
    def run_agent(self, text: str):
        try:
            self.agent.run_interaction_events(
                text,
                self.emit_from_worker,
                approve=self._choice_blocking,
                request_text=self._text_prompt_blocking,
            )
        finally:
            self.call_from_thread(self._worker_finished)

    def _worker_finished(self):
        self._flush_deltas()
        self.busy = False
        if self.current_state not in ("error", "success"):
            self.set_kai_state("idle")
        self.refresh_dock()
        self.main_query("#composer", Composer).focus()

    def on_app_exit(self):
        self.agent.shutdown()

    def request_workspace_refresh(self, selected_file: str = ""):
        if not self.config.ui.get("workspace_enabled", True) or not self.is_mounted:
            return
        if selected_file:
            self.workspace_selected_file = selected_file
        if self.workspace_scan_running:
            self.workspace_scan_pending = True
            return
        self.workspace_scan_running = True
        self.scan_workspace(self.workspace_selected_file)

    @work(thread=True, group="workspace")
    def scan_workspace(self, selected_file: str):
        # Capture the monitor and generation at invocation time so a stale worker
        # cannot overwrite a newer workspace context.
        monitor = self.workspace_monitor
        generation = self.workspace_generation
        snapshot = monitor.refresh(selected_file)
        self.call_from_thread(
            self.post_message,
            AgentEvent("workspace_snapshot", (generation, snapshot)),
        )
        self.call_from_thread(self._workspace_scan_finished)

    def _workspace_scan_finished(self):
        self.workspace_scan_running = False
        if self.workspace_scan_pending:
            self.workspace_scan_pending = False
            self.request_workspace_refresh()

    def on_workspace_file_selected(self, event: WorkspaceFileSelected):
        self.request_workspace_refresh(event.path)

    def _make_workspace_monitor(self) -> WorkspaceMonitor:
        return WorkspaceMonitor(
            self.workspace_context.root,
            max_files=int(self.config.ui.get("workspace_max_files", 2000)),
            max_diff_bytes=int(self.config.ui.get("workspace_diff_max_bytes", 204800)),
        )

    def _on_workspace_changed(self, new_root: str):
        root = Path(new_root).expanduser().resolve()
        self.workspace_generation += 1
        self.workspace_selected_file = ""
        self.workspace_active_tool = ""
        self.workspace_monitor = self._make_workspace_monitor()
        empty_snapshot = WorkspaceSnapshot(root=str(root))
        self.workspace_snapshot = empty_snapshot
        # Immediately clear the Dock so the user does not continue seeing the old workspace.
        self.post_message(AgentEvent("workspace_snapshot", empty_snapshot))
        try:
            self.main_query("#brand-header", BrandHeader).update_meta(
                self.config.model, self.config.active_model_profile, str(root)
            )
        except Exception:
            pass
        self.request_workspace_refresh()

    def action_workspace(self):
        if self.screen.has_class("narrow"):
            if self.workspace_modal and self.workspace_modal.is_mounted:
                self.pop_screen()
                self.workspace_modal = None
                return
            self.workspace_modal = WorkspaceModal(self.workspace_snapshot)
            self.push_screen(self.workspace_modal, lambda _value: setattr(self, "workspace_modal", None))
            return
        focused = self.focused
        dock = self.main_query("#status-dock", StatusDock)
        if focused and dock in list(focused.ancestors):
            self.main_query("#composer", Composer).focus()
        else:
            dock.query_one(WorkspaceTree).focus()

    def action_toggle_auth(self):
        levels = ["manual", "auto", "yolo"]
        current = self.config.authorization_level
        next_level = levels[(levels.index(current) + 1) % len(levels)] if current in levels else "manual"
        self.agent.handle_command(f"/{next_level}")
        self.refresh_dock()
        try:
            self.main_query("#composer", Composer).focus()
        except Exception:
            pass

    # Deprecated alias retained for compatibility.
    def action_toggle_auto(self):
        self.action_toggle_auth()

    def action_toggle_plan(self):
        self._toggle_mode("/plan")

    def action_toggle_think(self):
        self._toggle_mode("/think")

    def _toggle_mode(self, command: str):
        self.agent.handle_command(command)
        self.refresh_dock()
        try:
            self.main_query("#composer", Composer).focus()
        except Exception:
            pass

    def _choice_blocking(self, title: str, options: List[str], default_index: int = 0) -> int:
        completed = threading.Event()
        result = [-1]

        def show_modal():
            def resolved(value):
                result[0] = -1 if value is None else int(value)
                completed.set()
            self.push_screen(ChoiceModal(title, options, default_index), resolved)

        self.call_from_thread(show_modal)
        completed.wait()
        return result[0]

    def _text_prompt_blocking(self, title: str) -> str:
        completed = threading.Event()
        result = [""]

        def show_modal():
            def resolved(value):
                result[0] = value or ""
                completed.set()
            self.push_screen(TextPromptModal(title), resolved)

        self.call_from_thread(show_modal)
        completed.wait()
        return result[0]

    async def handle_command(self, raw: str):
        dispatcher = CommandDispatcher(self.agent)
        result = dispatcher.dispatch(raw)
        view = self.main_query("#conversation", ConversationView)

        if not result.handled:
            await view.add_notice(Text(f"Unknown command: {raw.strip().split()[0].lower()}", style="#ed8796"), "error-message")
            return

        if result.exit_app:
            self.exit()
            return

        kind = result.data.get("kind") if isinstance(result.data, dict) else None

        if kind == "help":
            await view.add_notice(Markdown(build_help_markdown().replace("Available Slash Commands", "Kairo commands")))
            return

        if kind == "skills":
            skills = result.data.get("skills", [])
            skills_text = "".join(f"- **{item['name']}**: {item['description']}\n" for item in skills)
            await view.add_notice(Markdown(skills_text or "No skills loaded."))
            self.refresh_dock()
            return

        if kind == "config":
            await view.add_notice(Markdown(result.message))
            self.refresh_dock()
            return

        if kind == "new":
            await view.clear_messages()
            await view.add_notice(Text(result.message, style="#8bd5ca"))
            self.refresh_dock()
            return

        if kind == "clear":
            await view.clear_messages()
            self.refresh_dock()
            return

        if kind == "undo":
            await view.render_history(self.agent.history)
            self.refresh_dock()
            return

        if kind == "workspace_moved":
            await view.add_notice(Text(result.message, style="#8bd5ca"))
            self.refresh_dock()
            return

        if kind == "workspace_show":
            await view.add_notice(Text(result.message, style="#a5adcb"))
            return

        if result.message:
            style = "#8bd5ca" if result.success else "#ed8796"
            await view.add_notice(Text(result.message, style=style))

        if result.interactive:
            if kind == "sessions":
                self.push_screen(
                    ChoiceModal("Switch conversation", result.data["options"], result.data["default_index"]),
                    self._session_selected,
                )
            elif kind == "model":
                self.push_screen(
                    ChoiceModal("Select provider / model", result.data["profiles"], result.data["default_index"]),
                    self._model_selected,
                )
            return

        if kind == "compress":
            self.busy = True
            self.set_kai_state("compressing")
            self.run_compression()
            return

        if result.refresh_ui:
            self.refresh_dock()

    def _session_selected(self, choice):
        if choice is None or choice < 0:
            return
        sessions = self.agent.conversations.sessions
        if choice >= len(sessions):
            return
        self.agent.conversations.switch_session(sessions[choice].id)
        self.post_message(AgentEvent("conversation_selected", self.agent.active_session_name))

    def _model_selected(self, choice):
        profiles = self.config.get_model_profile_names()
        if choice is None or choice < 0 or choice >= len(profiles):
            return
        self.config.apply_model_profile(profiles[choice])
        self.agent.conversations.set_context_window(self.config.context_window)
        self.agent.conversations.update_runtime_state(model_profile=self.config.active_model_profile)
        self.agent.conversations.save_all(reason="model_switch")
        self.config.save()
        self.main_query("#brand-header", BrandHeader).update_meta(
            self.config.model, self.config.active_model_profile, str(self.workspace_context.root)
        )
        self.post_message(AgentEvent("model_selected", profiles[choice]))
        self.refresh_dock()

    @work(thread=True, exclusive=True, group="agent")
    def run_compression(self):
        success, message = self.agent.compress_context(manual=True)
        self.emit_from_worker("notice" if success else "error", message)
        self.emit_from_worker("state", "success" if success else "error")
        self.call_from_thread(self._worker_finished)
