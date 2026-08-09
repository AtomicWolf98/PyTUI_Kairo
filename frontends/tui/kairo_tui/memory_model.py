"""Pure view-model for the Memory page.

Textual-free: ``build_query`` parses the search inputs into a ``MemoryQuery``,
``memory_rows`` flattens search results into row previews, and ``new_entry``
builds a fresh ``MemoryEntry`` for create. Only public ``kairo_kernel.contracts``
types are touched (boundary test: no kernel-private imports).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast

from kairo_kernel.contracts.content import TextBlock
from kairo_kernel.contracts.identifiers import MemoryId
from kairo_kernel.contracts.support import MemoryEntry, MemoryQuery


def build_query(namespace: str, text: str, tags: str, limit: int = 20) -> MemoryQuery:
    """Tags input splits on commas/whitespace into the MemoryQuery tags tuple."""
    parsed = tuple(t for t in tags.replace(",", " ").split() if t)
    return MemoryQuery(namespace.strip(), text.strip(), limit, parsed)


@dataclass(frozen=True)
class MemoryRow:
    memory_id: str
    namespace: str
    key: str
    preview: str  # first 60 chars of the text content
    tags: tuple[str, ...]


def memory_rows(entries: tuple[object, ...]) -> tuple[MemoryRow, ...]:
    # Entries are MemoryEntry (public contract) — direct access is fine; the
    # cast only re-narrows for mypy while the signature stays opaque per plan.
    rows = []
    for entry in cast(tuple[MemoryEntry, ...], entries):
        text = "".join(b.text for b in entry.content if isinstance(b, TextBlock))
        rows.append(MemoryRow(str(entry.memory_id), entry.namespace, entry.key, text[:60], entry.tags))
    return tuple(rows)


def new_entry(namespace: str, key: str, text: str, tags: tuple[str, ...],
              memory_id: MemoryId | None = None) -> MemoryEntry:
    now = datetime.now(timezone.utc)
    return MemoryEntry(memory_id or MemoryId(uuid.uuid4().hex), namespace.strip(), key.strip(),
                       (TextBlock(text),), now, now, tags)
