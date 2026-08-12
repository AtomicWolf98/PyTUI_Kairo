"""Read-only transcript for the active session."""

from __future__ import annotations

from textual.containers import VerticalScroll


class Transcript(VerticalScroll):
    """Scrollable message history; messages are rendered from events (C1)."""
