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
    ConfirmModal,
    ConnectionTestModal,
    ConversationView,
    DoctorModal,
    KeyEditorModal,
    ModelEditorModal,
    ProfileEditorModal,
    ProfileListModal,
    ProviderEditorModal,
    ProviderListModal,
    RoleEditorModal,
    SearchResultModal,
    SecretConfirmModal,
    SettingsScreen,
    StatusDock,
    TextPromptModal,
    WorkspaceFileSelected,
    WorkspaceModal,
    WorkspacePanel,
    WorkspaceTree,
)
from agent.profile_resolver import get_active_profile, mask_key
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

    ProviderListModal, ProviderEditorModal, ModelEditorModal,
    ConnectionTestModal, SecretConfirmModal, SettingsScreen,
    ProfileListModal, ProfileEditorModal, KeyEditorModal,
    RoleEditorModal, ConfirmModal, SearchResultModal, DoctorModal {
        align: center middle;
        background: #000000 55%;
    }

    #provider-list-shell, #provider-editor-shell, #model-editor-shell,
    #connection-test-shell, #secret-confirm-shell, #settings-shell,
    #profile-list-shell, #profile-editor-shell, #key-editor-shell,
    #role-editor-shell, #confirm-shell, #search-result-shell, #doctor-shell {
        width: 72;
        height: auto;
        max-height: 28;
        padding: 1 2;
        background: $surface-2;
        border: solid #3a414b;
    }

    #profile-list, #role-profile-list, #search-result-list {
        height: auto;
        max-height: 16;
        background: $surface;
        border: solid #343b44;
    }

    #profile-list > ListItem, #role-profile-list > ListItem, #search-result-list > ListItem {
        height: 1;
        padding: 0 1;
    }

    #profile-list > ListItem.--highlight, #role-profile-list > ListItem.--highlight,
    #search-result-list > ListItem.--highlight {
        background: #29323a;
    }

    #profile-editor-title, #key-editor-title, #role-editor-title,
    #confirm-title, #search-result-title, #doctor-title {
        color: $cyan;
        text-style: bold;
        height: auto;
        margin-bottom: 1;
    }

    #prof-actions, #role-actions {
        height: 1;
        margin-top: 1;
        color: #7f849c;
    }

    #prof-hint, #key-hint, #role-hint, #confirm-hint, #doctor-hint {
        color: #7f849c;
        height: 1;
    }

    #confirm-message, #doctor-content, #search-result-title {
        height: auto;
        max-height: 14;
        background: $surface;
        padding: 1;
        border: solid #343b44;
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

        # 0.2.3 First-run guidance: if no usable provider/model, prompt the user
        # to either start the wizard (via /provider add) or skip and document.
        from kairo import needs_first_run_setup
        if needs_first_run_setup(self.config):
            self.set_timer(1.0, self._maybe_show_first_run_guidance)
        self._first_run_guidance_shown = False

    def _maybe_show_first_run_guidance(self):
        if self._first_run_guidance_shown:
            return
        self._first_run_guidance_shown = True
        view = self.main_query("#conversation", ConversationView)
        async def emit():
            await view.add_notice(Text(
                "No usable provider/model is configured. Type '/provider add' to launch the setup wizard, "
                "or '/docs providers' for instructions.",
                style="#f6c177",
            ), "notice-message")
        self.call_after_refresh(emit)

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
        active = get_active_profile(self.config)
        key_status = mask_key(active.api_key) if active else "missing"
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
            key_status=key_status,
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
        # 0.2.3 runtime-config modal routing -----------------------------------
        # Pop the corresponding modal BEFORE dispatching to the plain handler.
        # This avoids the plain path blocking on stdin inside the TUI thread.
        if await self._maybe_route_runtime_modal(raw):
            return

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

        if kind in ("keys", "roles", "workspace_saved", "workspace_removed", "key_set", "key_cleared", "role_set", "role_cleared"):
            await view.add_notice(Text(result.message, style="#a5adcb"))
            self.refresh_dock()
            return

        if kind == "doctor":
            self.push_screen(DoctorModal(result.data.get("checks", [])), None)
            return

        if kind == "workspaces":
            if result.data.get("bookmarks"):
                self.push_screen(
                    ChoiceModal("Workspace bookmarks (Enter to move)", result.data["options"], 0),
                    self._workspace_bookmark_chosen,
                )
            else:
                await view.add_notice(Text(result.message, style="#a5adcb"))
            return

        if kind == "session_search":
            results = result.data.get("results", [])
            if results:
                self.agent._last_session_search_results = results
                options = [f"[{r['index']}] {r['name']}" for r in results]
                self.push_screen(
                    SearchResultModal(f"Search: {result.data.get('keyword', '')}", options, 0),
                    self._session_search_chosen,
                )
            else:
                await view.add_notice(Text(result.message, style="#a5adcb"))
            return

        if kind == "config_export":
            await view.add_notice(Text(result.message, style="#8bd5ca"))
            return

        if kind == "config_import":
            await view.add_notice(Text(result.message, style="#8bd5ca"))
            self.refresh_dock()
            try:
                self.main_query("#brand-header", BrandHeader).update_meta(
                    self.config.model, self.config.active_model_profile, str(self.workspace_context.root)
                )
            except Exception:
                pass
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
            elif kind == "workspaces":
                self.push_screen(
                    ChoiceModal("Workspace bookmarks (Enter to move)", result.data["options"], 0),
                    self._workspace_bookmark_chosen,
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
        profiles = self.config.get_profile_ids() if self.config.llm.get("profiles") else self.config.get_model_profile_names()
        if choice is None or choice < 0 or choice >= len(profiles):
            return
        selected_profile = profiles[choice]
        if self.config.llm.get("profiles"):
            self.config.apply_profile(selected_profile)
        else:
            self.config.apply_model_profile(selected_profile)
        self.agent.conversations.set_context_window(self.config.context_window)
        self.agent.conversations.update_runtime_state(model_profile=self.config.active_model_profile)
        self.agent.conversations.save_all(reason="model_switch")
        self.config.save()
        self.main_query("#brand-header", BrandHeader).update_meta(
            self.config.model, self.config.active_model_profile, str(self.workspace_context.root)
        )
        self.post_message(AgentEvent("model_selected", profiles[choice]))
        self.refresh_dock()

    # ---- 0.2.3 runtime-config modal routing ------------------------------------

    async def _maybe_route_runtime_modal(self, raw: str) -> bool:
        """For runtime config commands, pop the corresponding modal.

        Returns True if *raw* was consumed here (caller should stop),
        False otherwise.
        """
        stripped = raw.strip()
        parts = stripped.split(maxsplit=2)
        command = parts[0].lower()
        sub = parts[1].lower() if len(parts) > 1 else ""

        if stripped == "/settings":
            self.push_screen(SettingsScreen(), self._settings_choice)
            return True

        # Runtime config modal paths share the plain-handler completion,
        # but the user interaction can be carried through dedicated modals.
        # For 0.2.3 we wire the most impactful ones to modals and fall back
        # to plain-mode for the rest so feature parity is guaranteed.
        if command == "/provider" and sub in ("add", "edit", "remove", "test"):
            return await self._route_provider_subcommand(stripped, sub)

        if command == "/model" and sub in ("add", "edit", "remove", "test"):
            return await self._route_model_subcommand(stripped, sub)

        if command == "/session" and sub in ("rename", "delete", "export", "reveal", "search", "open"):
            return await self._route_session_subcommand(sub)

        if command == "/docs":
            self._handle_docs_notice(sub)
            return True

        if command == "/config" and sub in ("validate", "backup", "restore", "export", "import"):
            return await self._route_config_subcommand(stripped, sub)

        if command == "/key" and sub in ("set", "clear", "reveal", "migrate"):
            return await self._route_key_subcommand(stripped, sub)

        if command == "/role" and sub in ("set", "clear"):
            return await self._route_role_subcommand(stripped, sub)

        if stripped == "/keys":
            self._handle_keys_notice()
            return True

        if stripped == "/roles":
            self._handle_roles_notice()
            return True

        if stripped == "/doctor":
            self._handle_doctor_notice()
            return True

        if stripped == "/workspaces":
            self._handle_workspaces_notice()
            return True

        if command == "/workspace" and sub in ("save", "remove"):
            return await self._route_workspace_subcommand(stripped, sub)

        if stripped == "/profile" or stripped == "/profiles":
            self._handle_profiles_notice()
            return True

        if command == "/profile" and sub in ("add", "edit", "remove"):
            return await self._route_profile_subcommand(stripped, sub)

        if command == "/providers":
            self._handle_providers_notice()
            return True

        return False

    async def _route_provider_subcommand(self, raw: str, sub: str) -> bool:
        if sub == "add":
            self.push_screen(ProviderEditorModal(title="Add provider"), self._provider_add_form_done)
            return True
        if sub == "edit":
            self.push_screen(
                ProviderListModal(
                    providers=list(self.config.llm["providers"]),
                    active_name=self.config.llm["active_provider"],
                ),
                self._provider_edit_choice,
            )
            return True
        if sub == "remove":
            self.push_screen(
                ProviderListModal(
                    providers=list(self.config.llm["providers"]),
                    active_name=self.config.llm["active_provider"],
                ),
                self._provider_remove_choice,
            )
            return True
        if sub == "test":
            self.push_screen(
                ProviderListModal(
                    providers=list(self.config.llm["providers"]),
                    active_name=self.config.llm["active_provider"],
                ),
                self._provider_test_choice,
            )
            return True
        return False

    async def _route_model_subcommand(self, raw: str, sub: str) -> bool:
        if sub in ("add", "edit"):
            self.push_screen(
                ProviderListModal(
                    providers=list(self.config.llm["providers"]),
                    active_name=self.config.llm["active_provider"],
                ),
                lambda choice: self._model_form_after_provider(sub, choice),
            )
            return True
        if sub in ("remove", "test"):
            self.push_screen(
                ProviderListModal(
                    providers=list(self.config.llm["providers"]),
                    active_name=self.config.llm["active_provider"],
                ),
                lambda choice: self._model_action_after_provider(sub, choice),
            )
            return True
        return False

    # ---- provider callbacks -----------------------------------------------------

    def _provider_add_form_done(self, values):
        if not values:
            self._restore_focus()
            return
        name = (values.get("name") or "").strip()
        if not name:
            self._restore_focus()
            return
        api_key = values.get("api_key") or ""
        api_key_env = values.get("api_key_env") or ""
        allow_inline = bool(api_key)
        if allow_inline:
            # Confirm via SecretConfirmModal before saving inline key.
            self.push_screen(
                SecretConfirmModal(
                    "Inline API keys are written to config.json. Anyone with access to that file will see the key. "
                    "Env-based keys are recommended. Authorize inline key?",
                ),
                lambda approved: self._provider_add_with_key(values, approved, api_key, api_key_env),
            )
            return
        self._provider_add_with_key(values, True, api_key, api_key_env)

    def _provider_add_with_key(self, values, approved: bool, api_key: str, api_key_env: str):
        if not approved:
            self.main_query("#conversation", ConversationView)
            self.post_message(AgentEvent("notice", "Inline API key was not authorized; save cancelled."))
            self._restore_focus()
            return
        from agent.config_editor import ConfigDraft
        draft = ConfigDraft.from_config(self.config)
        # Model defaults from llm.defaults so the new provider has one model.
        defaults = self.config.llm["defaults"]
        added = draft.add_provider(
            name=values["name"],
            base_url=values["base_url"],
            api_key=api_key,
            api_key_env=api_key_env,
            models=[{
                "name": f"{values['name']}-default",
                "temperature": float(defaults["temperature"]),
                "max_tokens": int(defaults["max_tokens"]),
                "context_window": int(defaults["context_window"]),
            }],
        )
        if not added:
            self.post_message(AgentEvent("notice", "Failed to add provider to draft."))
            self._restore_focus()
            return
        draft.set_active_model(values["name"], f"{values['name']}-default")
        allowed_inline = [values["name"]] if api_key else None
        report = draft.apply_to(
            self.config,
            backup=True,
            allow_inline_key=bool(api_key),
            allowed_inline_providers=allowed_inline,
        )
        msg = report.to_text() if not report.ok else f"Provider '{values['name']}' added. Active target: {self.config.active_model_profile}"
        self.post_message(AgentEvent("notice" if report.ok else "error", msg))
        if report.ok:
            self.post_message(AgentEvent("model_selected", self.config.active_model_profile))
            self.config.save(backup=False)
            self.main_query("#brand-header", BrandHeader).update_meta(
                self.config.model, self.config.active_model_profile, str(self.workspace_context.root)
            )
            self.refresh_dock()
        self._restore_focus()

    def _provider_edit_choice(self, choice):
        if not choice:
            self._restore_focus()
            return
        action = choice.get("action")
        name = choice.get("name") or ""
        if action == "edit":
            provider = self.config._get_provider(name) or {}
            self.push_screen(
                ProviderEditorModal(
                    title=f"Edit provider: {name}",
                    defaults={
                        "name": name,
                        "base_url": provider.get("base_url", ""),
                        "api_key_env": provider.get("api_key_env", ""),
                        "api_key": provider.get("api_key", ""),
                    },
                ),
                lambda values: self._provider_edit_form_done(name, values),
            )
            return
        if action == "delete":
            if len(self.config.llm["providers"]) <= 1:
                self.post_message(AgentEvent("notice", "Cannot remove the last provider."))
                self._restore_focus()
                return
            self.push_screen(
                ChoiceModal(f"Remove provider '{name}' and all its models?", ["Remove", "Cancel"], 1),
                lambda idx: self._provider_remove_confirmed(idx, name),
            )

    def _provider_remove_confirmed(self, idx, name):
        if idx != 0 or not name:
            self.post_message(AgentEvent("notice", "Removed cancelled."))
            self._restore_focus()
            return
        from agent.config_editor import ConfigDraft
        draft = ConfigDraft.from_config(self.config)
        if not draft.remove_provider(name):
            self.post_message(AgentEvent("notice", f"Failed to remove '{name}'."))
            self._restore_focus()
            return
        report = draft.apply_to(self.config, backup=True)
        msg = report.to_text() if not report.ok else f"Provider '{name}' removed."
        self.post_message(AgentEvent("notice" if report.ok else "error", msg))
        if report.ok:
            self.refresh_dock()
        self._restore_focus()

    def _provider_edit_form_done(self, original_name, values):
        if not values:
            self._restore_focus()
            return
        from agent.config_editor import ConfigDraft
        draft = ConfigDraft.from_config(self.config)
        api_key = values.get("api_key") or ""
        api_key_env = values.get("api_key_env") or ""
        draft.update_provider(
            original_name,
            base_url=values["base_url"],
            api_key=api_key,
            api_key_env=api_key_env,
            rename=(values["name"] or None),
        )
        allow_inline = bool(api_key)
        if allow_inline:
            self.push_screen(
                SecretConfirmModal("Save inline API key to config.json? Env names are recommended."),
                lambda approved: self._provider_edit_commit(draft, approved, values.get("name") or original_name),
            )
            return
        self._provider_edit_commit(draft, True, "")

    def _provider_edit_commit(self, draft, approved, inline_provider_name: str = ""):
        if not approved:
            self.post_message(AgentEvent("notice", "Inline key not authorized; edit cancelled."))
            self._restore_focus()
            return
        allowed_inline = [inline_provider_name] if inline_provider_name else None
        report = draft.apply_to(
            self.config,
            backup=True,
            allow_inline_key=bool(allowed_inline),
            allowed_inline_providers=allowed_inline,
        )
        msg = report.to_text() if not report.ok else "Provider updated."
        self.post_message(AgentEvent("notice" if report.ok else "error", msg))
        if report.ok:
            self.refresh_dock()
        self._restore_focus()

    def _provider_test_choice(self, choice):
        if not choice or choice.get("action") != "edit":
            self._restore_focus()
            return
        name = choice.get("name") or ""
        provider = self.config._get_provider(name) or {}
        if not provider:
            self._restore_focus()
            return
        self.run_worker(
            lambda: self._run_provider_test_worker(provider.get("base_url", ""), provider.get("api_key_env", ""), provider.get("api_key", ""), provider["models"][0]["name"] if provider.get("models") else ""),
            thread=True, exclusive=True, group="provider-test",
        )

    def _run_provider_test_worker(self, base_url, env_name, inline_key, default_model):
        import os as _os
        from agent.provider_health import test_connection

        api_key = _os.environ.get(env_name) if env_name else inline_key
        result = test_connection(base_url=base_url, api_key=api_key, model=default_model)
        self.call_from_thread(
            self.push_screen, ConnectionTestModal(result.summary(), result.ok), None,
        )

    # ---- model callbacks --------------------------------------------------------

    def _model_form_after_provider(self, mode: str, choice):
        if not choice or choice.get("action") != "edit":
            self._restore_focus()
            return
        name = choice.get("name") or ""
        provider = self.config._get_provider(name) or {}
        existing_model = provider["models"][0] if provider.get("models") else {}
        self.push_screen(
            ModelEditorModal(
                title=f"{mode.title()} model in {name}",
                provider_name=name,
                defaults=existing_model if mode == "edit" else {},
            ),
            lambda values: self._model_form_done(mode, name, values),
        )

    def _model_form_done(self, mode, provider_name, values):
        if not values:
            self._restore_focus()
            return
        from agent.config_editor import ConfigDraft
        draft = ConfigDraft.from_config(self.config)
        defaults = self.config.llm["defaults"]
        try:
            context_window = int(values["context_window"] or defaults["context_window"])
            max_tokens = int(values["max_tokens"] or defaults["max_tokens"])
            temperature = float(values["temperature"] or defaults["temperature"])
        except (TypeError, ValueError):
            self.post_message(AgentEvent("notice", "Model numbers must be numeric."))
            self._restore_focus()
            return
        if mode == "add":
            added = draft.add_model(
                provider_name,
                name=values["name"],
                temperature=temperature,
                max_tokens=max_tokens,
                context_window=context_window,
            )
            if not added:
                self.post_message(AgentEvent("notice", "Failed to add model."))
                self._restore_focus()
                return
        else:
            draft.update_model(
                provider_name,
                values["name"],
                temperature=temperature,
                max_tokens=max_tokens,
                context_window=context_window,
            )
        report = draft.apply_to(self.config, backup=True)
        msg = report.to_text() if not report.ok else f"Model {mode}d in '{provider_name}'."
        self.post_message(AgentEvent("notice" if report.ok else "error", msg))
        if report.ok:
            self.refresh_dock()
        self._restore_focus()

    def _model_action_after_provider(self, mode: str, choice):
        if not choice or choice.get("action") != "edit":
            self._restore_focus()
            return
        name = choice.get("name") or ""
        provider = self.config._get_provider(name) or {}
        models = provider.get("models", []) if provider else []
        if not models:
            self._restore_focus()
            return
        labels = [m["name"] for m in models]
        if mode == "remove":
            self.push_screen(ChoiceModal(f"Remove model from '{name}'", labels, 0), lambda idx: self._model_remove_confirmed(idx, name, labels))
        elif mode == "test":
            self.push_screen(ChoiceModal(f"Test model from '{name}'", labels, 0), lambda idx: self._model_test_confirmed(idx, name, labels, provider))

    def _model_remove_confirmed(self, idx, provider_name, labels):
        if idx < 0 or idx >= len(labels):
            self._restore_focus()
            return
        target = labels[idx]
        from agent.config_editor import ConfigDraft
        draft = ConfigDraft.from_config(self.config)
        if not draft.remove_model(provider_name, target):
            self.post_message(AgentEvent("notice", f"Failed to remove '{target}'."))
            self._restore_focus()
            return
        report = draft.apply_to(self.config, backup=True)
        msg = report.to_text() if not report.ok else f"Model '{target}' removed."
        self.post_message(AgentEvent("notice" if report.ok else "error", msg))
        if report.ok:
            self.refresh_dock()
        self._restore_focus()

    def _model_test_confirmed(self, idx, provider_name, labels, provider):
        if idx < 0 or idx >= len(labels):
            self._restore_focus()
            return
        target = labels[idx]
        env_name = provider.get("api_key_env", "")
        self.run_worker(
            lambda: self._run_provider_test_worker(provider.get("base_url", ""), env_name, provider.get("api_key", ""), target),
            thread=True, exclusive=True, group="provider-test",
        )

    # ---- session modal routing (0.2.4) ------------------------------------------

    async def _route_session_subcommand(self, sub: str) -> bool:
        if sub == "rename":
            store = getattr(self.agent.conversations, "session_store", None)
            if store is None:
                self.post_message(AgentEvent("error", "Session persistence is disabled."))
                return True
            self.push_screen(
                TextPromptModal("Rename session"),
                lambda name: self._session_rename_done(name, store),
            )
            return True
        if sub == "delete":
            store = getattr(self.agent.conversations, "session_store", None)
            if store is None:
                self.post_message(AgentEvent("error", "Session persistence is disabled."))
                return True
            if len(self.agent.conversations.sessions) <= 1:
                self.post_message(AgentEvent("error", "Cannot delete the last session."))
                return True
            options = [s.name for s in self.agent.conversations.sessions]
            self.push_screen(
                ChoiceModal("Delete which session", options, 0),
                lambda idx: self._session_delete_chosen(idx, store),
            )
            return True
        if sub == "export":
            store = getattr(self.agent.conversations, "session_store", None)
            if store is None:
                self.post_message(AgentEvent("error", "Session persistence is disabled."))
                return True
            self.push_screen(
                ChoiceModal("Export format", ["markdown", "json"], 0),
                lambda idx: self._session_export_done(idx, store),
            )
            return True
        if sub == "reveal":
            store = getattr(self.agent.conversations, "session_store", None)
            if store is None:
                self.post_message(AgentEvent("error", "Session persistence is disabled."))
                return True
            path = store.reveal_session_path(self.agent.conversations.active.id)
            if path:
                self.post_message(AgentEvent("notice", f"Session '{self.agent.active_session_name}' file:\n{path}"))
            else:
                self.post_message(AgentEvent("error", "Active session has no on-disk file."))
            return True
        if sub == "search":
            self.push_screen(TextPromptModal("Search sessions"), self._session_search_done)
            return True
        if sub == "open":
            self.push_screen(TextPromptModal("Open session (id or index)"), self._session_open_done)
            return True
        return False

    def _session_search_done(self, keyword: str):
        if not keyword or not keyword.strip():
            self._restore_focus()
            return
        from agent.runtime_commands import _search_sessions
        results = _search_sessions(self.agent, keyword.strip())
        if not results:
            self.post_message(AgentEvent("notice", f"No sessions matched '{keyword.strip()}'."))
            self._restore_focus()
            return
        self.agent._last_session_search_results = results
        options = [f"[{r['index']}] {r['name']}" for r in results]
        self.push_screen(
            SearchResultModal(f"Search: {keyword.strip()}", options, 0),
            self._session_search_chosen,
        )

    def _session_search_chosen(self, choice):
        if choice is None or choice < 0:
            self._restore_focus()
            return
        results = getattr(self.agent, "_last_session_search_results", [])
        if choice >= len(results):
            self._restore_focus()
            return
        target_id = results[choice]["id"]
        self.agent.conversations.switch_session(target_id)
        self.post_message(AgentEvent("conversation_selected", self.agent.active_session_name))
        self._restore_focus()

    def _session_open_done(self, target: str):
        if not target or not target.strip():
            self._restore_focus()
            return
        target = target.strip()
        session = None
        for s in self.agent.conversations.sessions:
            if s.id == target:
                session = s
                break
        if session is None:
            try:
                idx = int(target)
                results = getattr(self.agent, "_last_session_search_results", [])
                if results and 0 <= idx < len(results):
                    target_id = results[idx]["id"]
                    for s in self.agent.conversations.sessions:
                        if s.id == target_id:
                            session = s
                            break
            except ValueError:
                pass
        if session is None:
            self.post_message(AgentEvent("error", f"Session '{target}' not found."))
            self._restore_focus()
            return
        self.agent.conversations.switch_session(session.id)
        self.post_message(AgentEvent("conversation_selected", self.agent.active_session_name))
        self._restore_focus()

    def _session_rename_done(self, name: str, store):
        if not name or not name.strip():
            self.post_message(AgentEvent("error", "Session name cannot be empty."))
            self._restore_focus()
            return
        session = self.agent.conversations.active
        if not store.rename_session(session.id, name.strip()):
            self.post_message(AgentEvent("error", "Failed to rename session."))
            self._restore_focus()
            return
        session.name = name.strip()
        session.touch()
        self.agent.conversations.refresh_context()
        try:
            store.save_session(session, is_active=True, reason="session_rename")
        except Exception as exc:
            self.post_message(AgentEvent("error", f"Index sync warning: {exc}"))
        self.post_message(AgentEvent("notice", f"Session renamed to '{name.strip()}'."))
        self.refresh_dock()
        self._restore_focus()

    def _session_delete_chosen(self, idx: int, store):
        if idx is None or idx < 0:
            self._restore_focus()
            return
        sessions = self.agent.conversations.sessions
        if idx >= len(sessions):
            self._restore_focus()
            return
        target = sessions[idx]
        self.push_screen(
            ChoiceModal(f"Delete '{target.name}'?", ["Delete", "Cancel"], 1),
            lambda confirm_idx: self._session_delete_confirmed(confirm_idx, target, store),
        )

    def _session_delete_confirmed(self, confirm_idx, target, store):
        if confirm_idx != 0:
            self.post_message(AgentEvent("notice", "Cancelled; no changes made."))
            self._restore_focus()
            return
        if not store.delete_session(target.id):
            self.post_message(AgentEvent("error", "Failed to delete session."))
            self._restore_focus()
            return
        self.agent.conversations.sessions = [
            s for s in self.agent.conversations.sessions if s.id != target.id
        ]
        if self.agent.conversations.active_session_id == target.id:
            self.agent.conversations.active_session_id = self.agent.conversations.sessions[0].id
            self.agent.conversations.refresh_context()
        self.agent.conversations.save_active(reason="session_delete")
        self.post_message(AgentEvent("notice", f"Session '{target.name}' deleted."))
        # Re-render conversation view for the now-active session.
        self.post_message(AgentEvent("conversation_selected", self.agent.active_session_name))
        self.refresh_dock()
        self._restore_focus()

    def _session_export_done(self, idx: int, store):
        if idx is None or idx < 0:
            self._restore_focus()
            return
        fmt = "markdown" if idx == 0 else "json"
        session = self.agent.conversations.active
        dest = store.export_session(session.id, fmt=fmt)
        if not dest:
            self.post_message(AgentEvent("error", "Export failed."))
        else:
            self.post_message(AgentEvent("notice", f"Exported '{session.name}' ({fmt}) to:\n{dest}"))
        self._restore_focus()

    # ---- config modal routing (0.2.4) -------------------------------------------

    async def _route_config_subcommand(self, raw: str, sub: str) -> bool:
        from agent.config import Config as _Config
        stripped = raw.strip()
        if sub == "validate":
            from agent.config_editor import ConfigDraft
            report = ConfigDraft.from_config(self.config).validate()
            kind = "notice" if report.ok else "error"
            self.post_message(AgentEvent(kind, report.to_text()))
            return True
        if sub == "backup":
            backup_path = _Config.create_backup(self.config.config_path)
            if backup_path:
                self.post_message(AgentEvent("notice", f"Backup written: {backup_path}"))
            else:
                self.post_message(AgentEvent("error", "Failed to create backup."))
            return True
        if sub == "restore":
            backups = _Config.list_backups(self.config.config_path)
            if not backups:
                self.post_message(AgentEvent("error", "No backups available."))
                return True
            options = [f"{b['name']}  ({b['size']} bytes)" for b in backups]
            self.push_screen(
                ChoiceModal("Restore config backup", options, 0),
                lambda idx: self._config_restore_chosen(idx, backups),
            )
            return True
        if sub == "export":
            # Bare /config export exports redacted immediately.
            # /config export --with-keys asks for confirmation.
            with_keys = stripped.endswith("--with-keys")
            if with_keys:
                self.push_screen(
                    ConfirmModal("Export config WITH plaintext keys?", default=False),
                    self._config_export_with_keys_confirmed,
                )
            else:
                self._config_export_with_keys_confirmed(False)
            return True
        if sub == "import":
            self.push_screen(TextPromptModal("Config import path"), self._config_import_done)
            return True
        return False

    def _config_export_with_keys_confirmed(self, with_keys: bool):
        if with_keys is None:
            # Modal was dismissed without choice (rare); treat as redacted export.
            with_keys = False
        from agent.config_editor import ConfigDraft
        from datetime import datetime
        import json, os
        draft = ConfigDraft.from_config(self.config)
        data = draft.export_config(with_keys=with_keys)
        export_dir = Path(self.config.config_path).parent / ".kairo" / "config_exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = export_dir / f"config.export.{timestamp}.json"
        try:
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, dest)
            msg = "Config exported" if not with_keys else "Config exported WITH plaintext keys"
            self.post_message(AgentEvent("notice", f"{msg} to:\n{dest}"))
        except Exception as exc:
            self.post_message(AgentEvent("error", f"Export failed: {exc}"))
        self._restore_focus()
        self._restore_focus()

    def _config_import_done(self, path: str):
        if not path or not path.strip():
            self._restore_focus()
            return
        path = path.strip()
        source_path = Path(path).expanduser()
        if not source_path.exists():
            self.post_message(AgentEvent("error", f"Import file not found: {path}"))
            self._restore_focus()
            return
        from agent.config_editor import ConfigDraft
        draft = ConfigDraft.from_config(self.config)
        report = draft.import_config(str(source_path))
        if not report.ok:
            self.post_message(AgentEvent("error", "Import validation failed; current config was not overwritten.\n" + report.to_text()))
            self._restore_focus()
            return
        self.push_screen(
            ConfirmModal(f"Import will overwrite config.json with '{path}'. Continue?", default=False),
            lambda approved: self._config_import_confirmed(approved, path, draft),
        )

    def _config_import_confirmed(self, approved: bool, path: str, draft):
        if not approved:
            self.post_message(AgentEvent("notice", "Import cancelled."))
            self._restore_focus()
            return
        report = draft.apply_to(self.config, backup=True)
        if not report.ok:
            self.post_message(AgentEvent("error", "Save refused:\n" + report.to_text()))
            self._restore_focus()
            return
        self.config._sync_runtime_fields()
        self.agent.conversations.set_context_window(self.config.context_window)
        self.agent.conversations.update_runtime_state(model_profile=self.config.active_model_profile)
        self.agent.conversations.save_all(reason="config_import")
        self.post_message(AgentEvent("notice", f"Config imported from '{path}' and saved."))
        self.post_message(AgentEvent("model_selected", self.config.active_model_profile))
        self.refresh_dock()
        try:
            self.main_query("#brand-header", BrandHeader).update_meta(
                self.config.model, self.config.active_model_profile, str(self.workspace_context.root)
            )
        except Exception:
            pass
        self._restore_focus()

    def _config_restore_chosen(self, idx, backups):
        if idx is None or idx < 0 or idx >= len(backups):
            self._restore_focus()
            return
        chosen = backups[idx]["name"]
        self.push_screen(
            ChoiceModal(f"Restore {chosen}? This OVERWRITES config.json.", ["Restore", "Cancel"], 1),
            lambda confirm_idx: self._config_restore_confirmed(confirm_idx, chosen),
        )

    def _config_restore_confirmed(self, confirm_idx, chosen):
        if confirm_idx != 0:
            self.post_message(AgentEvent("notice", "Cancelled; no changes made."))
            self._restore_focus()
            return
        from agent.config import Config as _Config
        if not _Config.restore_backup(self.config.config_path, chosen):
            self.post_message(AgentEvent("error", "Restore failed."))
            self._restore_focus()
            return
        self.config.load()
        self.config._sync_runtime_fields()
        self.agent.conversations.set_context_window(self.config.context_window)
        self.agent.conversations.update_runtime_state(model_profile=self.config.active_model_profile)
        self.agent.conversations.save_all(reason="config_restore")
        self.post_message(AgentEvent("notice", f"Restored {chosen} and reloaded config."))
        self.post_message(AgentEvent("model_selected", self.config.active_model_profile))
        self.refresh_dock()
        try:
            self.main_query("#brand-header", BrandHeader).update_meta(
                self.config.model, self.config.active_model_profile, str(self.workspace_context.root)
            )
        except Exception:
            pass
        self._restore_focus()

    # ---- docs / providers notice (0.2.4) ----------------------------------------

    def _handle_docs_notice(self, topic: str = ""):
        from agent.runtime_commands import DOCS_MAP, _resolve_doc_path
        if not topic:
            msg = (
                "Available topics: config, providers, sessions\n"
                "Local docs:\n"
                "  docs/zh/user-manual.md\n"
                "  docs/en/user-manual.md\n"
                "  docs/commands.md\n"
                "  docs/configuration.md"
            )
            self.post_message(AgentEvent("notice", msg))
            return
        if topic not in DOCS_MAP:
            self.post_message(AgentEvent("error", f"Unknown docs topic: '{topic}'. Available: config, providers, sessions"))
            return
        target = DOCS_MAP[topic]
        path = _resolve_doc_path(target)
        if path:
            self.post_message(AgentEvent("notice", f"Topic '{topic}':\n{path}"))
        else:
            self.post_message(AgentEvent("notice", f"No local doc for topic '{topic}'."))

    def _handle_providers_notice(self):
        lines = []
        for provider in self.config.llm["providers"]:
            marker = "* " if provider["name"] == self.config.llm["active_provider"] else "  "
            model_names = ", ".join(m["name"] for m in provider["models"])
            lines.append(f"{marker}{provider['name']}  base_url={provider.get('base_url', '')}  models=[{model_names}]")
        msg = "Configured Providers\n" + "\n".join(lines or ["(none)"])
        msg += "\n\nUse '/provider add' to add, '/provider edit|remove|test' to manage."
        self.post_message(AgentEvent("notice", msg))

    # ---- 0.2.5 profile / key / role / workspace / doctor modal routing ----------

    def _handle_profiles_notice(self):
        profiles = self.config.llm.get("profiles", [])
        lines = []
        active_id = self.config.llm.get("active_profile")
        for profile in profiles:
            marker = "* " if profile.get("id") == active_id else "  "
            lines.append(f"{marker}{profile.get('id', '')}  model={profile.get('model', '')}  base_url={profile.get('base_url', '')}")
        msg = "Configured Profiles\n" + "\n".join(lines or ["(none)"])
        msg += "\n\nUse '/profile add' to add, '/profile edit|remove' to manage."
        self.post_message(AgentEvent("notice", msg))

    async def _route_profile_subcommand(self, raw: str, sub: str) -> bool:
        if sub == "add":
            self.push_screen(ProfileEditorModal(title="Add profile"), self._profile_add_form_done)
            return True
        if sub == "edit":
            profiles = list(self.config.llm.get("profiles", []))
            if not profiles:
                self.post_message(AgentEvent("notice", "No profiles configured. Use '/profile add' to create one."))
                return True
            self.push_screen(
                ProfileListModal(profiles=profiles, active_id=self.config.llm.get("active_profile", "")),
                self._profile_edit_choice,
            )
            return True
        if sub == "remove":
            profiles = list(self.config.llm.get("profiles", []))
            if not profiles:
                self.post_message(AgentEvent("notice", "No profiles configured."))
                return True
            self.push_screen(
                ProfileListModal(profiles=profiles, active_id=self.config.llm.get("active_profile", "")),
                self._profile_remove_choice,
            )
            return True
        return False

    def _profile_add_form_done(self, values):
        if not values:
            self._restore_focus()
            return
        pid = (values.get("id") or "").strip()
        if not pid:
            self.post_message(AgentEvent("error", "Profile id is required."))
            self._restore_focus()
            return
        api_key = values.get("api_key") or ""
        from agent.config_editor import ConfigDraft
        draft = ConfigDraft.from_config(self.config)
        try:
            added = draft.add_profile(
                id=pid,
                label=values.get("label", ""),
                provider=values.get("provider", ""),
                base_url=values.get("base_url", ""),
                api_key=api_key,
                api_key_env=values.get("api_key_env", ""),
                model=values.get("model") or pid,
                temperature=float(values.get("temperature") or self.config.llm["defaults"]["temperature"]),
                max_tokens=int(values.get("max_tokens") or self.config.llm["defaults"]["max_tokens"]),
                context_window=int(values.get("context_window") or self.config.llm["defaults"]["context_window"]),
            )
        except (TypeError, ValueError) as exc:
            self.post_message(AgentEvent("error", f"Invalid profile values: {exc}"))
            self._restore_focus()
            return
        if not added:
            self.post_message(AgentEvent("error", f"Profile '{pid}' already exists or is invalid."))
            self._restore_focus()
            return
        if api_key:
            self.push_screen(
                SecretConfirmModal("Save inline API key to config.json?"),
                lambda approved: self._profile_add_commit(draft, approved, pid),
            )
            return
        self._profile_add_commit(draft, True, "")

    def _profile_add_commit(self, draft, approved: bool, inline_profile_id: str = ""):
        if not approved:
            self.post_message(AgentEvent("notice", "Inline key not authorized; profile not saved."))
            self._restore_focus()
            return
        report = draft.apply_to(self.config, backup=True, allow_inline_key=bool(inline_profile_id))
        msg = report.to_text() if not report.ok else f"Profile added. Active: {self.config.llm.get('active_profile', '')}"
        self.post_message(AgentEvent("notice" if report.ok else "error", msg))
        if report.ok:
            self.config._sync_runtime_fields()
            self.post_message(AgentEvent("model_selected", self.config.active_model_profile))
            self.main_query("#brand-header", BrandHeader).update_meta(
                self.config.model, self.config.active_model_profile, str(self.workspace_context.root)
            )
            self.refresh_dock()
        self._restore_focus()

    def _profile_edit_choice(self, choice):
        if not choice:
            self._restore_focus()
            return
        action = choice.get("action")
        pid = choice.get("id") or ""
        if action == "edit":
            profile = None
            for p in self.config.llm.get("profiles", []):
                if p.get("id") == pid:
                    profile = p
                    break
            if not profile:
                self._restore_focus()
                return
            self.push_screen(
                ProfileEditorModal(title=f"Edit profile: {pid}", defaults=dict(profile)),
                lambda values: self._profile_edit_form_done(pid, values),
            )
            return
        if action == "delete":
            if len(self.config.llm.get("profiles", [])) <= 1:
                self.post_message(AgentEvent("notice", "Cannot remove the last profile."))
                self._restore_focus()
                return
            self.push_screen(
                ConfirmModal(f"Remove profile '{pid}'?", default=False),
                lambda approved: self._profile_remove_confirmed(approved, pid),
            )
            return
        if action == "copy":
            self.push_screen(
                TextPromptModal(f"Copy '{pid}' to new profile id"),
                lambda new_id: self._profile_copy_done(pid, new_id),
            )

    def _profile_edit_form_done(self, original_id, values):
        if not values:
            self._restore_focus()
            return
        api_key = values.get("api_key") or ""
        from agent.config_editor import ConfigDraft
        draft = ConfigDraft.from_config(self.config)
        try:
            draft.update_profile(
                original_id,
                label=values.get("label"),
                provider=values.get("provider"),
                base_url=values.get("base_url"),
                api_key=api_key,
                api_key_env=values.get("api_key_env"),
                model=values.get("model"),
                temperature=float(values.get("temperature")) if values.get("temperature") else None,
                max_tokens=int(values.get("max_tokens")) if values.get("max_tokens") else None,
                context_window=int(values.get("context_window")) if values.get("context_window") else None,
                new_id=values.get("id") or None,
            )
        except (TypeError, ValueError) as exc:
            self.post_message(AgentEvent("error", f"Invalid profile values: {exc}"))
            self._restore_focus()
            return
        if api_key:
            self.push_screen(
                SecretConfirmModal("Save inline API key to config.json?"),
                lambda approved: self._profile_edit_commit(draft, approved),
            )
            return
        self._profile_edit_commit(draft, True)

    def _profile_edit_commit(self, draft, approved: bool):
        if not approved:
            self.post_message(AgentEvent("notice", "Inline key not authorized; edit cancelled."))
            self._restore_focus()
            return
        report = draft.apply_to(self.config, backup=True, allow_inline_key=True)
        msg = report.to_text() if not report.ok else "Profile updated."
        self.post_message(AgentEvent("notice" if report.ok else "error", msg))
        if report.ok:
            self.config._sync_runtime_fields()
            self.post_message(AgentEvent("model_selected", self.config.active_model_profile))
            self.main_query("#brand-header", BrandHeader).update_meta(
                self.config.model, self.config.active_model_profile, str(self.workspace_context.root)
            )
            self.refresh_dock()
        self._restore_focus()

    def _profile_remove_confirmed(self, approved: bool, pid: str):
        if not approved:
            self.post_message(AgentEvent("notice", "Remove cancelled."))
            self._restore_focus()
            return
        from agent.config_editor import ConfigDraft
        draft = ConfigDraft.from_config(self.config)
        if not draft.remove_profile(pid):
            self.post_message(AgentEvent("error", f"Failed to remove '{pid}'."))
            self._restore_focus()
            return
        report = draft.apply_to(self.config, backup=True)
        msg = report.to_text() if not report.ok else f"Profile '{pid}' removed."
        self.post_message(AgentEvent("notice" if report.ok else "error", msg))
        if report.ok:
            self.config._sync_runtime_fields()
            self.refresh_dock()
        self._restore_focus()

    def _profile_copy_done(self, source_id: str, new_id: str):
        if not new_id or not new_id.strip():
            self._restore_focus()
            return
        new_id = new_id.strip()
        from agent.config_editor import ConfigDraft
        draft = ConfigDraft.from_config(self.config)
        if not draft.copy_profile(source_id, new_id):
            self.post_message(AgentEvent("error", f"Failed to copy '{source_id}' to '{new_id}'."))
            self._restore_focus()
            return
        report = draft.apply_to(self.config, backup=True)
        msg = report.to_text() if not report.ok else f"Profile '{source_id}' copied to '{new_id}'."
        self.post_message(AgentEvent("notice" if report.ok else "error", msg))
        if report.ok:
            self.refresh_dock()
        self._restore_focus()

    async def _route_key_subcommand(self, raw: str, sub: str) -> bool:
        if sub == "set":
            parts = raw.split(maxsplit=2)
            profile_id = parts[2].strip() if len(parts) > 2 else ""
            ids = self.config.get_profile_ids()
            if not ids:
                self.post_message(AgentEvent("notice", "No profiles configured."))
                return True
            if profile_id and profile_id in ids:
                self.push_screen(KeyEditorModal(profile_id), self._key_set_done)
                return True
            self.push_screen(
                ChoiceModal("Set key for which profile", ids, 0),
                lambda idx: self._key_set_profile_chosen(idx, ids),
            )
            return True
        if sub == "clear":
            parts = raw.split(maxsplit=2)
            profile_id = parts[2].strip() if len(parts) > 2 else ""
            ids = self.config.get_profile_ids()
            if not ids:
                self.post_message(AgentEvent("notice", "No profiles configured."))
                return True
            if profile_id and profile_id in ids:
                self.push_screen(
                    ConfirmModal(f"Clear inline key for '{profile_id}'?", default=False),
                    lambda approved: self._key_clear_confirmed(approved, profile_id),
                )
                return True
            self.push_screen(
                ChoiceModal("Clear key for which profile", ids, 0),
                lambda idx: self._key_clear_profile_chosen(idx, ids),
            )
            return True
        if sub == "reveal":
            parts = raw.split(maxsplit=2)
            profile_id = parts[2].strip() if len(parts) > 2 else ""
            ids = self.config.get_profile_ids()
            if not ids:
                self.post_message(AgentEvent("notice", "No profiles configured."))
                return True
            if profile_id and profile_id in ids:
                self._reveal_key(profile_id)
                return True
            self.push_screen(
                ChoiceModal("Reveal key for which profile", ids, 0),
                lambda idx: self._key_reveal_profile_chosen(idx, ids),
            )
            return True
        if sub == "migrate":
            self.push_screen(
                ConfirmModal("Migrate legacy provider keys into profile keys?", default=False),
                self._key_migrate_confirmed,
            )
            return True
        return False

    def _key_set_profile_chosen(self, idx: int, ids: List[str]):
        if idx is None or idx < 0 or idx >= len(ids):
            self._restore_focus()
            return
        self.push_screen(KeyEditorModal(ids[idx]), self._key_set_done)

    def _key_set_done(self, values):
        if not values:
            self._restore_focus()
            return
        profile_id = values.get("profile_id", "")
        key = values.get("key", "")
        if not key:
            self.post_message(AgentEvent("notice", "No key entered."))
            self._restore_focus()
            return
        self.push_screen(
            SecretConfirmModal(f"Save inline API key for '{profile_id}' to config.json?"),
            lambda approved: self._key_set_commit(approved, profile_id, key),
        )

    def _key_set_commit(self, approved: bool, profile_id: str, key: str):
        if not approved:
            self.post_message(AgentEvent("notice", "Inline key not authorized."))
            self._restore_focus()
            return
        from agent.config_editor import ConfigDraft
        draft = ConfigDraft.from_config(self.config)
        if not draft.set_key(profile_id, key):
            self.post_message(AgentEvent("error", f"Failed to set key for '{profile_id}'."))
            self._restore_focus()
            return
        report = draft.apply_to(self.config, backup=True, allow_inline_key=True)
        msg = report.to_text() if not report.ok else f"API key set for '{profile_id}'."
        self.post_message(AgentEvent("notice" if report.ok else "error", msg))
        if report.ok:
            self.config._sync_runtime_fields()
            self.refresh_dock()
        self._restore_focus()

    def _key_clear_profile_chosen(self, idx: int, ids: List[str]):
        if idx is None or idx < 0 or idx >= len(ids):
            self._restore_focus()
            return
        profile_id = ids[idx]
        self.push_screen(
            ConfirmModal(f"Clear inline key for '{profile_id}'?", default=False),
            lambda approved: self._key_clear_confirmed(approved, profile_id),
        )

    def _key_clear_confirmed(self, approved: bool, profile_id: str):
        if not approved:
            self.post_message(AgentEvent("notice", "Clear cancelled."))
            self._restore_focus()
            return
        from agent.config_editor import ConfigDraft
        draft = ConfigDraft.from_config(self.config)
        if not draft.clear_key(profile_id):
            self.post_message(AgentEvent("error", f"Failed to clear key for '{profile_id}'."))
            self._restore_focus()
            return
        report = draft.apply_to(self.config, backup=True)
        msg = report.to_text() if not report.ok else f"API key cleared for '{profile_id}'."
        self.post_message(AgentEvent("notice" if report.ok else "error", msg))
        if report.ok:
            self.config._sync_runtime_fields()
            self.refresh_dock()
        self._restore_focus()

    def _key_reveal_profile_chosen(self, idx: int, ids: List[str]):
        if idx is None or idx < 0 or idx >= len(ids):
            self._restore_focus()
            return
        self._reveal_key(ids[idx])

    def _reveal_key(self, profile_id: str):
        from agent.profile_resolver import resolve_profile
        profile = resolve_profile(self.config, profile_id=profile_id)
        if profile is None:
            self.post_message(AgentEvent("error", f"Profile '{profile_id}' not found."))
            self._restore_focus()
            return
        if not profile.api_key:
            self.post_message(AgentEvent("notice", f"Profile '{profile_id}' has no key to reveal."))
            self._restore_focus()
            return
        self.push_screen(
            ConfirmModal("WARNING: revealing an API key may expose it to the screen. Continue?", default=False),
            lambda approved: self._key_reveal_confirmed(approved, profile_id),
        )

    def _key_reveal_confirmed(self, approved: bool, profile_id: str):
        if not approved:
            self.post_message(AgentEvent("notice", "Reveal cancelled."))
            self._restore_focus()
            return
        from agent.profile_resolver import resolve_profile
        profile = resolve_profile(self.config, profile_id=profile_id)
        if profile and profile.api_key:
            self.post_message(AgentEvent("notice", f"Profile '{profile_id}' API key:\n{profile.api_key}"))
        self._restore_focus()

    def _key_migrate_confirmed(self, approved: bool):
        if not approved:
            self.post_message(AgentEvent("notice", "Migration cancelled."))
            self._restore_focus()
            return
        from agent.config_editor import ConfigDraft
        draft = ConfigDraft.from_config(self.config)
        plan = draft.migrate_keys()
        if not plan:
            self.post_message(AgentEvent("notice", "No legacy keys to migrate."))
            self._restore_focus()
            return
        report = draft.apply_to(self.config, backup=True, allow_inline_key=True)
        msg = report.to_text() if not report.ok else f"Migrated keys for {len(plan)} profile(s)."
        self.post_message(AgentEvent("notice" if report.ok else "error", msg))
        if report.ok:
            self.config._sync_runtime_fields()
            self.refresh_dock()
        self._restore_focus()

    def _handle_keys_notice(self):
        from agent.runtime_commands import _list_profile_key_lines
        lines = _list_profile_key_lines(self.config) or ["(none)"]
        self.post_message(AgentEvent("notice", "API Key Status\n" + "\n".join(lines)))

    async def _route_role_subcommand(self, raw: str, sub: str) -> bool:
        ids = self.config.get_profile_ids()
        if not ids:
            self.post_message(AgentEvent("notice", "No profiles configured."))
            return True
        if sub == "set":
            parts = raw.split(maxsplit=3)
            if len(parts) >= 4:
                role = parts[2].strip()
                profile_id = parts[3].strip()
                if role and profile_id:
                    return self._role_set_commit(role, profile_id)
            self.push_screen(
                RoleEditorModal(roles=["chat", "plan", "compress", "fast"], profiles=ids),
                self._role_editor_done,
            )
            return True
        if sub == "clear":
            parts = raw.split(maxsplit=2)
            role = parts[2].strip() if len(parts) > 2 else ""
            if role:
                return self._role_clear_commit(role)
            self.push_screen(
                ChoiceModal("Clear which role", ["chat", "plan", "compress", "fast"], 0),
                lambda idx: self._role_clear_chosen(idx),
            )
            return True
        return False

    def _role_editor_done(self, values):
        if not values:
            self._restore_focus()
            return
        if values.get("action") == "clear":
            self._role_clear_commit(values.get("role", ""))
            return
        self._role_set_commit(values.get("role", ""), values.get("profile", ""))

    def _role_set_commit(self, role: str, profile_id: str) -> bool:
        role = role.strip()
        profile_id = profile_id.strip()
        if role not in {"chat", "plan", "compress", "fast"}:
            self.post_message(AgentEvent("error", f"Unknown role '{role}'."))
            self._restore_focus()
            return True
        if profile_id not in self.config.get_profile_ids():
            self.post_message(AgentEvent("error", f"Profile '{profile_id}' not found."))
            self._restore_focus()
            return True
        from agent.config_editor import ConfigDraft
        draft = ConfigDraft.from_config(self.config)
        if not draft.set_role(role, profile_id):
            self.post_message(AgentEvent("error", f"Failed to set role '{role}'."))
            self._restore_focus()
            return True
        report = draft.apply_to(self.config, backup=True)
        msg = report.to_text() if not report.ok else f"Role '{role}' set to '{profile_id}'."
        self.post_message(AgentEvent("notice" if report.ok else "error", msg))
        self._restore_focus()
        return True

    def _role_clear_chosen(self, idx: int):
        roles = ["chat", "plan", "compress", "fast"]
        if idx is None or idx < 0 or idx >= len(roles):
            self._restore_focus()
            return
        self._role_clear_commit(roles[idx])

    def _role_clear_commit(self, role: str) -> bool:
        role = role.strip()
        if role not in {"chat", "plan", "compress", "fast"}:
            self.post_message(AgentEvent("error", f"Unknown role '{role}'."))
            self._restore_focus()
            return True
        from agent.config_editor import ConfigDraft
        draft = ConfigDraft.from_config(self.config)
        if not draft.clear_role(role):
            self.post_message(AgentEvent("notice", f"Role '{role}' is not set."))
            self._restore_focus()
            return True
        report = draft.apply_to(self.config, backup=True)
        msg = report.to_text() if not report.ok else f"Role '{role}' cleared."
        self.post_message(AgentEvent("notice" if report.ok else "error", msg))
        self._restore_focus()
        return True

    def _handle_roles_notice(self):
        roles = self.config.model_roles
        lines = [f"  - {role}: {target}" for role, target in roles.items()]
        if not lines:
            lines = ["  (none configured)"]
        self.post_message(AgentEvent("notice", "Model Roles\n" + "\n".join(lines)))

    async def _route_workspace_subcommand(self, raw: str, sub: str) -> bool:
        if sub == "save":
            parts = raw.split(maxsplit=2)
            name = parts[2].strip() if len(parts) > 2 else ""
            if name:
                return self._workspace_save_commit(name)
            self.push_screen(TextPromptModal("Bookmark name"), self._workspace_save_done)
            return True
        if sub == "remove":
            parts = raw.split(maxsplit=2)
            name = parts[2].strip() if len(parts) > 2 else ""
            bookmarks = self.config.workspace_bookmarks
            if name:
                return self._workspace_remove_commit(name)
            if not bookmarks:
                self.post_message(AgentEvent("notice", "No workspace bookmarks."))
                return True
            options = [b["name"] for b in bookmarks]
            self.push_screen(
                ChoiceModal("Remove which bookmark", options, 0),
                lambda idx: self._workspace_remove_chosen(idx, bookmarks),
            )
            return True
        return False

    def _workspace_save_done(self, name: str):
        if not name or not name.strip():
            self._restore_focus()
            return
        self._workspace_save_commit(name.strip())

    def _workspace_save_commit(self, name: str) -> bool:
        from agent.config_editor import ConfigDraft
        draft = ConfigDraft.from_config(self.config)
        path = str(self.agent.workspace_context.root)
        if not draft.add_workspace_bookmark(name, path):
            self.post_message(AgentEvent("error", "Failed to add bookmark."))
            self._restore_focus()
            return True
        report = draft.apply_to(self.config, backup=True)
        msg = report.to_text() if not report.ok else f"Workspace bookmark '{name}' saved."
        self.post_message(AgentEvent("notice" if report.ok else "error", msg))
        self._restore_focus()
        return True

    def _workspace_remove_chosen(self, idx: int, bookmarks):
        if idx is None or idx < 0 or idx >= len(bookmarks):
            self._restore_focus()
            return
        self._workspace_remove_commit(bookmarks[idx]["name"])

    def _workspace_remove_commit(self, name: str) -> bool:
        from agent.config_editor import ConfigDraft
        draft = ConfigDraft.from_config(self.config)
        if not draft.remove_workspace_bookmark(name):
            self.post_message(AgentEvent("error", f"Bookmark '{name}' not found."))
            self._restore_focus()
            return True
        report = draft.apply_to(self.config, backup=True)
        msg = report.to_text() if not report.ok else f"Workspace bookmark '{name}' removed."
        self.post_message(AgentEvent("notice" if report.ok else "error", msg))
        self._restore_focus()
        return True

    def _handle_workspaces_notice(self):
        bookmarks = self.config.workspace_bookmarks
        if not bookmarks:
            self.post_message(AgentEvent("notice", "No workspace bookmarks. Use '/workspace save <name>' to create one."))
            return
        options = [f"{b['name']}: {b['path']}" for b in bookmarks]
        self.push_screen(
            ChoiceModal("Workspace bookmarks (Enter to move)", options, 0),
            self._workspace_bookmark_chosen,
        )

    def _workspace_bookmark_chosen(self, choice):
        if choice is None or choice < 0:
            self._restore_focus()
            return
        bookmarks = self.config.workspace_bookmarks
        if choice >= len(bookmarks):
            self._restore_focus()
            return
        target = bookmarks[choice]["path"]
        result = self.agent.move_workspace(target)
        self.post_message(AgentEvent("notice" if result.success else "error", result.message))
        self._restore_focus()

    def _handle_doctor_notice(self):
        from agent.runtime_commands import handle_doctor, run_doctor_probe
        # Run local checks on UI thread (fast), then probe in worker.
        result = handle_doctor(self.agent, "", [], local_only=True)
        checks = list(result.data.get("checks", []))
        self.push_screen(DoctorModal(checks), None)
        self.run_worker(
            lambda: self._run_doctor_probe_worker(run_doctor_probe),
            thread=True, exclusive=True, group="doctor-probe",
        )

    def _run_doctor_probe_worker(self, probe_fn):
        result = probe_fn(self.agent)
        self.call_from_thread(self._doctor_probe_done, result)

    def _doctor_probe_done(self, result):
        checks = result.data.get("checks", [])
        if checks:
            existing = self.screen.query(DoctorModal)
            for modal in existing:
                modal.add_checks(checks)
                return
            self.push_screen(DoctorModal(checks), None)

    # ---- settings screen --------------------------------------------------------

    def _settings_choice(self, choice):
        if not choice or choice == "close":
            self._restore_focus()
            return
        mapping = {
            "providers": "/providers",
            "profiles": "/profiles",
            "profile_add": "/profile add",
            "profile_edit": "/profile edit",
            "profile_remove": "/profile remove",
            "keys": "/keys",
            "key_set": "/key set",
            "key_clear": "/key clear",
            "roles": "/roles",
            "role_set": "/role set",
            "model_add": "/model add",
            "model_edit": "/model edit",
            "model_remove": "/model remove",
            "model_test": "/model test",
            "config_validate": "/config validate",
            "config_backup": "/config backup",
            "config_restore": "/config restore",
            "config_export": "/config export",
            "config_import": "/config import",
            "doctor": "/doctor",
        }
        target = mapping.get(choice) or ""
        if not target:
            self._restore_focus()
            return
        async def fire():
            await self.handle_command(target)
        self.call_after_refresh(fire)

    # ---- plain-handler runner for non-modal commands ---------------------------

    def _run_plain_to_view(self, raw: str):
        """Run a plain handler in a worker thread and pipe its result into the conversation view.

        Only used for non-interactive commands that return data without calling
        input()/print(). All UI updates go through the thread-safe event bridge.
        """
        dispatcher = CommandDispatcher(self.agent)
        result = dispatcher.dispatch(raw)
        if not result or not result.handled:
            return
        if result.message:
            kind = "notice" if result.success else "error"
            self.call_from_thread(self.post_message, AgentEvent(kind, result.message))
        if result.refresh_ui:
            self.call_from_thread(self.refresh_dock)
        if getattr(result, "interactive", False) and result.data.get("kind") == "provider_test_result":
            test_result = result.data.get("result")
            if test_result is not None:
                self.call_from_thread(
                    self.push_screen, ConnectionTestModal(test_result.summary(), test_result.ok), None,
                )

    def _restore_focus(self):
        try:
            self.main_query("#composer", Composer).focus()
        except Exception:
            pass

    @work(thread=True, exclusive=True, group="agent")
    def run_compression(self):
        success, message = self.agent.compress_context(manual=True)
        self.emit_from_worker("notice" if success else "error", message)
        self.emit_from_worker("state", "success" if success else "error")
        self.call_from_thread(self._worker_finished)
