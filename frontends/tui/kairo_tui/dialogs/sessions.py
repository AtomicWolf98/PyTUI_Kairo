"""Session picker: search, switch, create, rename, delete."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, ListItem, ListView, Static

from kairo_tui.state import SessionView


class SessionPicker(ModalScreen[None]):
    """Switching never cancels background turns (kernel owns turn lifecycle)."""

    BINDINGS = [
        Binding("escape", "cancel_picker", "Cancel", show=False),
    ]

    class Chosen(Message):
        def __init__(self, session_id: object) -> None:
            super().__init__()
            self.session_id = session_id

    class NewRequested(Message):
        pass

    class RenameRequested(Message):
        def __init__(self, session_id: object, name: str) -> None:
            super().__init__()
            self.session_id = session_id
            self.name = name

    class DeleteRequested(Message):
        def __init__(self, session_id: object) -> None:
            super().__init__()
            self.session_id = session_id

    def compose(self) -> ComposeResult:
        yield Static("Sessions", id="sessions-title")
        yield Input(id="session-search", placeholder="Search sessions")
        yield ListView(id="session-list")
        yield Static("", id="session-error")
        with Horizontal(id="session-actions"):
            yield Button("New", id="session-new")
            yield Button("Rename", id="session-rename")
            yield Button("Delete", id="session-delete")
            yield Button("Cancel", id="session-cancel")

    def on_mount(self) -> None:
        self.query_one("#session-search", Input).focus()

    def set_sessions(self, sessions: tuple[SessionView, ...]) -> None:
        self._sessions = sessions
        self._refresh("")

    def _refresh(self, query: str) -> None:
        needle = query.strip().lower()
        items: list[ListItem] = []
        self._visible: list[SessionView] = []
        for session in self._sessions:
            if needle in session.name.lower():
                badge = " · running" if session.running else ""
                items.append(ListItem(Static(f"{session.name}{badge}")))
                self._visible.append(session)
        list_view = self.query_one("#session-list", ListView)
        list_view.clear()
        list_view.extend(items)
        if items:
            list_view.index = 0
        self.query_one("#session-error", Static).update("")

    @on(Input.Changed, "#session-search")
    def on_search_changed(self, message: Input.Changed) -> None:
        self._refresh(message.value)

    def _selected(self) -> SessionView | None:
        index = self.query_one("#session-list", ListView).index
        if index is None or index >= len(self._visible):
            return None
        return self._visible[index]

    @on(Input.Submitted, "#session-search")
    def on_search_submitted(self, message: Input.Submitted) -> None:
        list_view = self.query_one("#session-list", ListView)
        if list_view.index is None and len(list_view.children):
            list_view.index = 0
        session = self._selected()
        if session is not None:
            self.post_message(self.Chosen(session.session_id))

    @on(ListView.Selected, "#session-list")
    def on_selected(self, message: ListView.Selected) -> None:
        session = self._selected()
        if session is not None:
            self.post_message(self.Chosen(session.session_id))

    @on(Button.Pressed, "#session-new")
    def on_new(self, message: Button.Pressed) -> None:
        self.post_message(self.NewRequested())

    @on(Button.Pressed, "#session-rename")
    def on_rename(self, message: Button.Pressed) -> None:
        session = self._selected()
        if session is None:
            self.query_one("#session-error", Static).update("[red]Select a session first.[/red]")
            return
        self.post_message(self.RenameRequested(session.session_id, f"{session.name}-renamed"))

    @on(Button.Pressed, "#session-delete")
    def on_delete(self, message: Button.Pressed) -> None:
        session = self._selected()
        if session is None:
            self.query_one("#session-error", Static).update("[red]Select a session first.[/red]")
            return
        self.post_message(self.DeleteRequested(session.session_id))

    @on(Button.Pressed, "#session-cancel")
    def on_cancel(self, message: Button.Pressed) -> None:
        self.action_cancel_picker()

    def action_cancel_picker(self) -> None:
        self.dismiss(None)
