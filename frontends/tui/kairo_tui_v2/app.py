"""V2 chat-first application shell."""

from __future__ import annotations

from kairo_kernel import KairoKernel
from kairo_kernel.contracts.identifiers import TurnId
from kairo_kernel.contracts.providers import ProviderConnectionRequest
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding

from kairo_tui_v2._version import __version__
from kairo_tui_v2.commands import LOCAL_COMMANDS
from kairo_tui_v2.controller import TuiController
from kairo_tui_v2.dialogs.commands import CommandPalette
from kairo_tui_v2.dialogs.connect import ConnectDialog
from kairo_tui_v2.dialogs.models import ModelPicker
from kairo_tui_v2.dialogs.sessions import SessionPicker
from kairo_tui_v2.event_loop import KernelEventLoop
from kairo_tui_v2.reducer import (
    CloseOverlay,
    ConnectSaved,
    DraftChanged,
    DraftReady,
    OpenConnectDialog,
    SessionActivated,
    SessionsLoaded,
    StopFinished,
    StopRequested,
    SubmitDraft,
    TurnUpdated,
    UiAction,
    reduce,
)
from kairo_tui_v2.state import AppState, OverlayKind, SessionTranscript, TurnView
from kairo_tui_v2.widgets.composer import Composer
from kairo_tui_v2.widgets.shell import Shell
from kairo_tui_v2.widgets.status import StatusLine
from kairo_tui_v2.widgets.transcript import Transcript


