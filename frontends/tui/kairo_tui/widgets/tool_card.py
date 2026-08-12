"""Tool card: one invocation's lifecycle rendered as a bordered card."""

from __future__ import annotations

from kairo_kernel.contracts.content import ContentBlock, TextBlock
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from kairo_tui.state import ToolCardView


class ToolCard(Vertical):
    """Renders one ToolCardView; updated in place by TOOL events."""

    def __init__(self, card: ToolCardView, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._card = card

    def compose(self) -> ComposeResult:
        card = self._card
        status = card.status
        title = f"Tool · {card.name}"
        if status == "completed":
            suffix = f" · {card.result_status}" if card.result_status else ""
            title = f"{title}{suffix}"
        elif status == "running":
            title = f"{title} · running"
        elif status == "started":
            title = f"{title} · started"
        elif status == "requested":
            title = f"{title} · requested"
        yield Static(title, classes="tool-card-title")
        if card.error:
            yield Static(f"[red]{card.error}[/red]", classes="tool-card-error")
        output_text = _output_text(card.output)
        if output_text:
            yield Static(output_text, classes="tool-card-output")


def _output_text(blocks: tuple[ContentBlock, ...]) -> str:

    parts: list[str] = []
    for block in blocks:
        if isinstance(block, TextBlock):
            parts.append(block.text)
    return "\n".join(parts)
