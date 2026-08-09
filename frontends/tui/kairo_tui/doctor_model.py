"""Pure view-model for the Doctor page.

Textual-free formatter for a diagnostics report. The kernel's
``DiagnosticReport`` DTO lives in a boundary-forbidden module, so the TUI
consumes it structurally through ``DiagnosticReportLike`` / ``CheckLike``
Protocols (the real dataclasses satisfy them structurally).
"""

from __future__ import annotations

from typing import Protocol


class CheckLike(Protocol):
    status: str
    name: str
    category: str
    message: str
    duration_ms: float
    details: tuple[tuple[str, str], ...]


class DiagnosticReportLike(Protocol):
    mode: str
    status: str
    checks: tuple[CheckLike, ...]
    duration_ms: float


def report_to_text(report: DiagnosticReportLike) -> str:
    lines = [f"Kairo diagnostics ({report.mode}) — {report.status}", ""]
    for check in report.checks:
        lines.append(
            f"[{check.status}] {check.name} ({check.category}) — {check.message}"
            f" ({check.duration_ms:.0f} ms)"
        )
        for key, value in check.details:
            lines.append(f"    {key}: {value}")
    lines.append("")
    lines.append(f"Total: {report.duration_ms:.0f} ms")
    return "\n".join(lines)
