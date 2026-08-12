"""Plan dialog: approve, edit with real text input, or cancel."""

from __future__ import annotations

from kairo_kernel.contracts.enums import InteractionAction
from kairo_kernel.contracts.interactions import InteractionRequest, InteractionResponse
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class PlanDialog(ModalScreen[None]):
    """Plan approval with a real text editor for instructions."""

    BINDINGS = [
        Binding("escape", "cancel_plan", "Cancel", show=False),
    ]

    class Responded(Message):
        def __init__(self, response: InteractionResponse) -> None:
            super().__init__()
            self.response = response

    def __init__(self, request: InteractionRequest, plan_text: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._request = request
        self._plan_text = plan_text

    def compose(self) -> ComposeResult:
        yield Static("Plan", id="plan-title")
        yield Static(self._plan_text, id="plan-body")
        yield Static("", id="plan-error")
        yield Input(id="plan-instructions", placeholder="Edit plan instructions (optional)")
        with Horizontal(id="plan-actions"):
            yield Button("Approve", id="plan-approve")
            yield Button("Edit", id="plan-edit")
            yield Button("Cancel", id="plan-cancel")

    def _respond(self, action: InteractionAction, text: str = "") -> None:
        self.post_message(
            self.Responded(
                InteractionResponse(self._request.interaction_id, self._request.turn_id, action, text)
            )
        )

    @on(Button.Pressed, "#plan-approve")
    def on_approve(self, message: Button.Pressed) -> None:
        self._respond(InteractionAction.APPROVE_ONCE)

    @on(Button.Pressed, "#plan-edit")
    def on_edit(self, message: Button.Pressed) -> None:
        text = self.query_one("#plan-instructions", Input).value.strip()
        if not text:
            self.query_one("#plan-error", Static).update("[red]Enter instructions to edit the plan.[/red]")
            return
        self._respond(InteractionAction.SUBMIT_TEXT, text)

    @on(Button.Pressed, "#plan-cancel")
    def on_cancel(self, message: Button.Pressed) -> None:
        self.action_cancel_plan()

    def action_cancel_plan(self) -> None:
        self._respond(InteractionAction.REJECT)

    def show_error(self, message: str) -> None:
        self.query_one("#plan-error", Static).update(f"[red]{message}[/red]")
