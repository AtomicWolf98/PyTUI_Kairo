"""V2 chat-first application shell."""

from __future__ import annotations

from kairo_kernel import KairoKernel
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding

from kairo_tui_v2._version import __version__
from kairo_tui_v2.reducer import DraftChanged, SubmitDraft, UiAction, reduce
from kairo_tui_v2.state import AppState
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
        self._state = AppState()
        self._last_submitted: str | None = None

    @property
    def state(self) -> AppState:
        return self._state

    def compose(self) -> ComposeResult:
        yield Shell(id="workbench")

    def on_mount(self) -> None:
        self.query_one("#composer", Composer).focus()

    def dispatch_action(self, action: UiAction) -> None:
        self._state = reduce(self._state, action)
        self._refresh_views()

    def _refresh_views(self) -> None:
        """P0: no view mutations yet; labels render in later work orders."""

    @on(Composer.Submitted)
    def on_composer_submitted(self, message: Composer.Submitted) -> None:
        self._last_submitted = message.text
        self.dispatch_action(SubmitDraft(message.text))

    @on(Composer.Changed)
    def on_composer_changed(self, message: Composer.Changed) -> None:
        self.dispatch_action(DraftChanged(self.query_one("#composer", Composer).text))

    def action_focus_composer(self) -> None:
        self.query_one("#composer", Composer).focus()

    def action_command_palette(self) -> None:
        """D0 adds the command palette."""

    def action_leader(self) -> None:
        """D0 adds leader chords."""
