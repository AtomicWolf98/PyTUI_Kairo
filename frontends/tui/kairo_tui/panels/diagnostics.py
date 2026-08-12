"""Doctor panel: diagnostics results, redacted before display."""

from __future__ import annotations

from textual.widgets import Static


class DiagnosticsPanel(Static):
    """Shows each check's status, duration and message; secrets never shown."""

    def render_report(self, checks: tuple[object, ...]) -> None:
        lines = ["[b]Doctor[/b]"]
        for check in checks:
            name = getattr(check, "name", "")
            status = getattr(check, "status", "")
            message = getattr(check, "message", "")
            duration = getattr(check, "duration_ms", None)
            duration_text = f" ({duration}ms)" if duration is not None else ""
            lines.append(f"• {name}: {status}{duration_text} — {_redact(str(message))}")
        if not checks:
            lines.append("Run diagnostics to see results.")
        self.update("\n".join(lines))

    def render_state(self, state: object) -> None:
        self.render_report(())


def _redact(text: str) -> str:
    lower = text.lower()
    if "sk-" in lower or "api_key" in lower or "secret" in lower:
        return "[redacted]"
    return text
