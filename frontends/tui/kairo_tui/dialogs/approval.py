"""Approval dialog: fail-closed interaction responses from offered actions."""

from __future__ import annotations

from kairo_kernel.contracts.enums import InteractionAction
from kairo_kernel.contracts.interactions import InteractionRequest, InteractionResponse
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ApprovalDialog(ModalScreen[None]):
    """Shows only the actions the request actually offers; never defaults to approve."""

    BINDINGS = [
        Binding("escape", "fail_closed", "Cancel", show=False),
    ]

    class Responded(Message):
        def __init__(self, response: InteractionResponse) -> None:
            super().__init__()
            self.response = response

    def __init__(self, request: InteractionRequest, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._request = request

    def compose(self) -> ComposeResult:
        request = self._request
        yield Static(request.prompt, id="approval-prompt")
        yield Static("", id="approval-error")
        with Vertical(id="approval-actions"):
            for choice in request.choices:
                yield Button(choice.label, id=f"approval-{choice.action.value}")

    @on(Button.Pressed)
    def on_action_pressed(self, message: Button.Pressed) -> None:
        action_name = str(message.button.id).removeprefix("approval-")
        for choice in self._request.choices:
            if choice.action.value == action_name:
                self.post_message(
                    self.Responded(
                        InteractionResponse(
                            self._request.interaction_id,
                            self._request.turn_id,
                            choice.action,
                        )
                    )
                )
                return

    def action_fail_closed(self) -> None:
        """Escape: STOP first, then reject/safe-default; never approve."""
        actions = {choice.action for choice in self._request.choices}
        action = InteractionAction.STOP
        if action not in actions:
            action = InteractionAction.REJECT if InteractionAction.REJECT in actions else self._request.safe_default
        self.post_message(
            self.Responded(
                InteractionResponse(self._request.interaction_id, self._request.turn_id, action)
            )
        )

    def show_error(self, message: str) -> None:
        self.query_one("#approval-error", Static).update(f"[red]{message}[/red]")