class KairoTuiApp(App[None]):
    """Chat-first workbench; the composer is always focusable and inputable."""

    TITLE = "Kairo"
    SUB_TITLE = __version__
    CSS_PATH = "theme.tcss"

    BINDINGS = [
        Binding("ctrl+l", "focus_composer", "Composer", show=False),
        Binding("ctrl+p", "command_palette", "Commands", priority=True, show=False),
        Binding("ctrl+x", "leader", "Leader", priority=True, show=False),
    ]

    def __init__(self, kernel: KairoKernel | None = None) -> None:
        super().__init__()
        self._kernel = kernel
        self._leader_active = False
        self._leader_timer: object = None
        self._controller = TuiController(kernel)
        self._state = AppState()
        self._last_submitted: str | None = None
        self._last_ready: str | None = None
        self._connect_open = False
        self._event_loop: KernelEventLoop | None = None

    @property
    def state(self) -> AppState:
        return self._state

    def compose(self) -> ComposeResult:
        yield Shell(id="workbench")

    def on_mount(self) -> None:
        self.query_one("#composer", Composer).focus()
        if self._kernel is not None:
            self._event_loop = KernelEventLoop(
                self._kernel,
                self._state,
                emit=self._on_loop_emit,
                recover=self._recover_state,
            )
            self._event_loop.start()
            self.run_worker(self._initial_load())

    async def _initial_load(self) -> None:
        for action in await self._controller.load_workspace():
            self.dispatch_action(action)
        sessions = await self._controller.load_sessions()
        if sessions:
            self.dispatch_action(SessionsLoaded(sessions))
        for turn in await self._controller.load_active_turns():
            self.dispatch_action(TurnUpdated(turn))
        if self._state.active_session_id is None and self._state.sessions:
            self.dispatch_action(SessionActivated(self._state.sessions[-1].session_id))

    def on_unmount(self) -> None:
        if self._event_loop is not None:
            self.run_worker(self._event_loop.close(), exclusive=True)

    def _on_loop_emit(self, state: AppState, actions: tuple[UiAction, ...]) -> None:
        self._state = state
        for action in actions:
            if isinstance(action, TurnUpdated):
                self._maybe_clear_stop(action)
        self._refresh_views()

    def _recover_state(self) -> None:
        """Recovery: reload everything from the kernel into the state."""
        self.run_worker(self._reload_all())

    async def _reload_all(self) -> None:
        if self._state.active_session_id is not None:
            for action in await self._controller.load_history(self._state.active_session_id):
                self.dispatch_action(action)
        sessions = await self._controller.load_sessions()
        if sessions:
            self.dispatch_action(SessionsLoaded(sessions))
        for turn in await self._controller.load_active_turns():
            self.dispatch_action(TurnUpdated(turn))

    def _maybe_clear_stop(self, action: TurnUpdated) -> None:
        if self._state.stopping_turn_id == action.turn.turn_id and action.turn.terminal:
            self._state = reduce(self._state, StopFinished(action.turn.turn_id))

    def dispatch_action(self, action: UiAction) -> None:
        self._state = reduce(self._state, action)
        if isinstance(action, DraftReady):
            self._last_ready = action.text
        if self._event_loop is not None:
            self._event_loop.sync_state(self._state)
        self._refresh_views()

    def _refresh_views(self) -> None:
        """Mirror overlay state and render the active session views."""
        if self._state.overlay is OverlayKind.CONNECT:
            self._open_connect_dialog()
        else:
            self._close_connect_dialog()
        self.run_worker(self._render_transcript(), exclusive=True, group="render")
        composer = self.query_one("#composer", Composer)
        if composer.text != self._state.draft:
            composer.text = self._state.draft
        status = self.query_one("#status-line", StatusLine)
        status.render_status(
            self._state.profile_label or self._state.model_label,
            self._state.notice,
            self._state.stopping_turn_id is not None,
        )

    async def _render_transcript(self) -> None:
        transcript_view = self._active_transcript()
        await self.query_one("#transcript", Transcript).render_session(
            transcript_view,
            stopping=self._state.stopping_turn_id is not None,
            can_retry=bool(self._last_submitted),
        )

    def _active_transcript(self) -> SessionTranscript | None:
        session_id = self._state.active_session_id
        if session_id is None:
            return None
        return next(
            (item for item in self._state.transcripts if item.session_id == session_id),
            None,
        )

    @on(Composer.Submitted)
    def on_composer_submitted(self, message: Composer.Submitted) -> None:
        self._last_submitted = message.text
        self.dispatch_action(SubmitDraft(message.text))
        self.run_worker(self._handle_submit(message.text))

    @on(Composer.Changed)
    def on_composer_changed(self, message: Composer.Changed) -> None:
        self.dispatch_action(DraftChanged(self.query_one("#composer", Composer).text))

    async def _handle_submit(self, text: str) -> None:
        for action in await self._controller.submit_draft(text):
            self.dispatch_action(action)

    # ---- ConnectDialog plumbing -------------------------------------------

    def _open_connect_dialog(self) -> None:
        if self._connect_open:
            return
        self._connect_open = True
        self.push_screen(ConnectDialog())

    def _close_connect_dialog(self) -> None:
        if not self._connect_open:
            return
        self._connect_open = False
        self.pop_screen()

    @on(ConnectDialog.Canceled)
    def on_connect_canceled(self, message: ConnectDialog.Canceled) -> None:
        self._close_connect_dialog()
        self.dispatch_action(CloseOverlay(restore_draft=True))
        self.query_one("#composer", Composer).focus()

    @on(ConnectDialog.SaveRequested)
    def on_connect_save_requested(self, message: ConnectDialog.SaveRequested) -> None:
        self.run_worker(self._handle_connect_save(message.request, message.send_after))

    async def _handle_connect_save(self, request: ProviderConnectionRequest, send_after: bool) -> None:
        dialog = self.screen if isinstance(self.screen, ConnectDialog) else None
        if dialog is None:
            return
        revision = await self._controller.catalog_revision()
        request = dialog.refresh_revision(request, revision)
        result = await self._controller.connect(request)
        if result.error is not None:
            dialog.show_error(result.error.message)
            return
        dialog.clear_secret_widget()
        self._close_connect_dialog()
        self.dispatch_action(ConnectSaved(send_after=send_after))
        if send_after:
            text = self._state.pending_draft or self._last_submitted or ""
            self.run_worker(self._handle_submit(text))

    # ---- Stop / retry ------------------------------------------------------

    @on(Transcript.StopPressed)
    def on_stop_pressed(self, message: Transcript.StopPressed) -> None:
        self.action_stop()

    @on(Transcript.RetryPressed)
    def on_retry_pressed(self, message: Transcript.RetryPressed) -> None:
        self.action_retry()

    def action_stop(self) -> None:
        if self._state.stopping_turn_id is not None:
            return  # no duplicate stop requests
        turn = self._active_turn()
        if turn is None:
            return
        self.dispatch_action(StopRequested(turn.turn_id))
        self.run_worker(self._handle_stop(turn.turn_id))

    def _active_turn(self) -> TurnView | None:
        running = [turn for turn in self._state.turns if not turn.terminal]
        return running[-1] if running else None

    async def _handle_stop(self, turn_id: TurnId) -> None:
        await self._controller.cancel_turn(turn_id)

    def action_retry(self) -> None:
        text = self._last_submitted or ""
        if not text:
            return
        self.run_worker(self._handle_retry(text))

    async def _handle_retry(self, text: str) -> None:
        for action in await self._controller.retry_draft(text):
            self.dispatch_action(action)

    # ---- Actions ----------------------------------------------------------

    def action_focus_composer(self) -> None:
        self.query_one("#composer", Composer).focus()

    async def on_event(self, event: events.Event) -> None:

        if isinstance(event, events.Key) and self._leader_active:
            self._leader_active = False
            if self._leader_timer is not None:
                self._leader_timer.stop()  # type: ignore[attr-defined]
                self._leader_timer = None
            chord = {
                "n": "action_new_session",
                "l": "action_session_picker",
                "b": "action_toggle_sidebar",
                "m": "action_model_picker",
                "c": "action_compress",
            }
            if event.key in chord:
                event.stop()  # type: ignore[union-attr]
                getattr(self, chord[event.key])()
                return
        await super().on_event(event)

    def action_leader(self) -> None:
        """Start a 2-second leader chord; the next key selects the action."""
        self._leader_active = True
        self._leader_timer = self.set_timer(2.0, self._clear_leader)

    def _clear_leader(self) -> None:
        self._leader_active = False
        self._leader_timer = None

    def action_command_palette(self) -> None:
        self.run_worker(self._open_command_palette())

    async def _open_command_palette(self) -> None:
        catalog = await self._controller.kernel_command_catalog()
        self.push_screen(CommandPalette(LOCAL_COMMANDS, catalog))

    @on(CommandPalette.Chosen)
    def on_command_chosen(self, message: CommandPalette.Chosen) -> None:
        self.pop_screen()
        if message.kernel_command:
            self.run_worker(self._run_kernel_command(message.name))
            return
        if message.name == "sessions":
            self.action_session_picker()
        elif message.name == "models":
            self.action_model_picker()
        elif message.name == "sidebar":
            self.action_toggle_sidebar()
        elif message.name == "connect":
            self.dispatch_action(OpenConnectDialog(pending_draft=""))
            self.query_one("#composer", Composer).focus()

    async def _run_kernel_command(self, name: str) -> None:
        for action in await self._controller.execute_command(name):
            self.dispatch_action(action)

    def action_new_session(self) -> None:
        self.run_worker(self._create_session())

    async def _create_session(self) -> None:
        for action in await self._controller.create_session("Chat"):
            self.dispatch_action(action)
        self.run_worker(self._switch_history())

    def action_session_picker(self) -> None:
        self.run_worker(self._open_session_picker())

    async def _open_session_picker(self) -> None:
        picker = SessionPicker()
        await self.push_screen(picker)
        picker.set_sessions(self._state.sessions)

    @on(SessionPicker.Chosen)
    def on_session_chosen(self, message: SessionPicker.Chosen) -> None:
        self.pop_screen()
        self.dispatch_action(SessionActivated(message.session_id))  # type: ignore[arg-type]
        self.run_worker(self._switch_history())

    @on(SessionPicker.NewRequested)
    def on_session_new(self, message: SessionPicker.NewRequested) -> None:
        self.pop_screen()
        self.action_new_session()

    @on(SessionPicker.RenameRequested)
    def on_session_rename(self, message: SessionPicker.RenameRequested) -> None:
        self.run_worker(self._rename_session(message.session_id, message.name))

    async def _rename_session(self, session_id: object, name: str) -> None:
        for action in await self._controller.rename_session(session_id, name):
            self.dispatch_action(action)

    @on(SessionPicker.DeleteRequested)
    def on_session_delete(self, message: SessionPicker.DeleteRequested) -> None:
        self.run_worker(self._delete_session(message.session_id))

    async def _delete_session(self, session_id: object) -> None:
        for action in await self._controller.delete_session(session_id):
            self.dispatch_action(action)

    async def _switch_history(self) -> None:
        if self._state.active_session_id is not None:
            for action in await self._controller.load_history(self._state.active_session_id):
                self.dispatch_action(action)

    def action_model_picker(self) -> None:
        self.run_worker(self._open_model_picker())

    async def _open_model_picker(self) -> None:
        profiles = await self._controller.model_profiles()
        picker = ModelPicker()
        await self.push_screen(picker)
        picker.set_profiles(profiles)

    @on(ModelPicker.ModelChosen)
    def on_model_chosen(self, message: ModelPicker.ModelChosen) -> None:
        self.pop_screen()
        self.run_worker(self._select_model(message.profile_id))

    async def _select_model(self, profile_id: object) -> None:
        for action in await self._controller.select_model(profile_id):
            self.dispatch_action(action)

    @on(ModelPicker.ConnectRequested)
    def on_model_connect(self, message: ModelPicker.ConnectRequested) -> None:
        self.pop_screen()
        self.dispatch_action(OpenConnectDialog(pending_draft=""))

    def action_toggle_sidebar(self) -> None:
        """M0 adds the context sidebar."""

    def action_compress(self) -> None:
        self.run_worker(self._compress())

    async def _compress(self) -> None:
        if self._state.active_session_id is not None:
            await self._kernel.conversations.compress(self._state.active_session_id, "")  # type: ignore[union-attr]
            self.run_worker(self._switch_history())
