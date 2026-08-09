"""Memory page model: build_query tag parsing and new_entry construction.

Pure unit tests (no Textual); the screen tests cover the widget wiring.
"""

from __future__ import annotations

from kairo_kernel.contracts.content import TextBlock
from kairo_kernel.contracts.identifiers import MemoryId

from kairo_tui.memory_model import build_query, new_entry


def test_build_query_parses_tags_on_commas_and_whitespace() -> None:
    """Tags split on commas and whitespace; namespace/text are stripped."""
    query = build_query("  user ", "  alpha beta  ", "important, cn  high")
    assert query.namespace == "user"
    assert query.text == "alpha beta"
    assert query.limit == 20
    assert query.tags == ("important", "cn", "high")


def test_new_entry_sets_fields_and_non_null_timestamps() -> None:
    """new_entry generates a hex id, strips namespace/key, keeps tags, stamps now."""
    entry = new_entry(" user ", " memo-key ", "durable note", ("important", "cn"))
    assert entry.memory_id  # non-empty generated hex id
    assert entry.namespace == "user"
    assert entry.key == "memo-key"
    assert entry.content == (TextBlock("durable note"),)
    assert entry.tags == ("important", "cn")
    assert entry.created_at is not None
    assert entry.updated_at is not None

    preserved = new_entry("user", "k", "t", (), memory_id=MemoryId("m-42"))
    assert preserved.memory_id == MemoryId("m-42")
