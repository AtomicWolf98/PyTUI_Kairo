"""Extensions panel: skills inventory and MCP catalog."""

from __future__ import annotations

from textual.widgets import Static


class ExtensionsPanel(Static):
    """Read-only skill/MCP overview; trust requires explicit confirmation."""

    def render_inventory(self, skills: tuple[object, ...], mcp_catalog: tuple[object, ...]) -> None:
        lines = ["[b]Extensions[/b]", "[b]Skills[/b]"]
        for skill in skills:
            name = getattr(skill, "name", "")
            trusted = getattr(skill, "trusted", False)
            lines.append(f"• {name} {'[green]trusted[/green]' if trusted else '[yellow]untrusted[/yellow]'}")
        if not skills:
            lines.append("No skills loaded.")
        lines.append("[b]MCP servers[/b]")
        for entry in mcp_catalog:
            lines.append(f"• {getattr(entry, 'server_name', '')}")
        if not mcp_catalog:
            lines.append("No MCP servers connected.")
        self.update("\n".join(lines))
