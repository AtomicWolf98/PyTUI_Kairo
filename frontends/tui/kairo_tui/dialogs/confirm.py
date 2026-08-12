"""Generic confirmation dialog (destructive actions)."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmDialog(ModalScreen[None]):
    """Yes/no confirmation; Escape is always a no."""

    BINDINGS = [
        Binding("escape", "decline", "No", show=False),
    ]

    class Answered(Message):
        def __init__(self, confirmed: bool) -> None:
            super().__init__()
            self.confirmed = confirmed

    def __init__(self, prompt: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        yield Static(self._prompt, id="confirm-prompt")
        with Horizontal(id="confirm-actions"):
            yield Button("Yes", id="confirm-yes", variant="error")
            yield Button("No", id="confirm-no")

    @on(Button.Pressed, "#confirm-yes")
    def on_yes(self, message: Button.Pressed) -> None:
        self.post_message(self.Answered(True))

    @on(Button.Pressed, "#confirm-no")
    def on_no(self, message: Button.Pressed) -> None:
        self.post_message(self.Answered(False))

    def action_decline(self) -> None:
        self.post_message(self.Answered(False))
