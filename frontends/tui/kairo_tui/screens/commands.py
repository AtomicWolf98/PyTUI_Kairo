"""Searchable command palette for the chat-first TUI shell."""

from __future__ import annotations

from kairo_kernel.contracts.commands import ParsedCommand
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

from kairo_tui.commands import PaletteEntry, active_session_contract, execute_tui_command


class CommandPaletteScreen(ModalScreen[None]):
    """Keyboard-first searchable command list.

    The stable ``cmd-*`` button ids are retained for automation and existing
    integrations; the visible presentation is now a compact picker instead of
    a wall of default full-width buttons.
    """

    DEFAULT_CSS = """
    CommandPaletteScreen { align: center middle; background: $background 80%; }
    CommandPaletteScreen #palette-shell { width: 76; max-width: 92%; height: 80%;
                                           border: round $accent; background: $surface;
                                           padding: 1 2; }
    CommandPaletteScreen #palette-title { height: 1; color: $text; }
    CommandPaletteScreen #palette-hint { height: 1; color: $text-muted; }
    CommandPaletteScreen #command-search { height: 3; margin: 1 0; border: round $panel; }
    CommandPaletteScreen #palette { height: 1fr; border: none; padding: 0; }
    CommandPaletteScreen #palette Button { width: 100%; height: 2; min-width: 0;
                                           padding: 0 1; margin: 0 0 1 0;
                                           background: $surface; color: $text; }
    CommandPaletteScreen #palette Button:hover,
    CommandPaletteScreen #palette Button:focus { background: $panel; color: $text; }
    """

    def __init__(self, app, entries: tuple[PaletteEntry, ...]) -> None:
        super().__init__()
        self._app = app
        self._entries = entries

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="palette-shell"):
            yield Label("Commands", id="palette-title")
            yield Label("Type to filter · Enter runs · Esc closes", id="palette-hint")
            yield Input(placeholder="Search commands…", id="command-search")
            with VerticalScroll(id="palette"):
                for entry in self._entries:
                    yield Button(
                        f"{entry.name}  ·  {entry.help or entry.summary}",
                        id=f"cmd-{entry.name.removeprefix('/')}",
                    )

    def on_mount(self) -> None:
        self.query_one("#command-search", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "command-search":
            return
        query = event.value.strip().lower()
        for button in self.query("#palette Button"):
            if isinstance(button, Button):
                button.display = not query or query in str(button.label).lower()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("cmd-"):
            return
        self.run_worker(self._execute("/" + button_id.removeprefix("cmd-")))

    async def _execute(self, name: str) -> None:
        for entry in self._entries:
            if entry.name != name:
                continue
            if entry.kernel:
                await self._app.kernel.commands.execute(
                    ParsedCommand(name),
                    session_id=active_session_contract(self._app.store.state),
                )
            else:
                await execute_tui_command(self._app, ParsedCommand(name))
            break
        self.dismiss()
