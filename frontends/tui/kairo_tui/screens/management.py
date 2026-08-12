"""Full-screen command-palette destinations.

Management features remain implemented by their existing store-driven screens,
but are presented as an overlay rather than a second permanent application
shell.  This lets us migrate each feature independently without bringing back
the seven-column workbench.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Label

from kairo_tui.store import PageAction, PageId


class ManagementModal(ModalScreen[None]):
    DEFAULT_CSS = """
    ManagementModal { align: center middle; background: $background 85%; }
    ManagementModal #management-shell { width: 94%; height: 92%;
                                        border: round $accent; background: $background;
                                        padding: 1; }
    ManagementModal #management-title { height: 1; color: $text-muted; padding: 0 1; }
    ManagementModal #management-body { height: 1fr; }
    ManagementModal #management-body Button { width: auto; min-width: 0; height: 2; padding: 0 1; margin: 0 1 1 0; }
    ManagementModal #management-body Input, ManagementModal #management-body Select { height: 3; margin: 0 0 1 0; }
    ManagementModal #management-body .settings-section,
    ManagementModal #management-body .section { height: auto; margin: 0 0 1 0; }
    """

    def __init__(self, app, page: PageId) -> None:
        super().__init__()
        self._app = app
        self._page = page

    def compose(self) -> ComposeResult:
        with Container(id="management-shell"):
            yield Label(f"{self._page.value.capitalize()}  ·  Esc closes", id="management-title")
            yield Container(id="management-body")

    def on_mount(self) -> None:
        self.call_after_refresh(self._mount_body)

    def _mount_body(self) -> None:
        body = self.query_one_optional("#management-body", Container)
        if body is None:
            return
        screen_type = {
            PageId.SETUP: "setup",
            PageId.SESSIONS: "sessions",
            PageId.WORKSPACE: "workspace",
            PageId.MEMORY: "memory",
            PageId.EXTENSIONS: "extensions",
            PageId.SETTINGS: "settings",
            PageId.DOCTOR: "doctor",
        }.get(self._page)
        if screen_type is None:
            return
        body.mount(self._build_screen(screen_type))

    def _build_screen(self, screen_type: str) -> Widget:
        if screen_type == "setup":
            from kairo_tui.screens.setup import SetupScreen

            return SetupScreen(self._app)
        if screen_type == "sessions":
            from kairo_tui.screens.sessions import SessionsScreen

            return SessionsScreen(self._app)
        if screen_type == "workspace":
            from kairo_tui.screens.workspace import WorkspaceScreen

            return WorkspaceScreen(self._app)
        if screen_type == "memory":
            from kairo_tui.screens.memory import MemoryScreen

            return MemoryScreen(self._app)
        if screen_type == "extensions":
            from kairo_tui.screens.extensions import ExtensionsScreen

            return ExtensionsScreen(self._app)
        if screen_type == "settings":
            from kairo_tui.screens.settings import SettingsScreen

            return SettingsScreen(self._app)
        from kairo_tui.screens.doctor import DoctorScreen

        return DoctorScreen(self._app)

    def on_unmount(self) -> None:
        if getattr(self._app, "_modal_page", None) is self._page:
            self._app._modal_page = None
            self._app.store.dispatch(PageAction(PageId.CHAT))
            self._app._show_page(PageId.CHAT)
