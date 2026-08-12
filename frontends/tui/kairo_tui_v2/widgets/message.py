"""Single chat message widget: user/assistant, thought folded, markdown body."""

from __future__ import annotations

from kairo_kernel.contracts.content import ContentBlock, ReasoningBlock, TextBlock
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Collapsible, Label, Markdown, Static

from kairo_tui_v2.state import TranscriptEntry


def blocks_to_markdown(blocks: tuple[ContentBlock, ...]) -> str:
    """Serialize visible content blocks to markdown text."""
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, TextBlock):
            parts.append(block.text)
    return "\n\n".join(parts)


def split_thought(content: tuple[ContentBlock, ...]) -> tuple[tuple[ContentBlock, ...], tuple[ContentBlock, ...]]:
    """Split content into (thought blocks, visible blocks)."""
    thought: list[ContentBlock] = []
    visible: list[ContentBlock] = []
    for block in content:
        if isinstance(block, ReasoningBlock):
            thought.append(block)
        else:
            visible.append(block)
    return tuple(thought), tuple(visible)


class MessageWidget(Vertical):
    """One merged transcript message; thoughts are collapsed by default."""

    def __init__(self, entry: TranscriptEntry, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._entry = entry

    def compose(self) -> ComposeResult:
        entry = self._entry
        thought, visible = split_thought(entry.content)
        role_label = "You" if entry.role == "user" else "Kairo"
        classes = "message-user" if entry.role == "user" else "message-assistant"
        yield Label(role_label, classes=f"message-role {classes}")
        if thought:
            thought_text = "\n\n".join(
                block.text for block in thought if isinstance(block, ReasoningBlock)
            )
            yield Collapsible(Static(Text(thought_text, style="dim")), title="Thought", collapsed=True)
        markdown = blocks_to_markdown(visible)
        yield Markdown(markdown, classes="message-body")
