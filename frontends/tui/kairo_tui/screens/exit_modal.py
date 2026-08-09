"""Three-option exit confirmation when background turns are running."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ExitWithTurnsModal(ModalScreen[str]):
    """等待完成 / 停止全部并退出 / 返回"""

    def __init__(self, turn_count: int) -> None:
        super().__init__()
        self.turn_count = turn_count

    def compose(self) -> ComposeResult:
        with Vertical(id="exit-modal"):
            yield Static(f"{self.turn_count} turn(s) still running.")
            yield Button("等待完成 (wait and exit)", id="exit-wait", variant="primary")
            yield Button("停止全部并退出 (cancel all and exit)", id="exit-stop", variant="error")
            yield Button("返回 (back)", id="exit-back", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        choice = event.button.id
        self.dismiss(choice)
