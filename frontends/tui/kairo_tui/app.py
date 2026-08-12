"""KairoTuiApp: the Textual application shell."""

from __future__ import annotations

import asyncio
from pathlib import Path

from kairo_kernel import KairoKernel
from kairo_kernel.contracts.enums import AuthorizationMode
from kairo_kernel.contracts.identifiers import TurnId
from kairo_kernel.contracts.lifecycle import ShutdownRequest
from kairo_kernel.contracts.preferences import PreferencesPatch
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.timer import Timer
from textual.widgets import Button

from kairo_tui.bootstrap import BootstrapOptions, BootstrapResult, build_running_kernel
from kairo_tui.cli import CliOptions
from kairo_tui.event_pump import EventPump
from kairo_tui.layout import Breakpoint, responsive_layout
from kairo_tui.page import refresh_sessions
from kairo_tui.screens.inspector import InspectorPanel
from kairo_tui.store import (
    AppStore,
    CompatAction,
    DraftAction,
    InspectorAction,
    KernelStatusAction,
    PageAction,
    PageId,
    SessionAction,
    UserTurnAction,
)
from kairo_tui.widgets import Composer, StatusLine, TopBar


class KairoTuiApp(App[None]):
    TITLE = "Kairo"
    SUB_TITLE = "0.4.0a2"
    ENABLE_COMMAND_PALETTE = False

    # OpenCode-style leader bindings are the visible contract.  The old
    # single-key bindings remain as hidden compatibility aliases for existing
    # scripts and muscle memory; they do not render a navigation rail.
    BINDINGS = [
        Binding("escape", "esc", "Escape", priority=True, show=False),
        Binding("ctrl+x", "leader", "Leader", priority=True, show=False),
        Binding("ctrl+p", "command_palette", "Commands", priority=True, show=False),
        Binding("ctrl+l", "focus_composer", "Composer", show=False),
        Binding("ctrl+t", "toggle_thinking", "Thinking", show=False),
        Binding("ctrl+1", "page('chat')", "Chat", show=False),
        Binding("ctrl+2", "page('sessions')", "Sessions", show=False),
        Binding("ctrl+3", "page('workspace')", "Workspace", show=False),
        Binding("ctrl+b", "page('workspace')", "Workspace", show=False),
        Binding("ctrl+4", "page('memory')", "Memory", show=False),
        Binding("ctrl+5", "page('extensions')", "Extensions", show=False),
        Binding("ctrl+6", "page('settings')", "Settings", show=False),
        Binding("ctrl+7", "page('doctor')", "Doctor", show=False),
        Binding("ctrl+k", "command_palette", "Commands", show=False),
        Binding("ctrl+n", "new_chat", "New chat", show=False),
        Binding("ctrl+a", "toggle_authorization", "Authorization", show=False),
    ]

    CSS = """
    /* Chat-first shell: legacy nav/inspector stay mounted for compatibility
       but consume no visual space. They are replaced by the command palette
       and the opt-in context drawer. */
    #workbench { layout: vertical; background: $background; }
    #topbar { height: 2; padding: 0 2; content-align: left middle;
              color: $text; background: $surface; border-bottom: solid $panel; }
    #body { layout: horizontal; height: 1fr; position: relative; }
    #nav { width: 1; min-width: 0; padding: 0; background: $background;
           opacity: 0; overflow: hidden; }
    #nav Button { width: 1; min-width: 1; height: 1; padding: 0; }
    #page { width: 1fr; height: 1fr; min-width: 0; padding: 0 2; }
    #inspector { width: 42; min-width: 42; position: absolute; dock: right;
                 height: 1fr; padding: 0 1; background: $surface;
                 opacity: 0; overflow: hidden; }
    #inspector.drawer-open { opacity: 1; }
    #composer { height: auto; min-height: 3; max-height: 8; margin: 0 2;
                border: round $panel; background: $surface; padding: 0 1; }
    #composer:focus { border: round $accent; }
    #status-line { height: 1; padding: 0 2; color: $text-muted; background: $background; }
    #page > ChatScreen { height: 1fr; }
    #page > * { width: 1fr; }
    #page Button { min-width: 0; width: auto; height: 2; padding: 0 1; margin: 0 1 1 0; }
    /* Dense management forms must remain directly clickable without forcing
       compatibility tests and keyboard users to scroll between every row. */
    #page SettingsScreen Button { margin: 0; }
    #page Input, #page Select { height: 3; margin: 0 0 1 0; }
    #page SettingsScreen, #page SessionsScreen, #page WorkspaceScreen,
    #page MemoryScreen, #page ExtensionsScreen, #page DoctorScreen,
    #page SetupScreen { padding: 1 2; }
    .bp-narrow #inspector { display: none; }
    .bp-narrow #inspector.drawer-open { display: block; }
    .bp-overlay #inspector.drawer-open { width: 1fr; min-width: 0; }
    .bp-compat #page { padding: 0; }
    .bp-compat #topbar { padding: 0 1; }
    .bp-compat #composer { margin: 0; }
    .bp-reduced-motion #workbench * { transition: none !important; }
    """

    THEME_ALIASES = {"default": "textual-dark"}

    def __init__(self, bootstrap: BootstrapResult) -> None:
        super().__init__()
        self._bootstrap = bootstrap
        self.kernel: KairoKernel = bootstrap.kernel
        self.store: AppStore = bootstrap.store
        self._pump = EventPump(self.kernel, self.store)
        self._breakpoint = Breakpoint.FULL
        self._current_page: PageId | None = None
        self._last_theme: str | None = None
        self._leader_active = False
        self._leader_timer: Timer | None = None
        self._modal_page: PageId | None = None
        self.store.subscribe(self._on_store_changed)

    @classmethod
    def from_options(cls, options: CliOptions) -> KairoTuiApp:
        workspace = options.workspace or str(Path.cwd())
        bootstrap = build_running_kernel(
            BootstrapOptions(
                workspace_root=workspace,
                config_path=Path(options.config_path) if options.config_path else None,
                theme=options.theme,
                reduced_motion=options.reduced_motion,
                safe_mode=options.safe_mode,
            )
        )
        return cls(bootstrap)

    def compose(self) -> ComposeResult:
        with Container(id="workbench"):
            yield TopBar(id="topbar")
            with Horizontal(id="body"):
                yield VerticalScroll(id="nav", classes="nav")
                yield Container(id="page")
                yield InspectorPanel(self, id="inspector")
            yield Composer(id="composer", placeholder="Ask Kairo… (Enter submits)")
            yield StatusLine(id="status-line")

    def on_mount(self) -> None:
        self._apply_responsive(self.size.width, self.size.height)
        self._render_nav()
        self.run_worker(self._pump_run())
        self._refresh_store_widgets()
        self._last_theme = self.store.state.document.theme
        self._apply_theme()
        self.set_class(self.store.state.reduced_motion, "bp-reduced-motion")

    def _apply_theme(self) -> None:
        name = self.store.state.document.theme or "default"
        target = self.THEME_ALIASES.get(name, name)
        if target not in self.available_themes:
            target = "textual-dark"
            self.notify(f"Theme '{name}' is not available; using textual-dark.")
        if self.theme != target:
            self.theme = target

    def on_resize(self, event) -> None:
        self._apply_responsive(event.size.width, event.size.height)

    def _apply_responsive(self, width: int, height: int) -> None:
        new_breakpoint = responsive_layout((width, height))
        if new_breakpoint is self._breakpoint:
            return
        self._breakpoint = new_breakpoint
        for bp in Breakpoint:
            self.set_class(bp is new_breakpoint, f"bp-{bp.value}")
        self.store.dispatch(CompatAction(new_breakpoint is Breakpoint.COMPAT))

    def action_leader(self) -> None:
        """Start the two-key OpenCode-style command chord."""
        self._leader_active = True
        if self._leader_timer is not None:
            self._leader_timer.stop()
        self._leader_timer = self.set_timer(2.0, self._clear_leader)
        self.notify("Leader: n new · l sessions · b sidebar · c compact · m model · q quit", timeout=2)

    def _clear_leader(self) -> None:
        self._leader_active = False
        self._leader_timer = None

    def on_key(self, event) -> None:
        if not self._leader_active:
            return
        key = event.key.lower()
        self._clear_leader()
        event.stop()
        actions = {
            "n": self.action_new_chat,
            "l": lambda: self.action_page("sessions"),
            "b": self.action_toggle_sidebar,
            "c": lambda: self.run_worker(self._run_command("/compress")),
            "m": lambda: self.run_worker(self._run_command("/model")),
            "a": self.action_toggle_authorization,
            "t": self.action_toggle_thinking,
            "q": self.action_quit,
        }
        action = actions.get(key)
        if action is not None:
            result = action()
            if asyncio.iscoroutine(result):
                self.run_worker(result)

    def action_toggle_sidebar(self) -> None:
        inspector = self.query_one_optional("#inspector", InspectorPanel)
        if inspector is None:
            return
        inspector.toggle_class("drawer-open")
        self.store.dispatch(InspectorAction(inspector.has_class("drawer-open")))

    async def _pump_run(self) -> None:
        await self._pump.run()

    async def on_unmount(self) -> None:
        """Best-effort teardown: stop the pump, then shut the kernel down.

        Idempotent — ``kernel.shutdown`` returns the cached report on the
        second call, so the explicit exit flow may shut down first.
        """
        await self._pump.close()
        await self.kernel.shutdown()

    def _render_nav(self) -> None:
        nav = self.query_one("#nav", VerticalScroll)
        nav.remove_children()
        for page in (
            PageId.CHAT, PageId.SESSIONS, PageId.WORKSPACE, PageId.MEMORY,
            PageId.EXTENSIONS, PageId.SETTINGS, PageId.DOCTOR,
        ):
            nav.mount(Button(page.value.capitalize(), id=f"nav-{page.value}"))

    def action_page(self, page: str) -> None:
        try:
            page_id = PageId(page)
        except ValueError:
            return
        self.store.dispatch(PageAction(page_id))
        self._show_page(page_id)

    def open_management(self, page: PageId) -> None:
        """Open a management destination as a modal over the conversation."""
        if self._modal_page is page:
            return
        if self._modal_page is not None and len(self.screen_stack) > 1:
            self.pop_screen()
        self._modal_page = page
        self.store.dispatch(PageAction(page))
        from kairo_tui.screens.management import ManagementModal

        self.push_screen(ManagementModal(self, page))

    def _show_page(self, page: PageId) -> None:
        if self._current_page is page:
            return  # already shown; mounting twice in one turn would duplicate ids
        self._current_page = page
        container = self.query_one("#page", Container)
        container.remove_children()
        if page is PageId.CHAT:
            from kairo_tui.screens.chat import ChatScreen
            container.mount(ChatScreen(self))
        elif page is PageId.SETUP:
            from kairo_tui.screens.setup import SetupScreen
            container.mount(SetupScreen(self))
        elif page is PageId.SESSIONS:
            from kairo_tui.screens.sessions import SessionsScreen
            container.mount(SessionsScreen(self))
        elif page is PageId.WORKSPACE:
            from kairo_tui.screens.workspace import WorkspaceScreen
            container.mount(WorkspaceScreen(self))
        elif page is PageId.MEMORY:
            from kairo_tui.screens.memory import MemoryScreen
            container.mount(MemoryScreen(self))
        elif page is PageId.EXTENSIONS:
            from kairo_tui.screens.extensions import ExtensionsScreen
            container.mount(ExtensionsScreen(self))
        elif page is PageId.SETTINGS:
            from kairo_tui.screens.settings import SettingsScreen
            container.mount(SettingsScreen(self))
        elif page is PageId.DOCTOR:
            from kairo_tui.screens.doctor import DoctorScreen
            container.mount(DoctorScreen(self))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("nav-"):
            return
        self.action_page(button_id.removeprefix("nav-"))

    def action_focus_composer(self) -> None:
        self.query_one("#composer", Composer).focus()

    def action_esc(self) -> None:
        if len(self.screen_stack) > 1:
            self.pop_screen()
            return
        session_id = self.store.state.active_session_id
        if session_id is None:
            return
        foreground = next(
            (t for t in self.store.state.active_turns if str(t.session_id) == session_id),
            None,
        )
        if foreground is not None:
            self.run_worker(self._cancel_turn(foreground.turn_id))

    async def _cancel_turn(self, turn_id: str) -> None:
        await self.kernel.cancel(TurnId(turn_id), "Escape pressed.")

    def action_new_chat(self) -> None:
        self.run_worker(self._new_chat())

    async def _new_chat(self) -> None:
        created = await self.kernel.sessions.create("Chat")
        if created.ok and created.value is not None:
            self.store.dispatch(SessionAction(str(created.value.session_id)))
            await refresh_sessions(self)  # store gap: sessions list is not event-driven
            self.store.dispatch(PageAction(PageId.CHAT))
            self._show_page(PageId.CHAT)

    def action_toggle_authorization(self) -> None:
        self.run_worker(self._toggle_preference("authorization_mode"))

    def action_toggle_plan(self) -> None:
        self.run_worker(self._toggle_preference("plan_mode"))

    def action_toggle_thinking(self) -> None:
        self.run_worker(self._toggle_preference("thinking_mode"))

    async def _toggle_preference(self, name: str) -> None:
        if self.store.state.safe_mode and name == "authorization_mode":
            return  # safe mode forces Manual
        snapshot = await self.kernel.preferences.snapshot()
        if name == "authorization_mode":
            target = (
                AuthorizationMode.AUTO
                if snapshot.authorization_mode is AuthorizationMode.MANUAL
                else AuthorizationMode.MANUAL
            )
            patch = PreferencesPatch(snapshot.revision, authorization_mode=target)
        elif name == "plan_mode":
            patch = PreferencesPatch(snapshot.revision, plan_mode=not snapshot.plan_mode)
        else:
            patch = PreferencesPatch(snapshot.revision, thinking_mode=not snapshot.thinking_mode)
        await self.kernel.preferences.patch(patch)
        # Reflect the toggle in the top bar immediately (the CONFIG_CHANGED
        # event alone does not refresh kernel_status in the store).
        status = await self.kernel.status()
        self.store.dispatch(KernelStatusAction(status))

    def action_command_palette(self) -> None:
        # Push the merged TUI + kernel command registry modal (shared slash/palette registry).
        from kairo_tui.commands import build_command_palette
        from kairo_tui.screens.commands import CommandPaletteScreen

        self.push_screen(CommandPaletteScreen(self, build_command_palette(self)))

    def _on_store_changed(self, state) -> None:
        self._refresh_store_widgets()
        if state.document.theme != self._last_theme:
            self._last_theme = state.document.theme
            self._apply_theme()

    async def action_quit(self) -> None:
        """Ctrl+Q and the /exit command both route through the confirmation flow."""
        await self.request_exit()

    async def request_exit(self) -> None:
        active = await self.kernel.active_turns()
        if not active:
            await self._shutdown_and_exit()
            return
        from kairo_tui.screens.exit_modal import ExitWithTurnsModal

        # Snapshot the active turn ids BEFORE showing the modal: a turn may end
        # between the snapshot and the user's choice, and the wait path below
        # must still complete (kernel.wait on a finished turn returns at once).
        turn_ids = tuple(str(turn.turn_id) for turn in active)
        choice = await self.push_screen_wait(ExitWithTurnsModal(len(active)))
        if choice == "exit-wait":
            self.run_worker(self._wait_for_turns_and_exit(turn_ids))
        elif choice == "exit-stop":
            await self.kernel.shutdown(ShutdownRequest(grace_period_seconds=5.0, cancel_active_turn=True))
            self.exit()
        # "exit-back": stay

    async def _wait_for_turns_and_exit(self, turn_ids: tuple[str, ...]) -> None:
        """Wait for the snapshot's turns via the kernel (never poll the store);
        a turn that finished before or during the modal returns immediately."""
        if turn_ids:
            await asyncio.gather(*(self.kernel.wait(TurnId(turn_id)) for turn_id in turn_ids))
        still_active = await self.kernel.active_turns()
        if still_active:
            self.notify("Active turns are still running; exit cancelled.", severity="error", timeout=10)
            return
        await self._shutdown_and_exit()

    async def _shutdown_and_exit(self) -> None:
        await self.kernel.shutdown(ShutdownRequest(grace_period_seconds=5.0, cancel_active_turn=True))
        self.exit()

    def _refresh_store_widgets(self) -> None:
        # Store callbacks may fire while the app is tearing down (the pump folds
        # shutdown events after app.exit()); the widgets are gone by then, so a
        # strict query would raise NoMatches inside a worker. Optional queries
        # make the refresh a no-op on the detached default screen.
        topbar = self.query_one_optional("#topbar", TopBar)
        status_line = self.query_one_optional("#status-line", StatusLine)
        composer = self.query_one_optional("#composer", Composer)
        if topbar is not None:
            topbar.render_status(self.store.state)
        if status_line is not None:
            status_line.render_state(self.store.state)
        if composer is not None:
            composer.disabled = not self.store.state.setup_complete
        # Mount the page only when it actually changes (never remount Setup on
        # every store dispatch — that would reset its step state).
        if self._modal_page is None and self.store.state.page is not self._current_page:
            self._show_page(self.store.state.page)

    def on_composer_submitted(self, message: Composer.Submitted) -> None:
        text = message.text.strip()
        if not text:
            return
        if not self.store.state.setup_complete:
            return
        composer = self.query_one("#composer", Composer)
        composer.push_history(text)
        composer.clear()
        self.store.dispatch(DraftAction(""))
        if text.startswith("/"):
            self.run_worker(self._run_command(text))
            return
        self.run_worker(self._submit_turn(text))

    async def _submit_turn(self, text: str) -> None:
        from kairo_kernel.contracts.identifiers import SessionId
        from kairo_kernel.contracts.turns import TurnRequest

        session_id = self.store.state.active_session_id
        if session_id is None:
            created = await self.kernel.sessions.create("Chat")
            if not created.ok or created.value is None:
                return
            session_id = str(created.value.session_id)
            self.store.dispatch(SessionAction(session_id))
        accepted = await self.kernel.submit(TurnRequest(text, session_id=SessionId(session_id)))
        if accepted.ok and accepted.value is not None:
            # The TUI inserts its own user bubble (events never carry user
            # messages); sequence is the last folded event, before this turn's.
            self.store.dispatch(UserTurnAction(session_id, str(accepted.value.turn_id), text))

    async def _run_command(self, text: str) -> None:
        from kairo_kernel.contracts.identifiers import SessionId

        from kairo_tui.commands import execute_tui_command, parse_tui_command

        parsed = parse_tui_command(text)
        if parsed is not None and await execute_tui_command(self, parsed):
            return
        parsed_kernel = self.kernel.commands.parse(text)
        if parsed_kernel.ok and parsed_kernel.value is not None:
            session_id = self.store.state.active_session_id
            await self.kernel.commands.execute(
                parsed_kernel.value,
                session_id=SessionId(session_id) if session_id else None,
            )
