"""Command palette: merged local display actions and kernel commands."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Input, ListItem, ListView, Static

from kairo_tui_v2.commands import LocalCommand


class CommandPalette(ModalScreen[None]):
    """Searchable palette; execution is delegated to the app via messages."""

    BINDINGS = [
        Binding("escape", "cancel_palette", "Cancel", show=False),
    ]

    class Chosen(Message):
        def __init__(self, name: str, kernel_command: bool) -> None:
            super().__init__()
            self.name = name
            self.kernel_command = kernel_command

    def __init__(
        self,
        local_commands: tuple[LocalCommand, ...],
        kernel_commands: tuple[object, ...],
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._local = local_commands
        self._kernel_commands = kernel_commands

    def compose(self) -> ComposeResult:
        yield Input(id="command-search", placeholder="Search commands")
        yield ListView(id="command-list")
        yield Static("No matching commands", id="command-empty")

    def on_mount(self) -> None:
        self.query_one("#command-search", Input).focus()
        self._refresh("")

    @on(Input.Changed, "#command-search")
    def on_search_changed(self, message: Input.Changed) -> None:
        self._refresh(message.value)

    def _refresh(self, query: str) -> None:
        needle = query.strip().lower()
        items: list[ListItem] = []
        self._mapping: list[tuple[str, bool]] = []
        for local_command in self._local:
            if needle in local_command.name.lower() or needle in local_command.description.lower():
                items.append(ListItem(Static(f"{local_command.name} — {local_command.description}")))
                self._mapping.append((local_command.name, False))
        for command in self._kernel_commands:
            name = str(getattr(command, "name", ""))
            summary = str(getattr(command, "summary", ""))
            if needle in name.lower() or needle in summary.lower():
                items.append(ListItem(Static(f"{name} — {summary}")))
                self._mapping.append((name, True))
        self.query_one("#command-list", ListView).clear()
        self.query_one("#command-list", ListView).extend(items)
        self.query_one("#command-empty", Static).display = not items

    @on(Input.Submitted, "#command-search")
    def on_search_submitted(self, message: Input.Submitted) -> None:
        list_view = self.query_one("#command-list", ListView)
        if list_view.index is None and len(list_view.children):
            list_view.index = 0
        index = list_view.index
        if index is not None and index < len(self._mapping):
            name, kernel_command = self._mapping[index]
            self.post_message(self.Chosen(name, kernel_command))

    @on(ListView.Selected, "#command-list")
    def on_selected(self, message: ListView.Selected) -> None:
        index = self.query_one("#command-list", ListView).index
        if index is not None and index < len(self._mapping):
            name, kernel_command = self._mapping[index]
            self.post_message(self.Chosen(name, kernel_command))

    def action_cancel_palette(self) -> None:
        self.dismiss(None)
