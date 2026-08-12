"""V2 chat-first application shell."""

from __future__ import annotations

from kairo_kernel import KairoKernel
from kairo_kernel.contracts.providers import ProviderConnectionRequest
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding

from kairo_tui_v2._version import __version__
from kairo_tui_v2.controller import TuiController
from kairo_tui_v2.dialogs.connect import ConnectDialog
from kairo_tui_v2.reducer import (
    CloseOverlay,
    ConnectSaved,
    DraftChanged,
    DraftReady,
    SubmitDraft,
    UiAction,
    reduce,
)
from kairo_tui_v2.state import AppState, OverlayKind
from kairo_tui_v2.widgets.composer import Composer
from kairo_tui_v2.widgets.shell import Shell


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
        self._controller = TuiController(kernel)
        self._state = AppState()
        self._last_submitted: str | None = None
        self._last_ready: str | None = None
        self._connect_open = False

    @property
    def state(self) -> AppState:
        return self._state

    def compose(self) -> ComposeResult:
        yield Shell(id="workbench")

    def on_mount(self) -> None:
        self.query_one("#composer", Composer).focus()

    def dispatch_action(self, action: UiAction) -> None:
        self._state = reduce(self._state, action)
        if isinstance(action, DraftReady):
            self._last_ready = action.text
        self._refresh_views()

    def _refresh_views(self) -> None:
        """Mirror overlay state onto the screen stack; nothing else yet (P1)."""
        if self._state.overlay is OverlayKind.CONNECT:
            self._open_connect_dialog()
        else:
            self._close_connect_dialog()

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

    # ---- Actions ----------------------------------------------------------

    def action_focus_composer(self) -> None:
        self.query_one("#composer", Composer).focus()

    def action_command_palette(self) -> None:
        """D0 adds the command palette."""

    def action_leader(self) -> None:
        """D0 adds leader chords."""
