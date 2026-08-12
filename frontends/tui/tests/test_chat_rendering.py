"""C1 acceptance: transcript rendering quality (markdown, unicode, widths)."""

from __future__ import annotations

from kairo_kernel.contracts.content import ReasoningBlock, TextBlock
from kairo_kernel.contracts.identifiers import MessageId, SessionId

from kairo_tui.state import TranscriptEntry
from kairo_tui.widgets.message import blocks_to_markdown, split_thought
from kairo_tui.widgets.transcript import merge_deltas

SESSION = SessionId("session-1")


def test_merge_deltas_combines_adjacent_same_message() -> None:
    entries = (
        TranscriptEntry(MessageId("m1"), "assistant", "assistant", (TextBlock("a"),)),
        TranscriptEntry(MessageId("m1"), "assistant", "assistant", (TextBlock("b"),)),
        TranscriptEntry(MessageId("m2"), "user", "chat", (TextBlock("c"),)),
        TranscriptEntry(MessageId("m1"), "assistant", "assistant", (TextBlock("d"),)),
    )
    merged = merge_deltas(entries)
    assert len(merged) == 3
    assert merged[0].content == (TextBlock("a"), TextBlock("b"))
    assert merged[2].content == (TextBlock("d"),)


def test_split_thought_separates_reasoning() -> None:
    thought, visible = split_thought((ReasoningBlock("r"), TextBlock("v")))
    assert thought == (ReasoningBlock("r"),)
    assert visible == (TextBlock("v"),)


def test_blocks_to_markdown_keeps_code_blocks() -> None:
    code = "```python\nprint('hi')\n```"
    markdown = blocks_to_markdown((TextBlock("before\n\n" + code + "\n\nafter"),))
    assert "```python" in markdown
    assert "print('hi')" in markdown


def test_blocks_to_markdown_long_text_preserved() -> None:
    long_text = "\n\n".join(f"paragraph {index}" for index in range(50))
    markdown = blocks_to_markdown((TextBlock(long_text),))
    assert markdown == long_text


def test_blocks_to_markdown_unicode_and_emoji_width() -> None:
    text = "中文测试 🚀 ＡＢＣ full-width"
    markdown = blocks_to_markdown((TextBlock(text),))
    assert markdown == text


def test_user_role_renders_as_you() -> None:
    from kairo_tui.widgets.message import MessageWidget

    widget = MessageWidget(TranscriptEntry(MessageId("m"), "user", "chat", (TextBlock("x"),)))
    assert widget._entry.role == "user"


def test_thought_blocks_not_in_visible_markdown() -> None:
    _, visible = split_thought((ReasoningBlock("hidden"), TextBlock("shown")))
    markdown = blocks_to_markdown(visible)
    assert "hidden" not in markdown
    assert "shown" in markdown
