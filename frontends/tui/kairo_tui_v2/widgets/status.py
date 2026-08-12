"""Single-line status bar."""

from __future__ import annotations

from textual.widgets import Static


class StatusLine(Static):
    """Fixed one-line footer with mode and shortcut hints."""
