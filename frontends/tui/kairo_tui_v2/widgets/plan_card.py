"""Plan card: structured plan output, never mixed into plain content."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from kairo_tui_v2.state import PlanCardView
from kairo_tui_v2.widgets.message import blocks_to_markdown


class PlanCard(Vertical):
    """Renders one PlanCardView as a bordered structured card."""

    def __init__(self, card: PlanCardView, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._card = card

    def compose(self) -> ComposeResult:
        card = self._card
        yield Static(card.title, classes="plan-card-title")
        body = blocks_to_markdown(card.blocks)
        if body.strip():
            yield Static(body, classes="plan-card-body")
        if card.instructions:
            yield Static(f"Instructions: {card.instructions}", classes="plan-card-instructions")
