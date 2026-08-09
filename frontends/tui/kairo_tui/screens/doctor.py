"""Doctor page: local/full diagnostics, per-check status, retry, cancel, copy-redacted.

Runs execute through the public ``kernel.diagnostics.local()/full()`` facade in
a ``run_worker`` whose handle is kept so Cancel can call ``worker.cancel()``:
the kernel has no per-diagnostic cancel (probes time out at 5 s each), so a
UI-level cancel stops the wait and re-renders idle. The report is consumed
structurally (the DiagnosticReport DTO is boundary-forbidden); copied reports
pass through ``redact_text`` with markers resolved from the SecretStore and the
``KAIRO_SECRET_*`` env vars, so full secret values never reach the clipboard.
"""

from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, Static
from textual.worker import Worker

from kairo_tui.doctor_model import report_to_text
from kairo_tui.redaction import redact_text, secret_markers

EMPTY_HINT = "No diagnostics run yet. Press Run local or Run full."


class DoctorScreen(Container):
    """Doctor page: per-check rows for the last local/full diagnostics run."""

    DEFAULT_CSS = """
    DoctorScreen { height: 1fr; }
    DoctorScreen #doctor-checks { height: 1fr; }
    DoctorScreen #doctor-checks Static { width: 100%; }
    DoctorScreen .check-ok { color: $success; }
    DoctorScreen .check-warning { color: $warning; }
    DoctorScreen .check-failed { color: $error; }
    DoctorScreen .check-skipped { color: $text-muted; }
    DoctorScreen Button { min-width: 0; }
    """

    def __init__(self, app) -> None:
        super().__init__(id="doctor-screen")
        self._app = app
        self.kernel = app.kernel
        self.store = app.store
        self._worker: Worker[None] | None = None
        self._scope = ""
        self._last_report = None

    def compose(self) -> ComposeResult:
        yield Static("[b]Doctor[/b]", id="doctor-title")
        with Horizontal(id="doctor-actions"):
            yield Button("Run local", id="doctor-local", variant="primary")
            yield Button("Run full", id="doctor-full", variant="primary")
            yield Button("Retry", id="doctor-retry", disabled=True)
            yield Button("Cancel", id="doctor-cancel", variant="error", disabled=True)
            yield Button("Copy redacted report", id="doctor-copy", disabled=True)
        with VerticalScroll(id="doctor-checks"):
            yield Static(EMPTY_HINT, id="doctor-empty")
        yield Static("", id="doctor-status")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "doctor-local":
            self._start_run("local")
        elif button_id == "doctor-full":
            self._start_run("full")
        elif button_id == "doctor-retry":
            if self._scope:
                self._start_run(self._scope)
        elif button_id == "doctor-cancel":
            self._cancel_run()
        elif button_id == "doctor-copy":
            self._copy_report()

    # --- Runs ---

    def _start_run(self, scope: str) -> None:
        if self._worker is not None and self._worker.is_running:
            return
        self._scope = scope
        self._set_running()
        self._worker = self.run_worker(self._run(scope), group="doctor")

    async def _run(self, scope: str) -> None:
        try:
            if scope == "full":
                result = await self.kernel.diagnostics.full()
            else:
                result = await self.kernel.diagnostics.local()
        except asyncio.CancelledError:
            self._reset_after_run(cancelled=True)
            await self._render_empty()
            raise
        except Exception as exc:  # pragma: no cover - defensive; the facade fail-closes
            if self.is_mounted:
                self._reset_after_run()
                self._notice(f"Diagnostics failed: {type(exc).__name__}: {exc}")
            return
        if not result.ok:
            if self.is_mounted:
                self._reset_after_run()
                self._notice(result.error.message if result.error else "Diagnostics failed.")
            return
        report = result.value
        if report is None or not self.is_mounted:
            return
        self._last_report = report
        self._reset_after_run()
        await self._render_report(report)

    def _cancel_run(self) -> None:
        worker = self._worker
        if worker is not None and worker.is_running:
            worker.cancel()

    # --- Rendering ---

    async def _render_report(self, report) -> None:
        container = self.query_one_optional("#doctor-checks", VerticalScroll)
        if container is None:
            return
        await container.remove_children()
        await container.mount(Static(
            f"Diagnostics ({report.mode}) — {report.status}", classes=f"check-{report.status}"
        ))
        for check in report.checks:
            await container.mount(Static(
                f"[{check.status}] {check.name} — {check.message} ({check.duration_ms:.0f} ms)",
                classes=f"check-{check.status}",
            ))

    async def _render_empty(self) -> None:
        if not self.is_mounted:
            return
        container = self.query_one_optional("#doctor-checks", VerticalScroll)
        if container is None:
            return
        await container.remove_children()
        await container.mount(Static(EMPTY_HINT, id="doctor-empty"))

    def _set_running(self) -> None:
        self.query_one("#doctor-local", Button).disabled = True
        self.query_one("#doctor-full", Button).disabled = True
        self.query_one("#doctor-retry", Button).disabled = True
        self.query_one("#doctor-cancel", Button).disabled = False
        self.query_one("#doctor-copy", Button).disabled = True
        self._notice(f"Running {self._scope} diagnostics…")

    def _reset_after_run(self, *, cancelled: bool = False) -> None:
        self._worker = None
        self.query_one("#doctor-local", Button).disabled = False
        self.query_one("#doctor-full", Button).disabled = False
        self.query_one("#doctor-retry", Button).disabled = not bool(self._scope)
        self.query_one("#doctor-cancel", Button).disabled = True
        self.query_one("#doctor-copy", Button).disabled = self._last_report is None
        if cancelled:
            self._notice("Run cancelled.")

    # --- Copy redacted report ---

    def _copy_report(self) -> None:
        report = self._last_report
        if report is None:
            return
        store = self._app._bootstrap.secret_store
        ids = tuple(p.secret_id or str(p.profile_id) for p in self.store.state.document.profiles)
        text = redact_text(report_to_text(report), secret_markers(store, ids))
        self._app.copy_to_clipboard(text)
        self._notice("Redacted report copied.")

    def _notice(self, message: str) -> None:
        status = self.query_one_optional("#doctor-status", Static)
        if status is not None:
            status.update(message)
