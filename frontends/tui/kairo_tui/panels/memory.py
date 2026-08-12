"""Memory panel: search, create, edit, delete memory entries."""

from __future__ import annotations

from textual.widgets import Static

from kairo_tui.state import AppState


class MemoryPanel(Static):
    """Namespace-aware memory view; never fabricates a default namespace."""

    def render_entries(self, entries: tuple[object, ...]) -> None:
        lines = ["[b]Memory[/b]"]
        for entry in entries:
            namespace = getattr(entry, "namespace", "")
            key = getattr(entry, "key", "")
            lines.append(f"• {namespace}/{key}")
        if not entries:
            lines.append("No memory entries.")
        self.update("\n".join(lines))

    def render_state(self, state: AppState) -> None:
        self.render_entries(())
