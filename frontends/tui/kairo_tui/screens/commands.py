"""Unified command palette: the merged TUI + kernel registry as a modal list.

Navigation, command palette and slash input all route through the single
``kairo_tui.commands`` registry (tui_plan.md): TUI commands execute through
``execute_tui_command``; kernel business commands run through
``kernel.commands.execute`` with the store's active session. Selecting an
entry executes it and dismisses the palette.
"""

from __future__ import annotations

from kairo_kernel.contracts.commands import ParsedCommand
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label

from kairo_tui.commands import PaletteEntry, active_session_contract, execute_tui_command


class CommandPaletteScreen(ModalScreen[None]):
    """List the merged command registry; run the selected one and close the palette."""

    DEFAULT_CSS = """
    CommandPaletteScreen { align: center middle; }
    CommandPaletteScreen #palette { width: 72; max-height: 80%;
                                    border: round $primary; background: $surface;
                                    padding: 1 2; }
    CommandPaletteScreen #palette Button { width: 100%; }
    """

    def __init__(self, app, entries: tuple[PaletteEntry, ...]) -> None:
        super().__init__()
        self._app = app  # Widget.app is a read-only Textual property
        self._entries = entries

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="palette"):
            yield Label("[b]Commands[/b]")
            for entry in self._entries:
                yield Button(
                    f"{entry.name} — {entry.help or entry.summary}",
                    id=f"cmd-{entry.name.removeprefix('/')}",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("cmd-"):
            return
        name = "/" + button_id.removeprefix("cmd-")
        self.run_worker(self._execute(name))

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
