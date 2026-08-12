"""Single-line status bar: model label, turn state, transient notice."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static


class StatusLine(Static):
    """Fixed one-line footer with mode and shortcut hints."""

    def render_status(self, model_label: str, notice: str, stopping: bool) -> None:
        parts = [f"{model_label}"]
        if stopping:
            parts.append("Stopping…")
        if notice:
            parts.append(notice)
        parts.append("Ctrl+P commands · Ctrl+X shortcuts · Esc stop")
        self.update(Text(" · ".join(parts)))
