"""DoctorScreen pilot tests: local/full runs, per-check status, retry, cancel,
copy-redacted report, and the empty-report (idle) state.

The app is bootstrapped synchronously (outside any event loop) and driven via
the Pilot inside ``asyncio.run`` — the same pattern as test_settings_screen.py.
Diagnostics are exercised "all real": the booted kernel's public
``kernel.diagnostics.local()/full()`` facade is driven with a real
``DiagnosticService`` whose probes are swapped in through the kernel's own
composition seam (``diagnostics._service`` — the same post-bootstrap seam
swap test_extensions_screen.py uses for the MCP hub), so the screen cannot
tell the difference and the facade/KernelResult path is fully covered.

Count note: the brief enumerates 10 behaviors; the "empty-report state renders"
behavior is asserted on both sides of the lifecycle (initial idle state in
``test_local_run...`` and post-cancel idle state in ``test_cancel...``), which
folds it into two pilot tests rather than a standalone one — 4 unit + 5 pilot
= 9 new tests (205 → 214). See .superpowers/sdd/wb-task-7-report.md.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest
from kairo_kernel.contracts.identifiers import ProfileId, SecretId
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.services.diagnostics import (
    DiagnosticDependencies,
    DiagnosticService,
    ProbeResult,
    ProviderProfileProbe,
)
from textual.widgets import Button, Static

from kairo_tui.app import KairoTuiApp
from kairo_tui.bootstrap import BootstrapOptions, build_running_kernel
from kairo_tui.config_document import ConfigDocument, ConfigDocumentAdapter, RoleMapping
from kairo_tui.doctor_model import DiagnosticReportLike, report_to_text
from kairo_tui.keyring_store import SecretStore
from tests.support.fakes import NOW_PROFILE, FakeProvider

MARKER = "sk-very-secret-marker-9f2c"


# --- TUI-local report stand-ins (DiagnosticReport is boundary-forbidden) ---


class _FakeCheck:
    def __init__(
        self,
        status: str,
        name: str,
        category: str,
        message: str,
        duration_ms: float,
        details: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.status = status
        self.name = name
        self.category = category
        self.message = message
        self.duration_ms = duration_ms
        self.details = details


class _FakeReport:
    def __init__(
        self, mode: str, status: str, checks: tuple[_FakeCheck, ...], duration_ms: float
    ) -> None:
        self.mode = mode
        self.status = status
        self.checks = checks
        self.duration_ms = duration_ms


# --- Diagnostic probes (real DiagnosticService consumers) ---


class _Probe:
    def __init__(self, name: str, result: ProbeResult, *, delay: float = 0) -> None:
        self.name = name
        self.result = result
        self.delay = delay
        self.calls = 0

    async def probe(self) -> ProbeResult:
        self.calls += 1
        await asyncio.sleep(self.delay)
        return self.result


class _BlockingProbe:
    """A probe that never completes on its own; only cancel() can stop it."""

    name = "blocking"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def probe(self) -> ProbeResult:
        self.started.set()
        await self.release.wait()
        return ProbeResult.healthy()


class _MemoryBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


# --- Fixture ---


@pytest.fixture
def doctor_app_factory(workspace: Path):
    """A booted KairoTuiApp on the Chat page with a seeded config.

    ``service`` (a real ``DiagnosticService``) is swapped into the booted
    kernel through the same post-bootstrap seam the kernel factory exposes as
    ``KernelDependencies.diagnostics``, so ``kernel.diagnostics.local()/full()``
    run through the real public facade.
    """

    def make(*, service: DiagnosticService | None = None, secret_store: SecretStore | None = None,
             document: ConfigDocument | None = None) -> KairoTuiApp:
        document = document or ConfigDocument(
            profiles=(NOW_PROFILE,),
            roles=(RoleMapping("chat", NOW_PROFILE.profile_id),),
            default_profile_id=NOW_PROFILE.profile_id,
        )
        ConfigDocumentAdapter(workspace.parent / "config-v1.json").save(document)
        bootstrap = build_running_kernel(
            BootstrapOptions(workspace_root=str(workspace), config_path=workspace.parent / "config-v1.json"),
            secret_store=secret_store or SecretStore(None),
            provider=FakeProvider(),
        )
        app = KairoTuiApp(bootstrap)
        if service is not None:
            app.kernel.diagnostics._service = service
        return app

    return make


async def _wait_for(pilot, predicate, *, polls: int = 80, delay: float = 0.05) -> None:
    for _ in range(polls):
        await pilot.pause(delay)
        if predicate():
            return


async def _open_doctor(pilot, app: KairoTuiApp) -> None:
    await pilot.press("ctrl+7")
    await pilot.pause()
    await _wait_for(pilot, lambda: app.query_one_optional("#doctor-screen") is not None)


def _checks_text(app: KairoTuiApp) -> str:
    rows = app.query_one("#doctor-checks")
    return " ".join(str(item.content) for item in rows.query(Static))


def _status_text(app: KairoTuiApp) -> str:
    return str(app.query_one("#doctor-status", Static).content)


# --- Unit: report_to_text ---


def test_report_to_text_renders_mode_status_and_check_lines() -> None:
    report = _FakeReport(
        mode="local",
        status="warning",
        checks=(
            _FakeCheck("ok", "queue-depth", "queue", "Healthy.", 1.0, (("depth", "0"),)),
            _FakeCheck("failed", "database", "database", "Timeout.", 5000.0, ()),
        ),
        duration_ms=5001.0,
    )
    # The fake report is a TUI-local stand-in: the kernel DiagnosticReport DTO is
    # boundary-forbidden, so the cast marks the structural hand-off explicitly.
    text = report_to_text(cast(DiagnosticReportLike, report))
    assert text.startswith("Kairo diagnostics (local) — warning")
    assert "[ok] queue-depth (queue) — Healthy. (1 ms)" in text
    assert "    depth: 0" in text
    assert "[failed] database (database) — Timeout. (5000 ms)" in text
    assert text.endswith("Total: 5001 ms")


# --- Pilot: local run ---


def test_local_run_renders_check_rows_with_per_check_status(doctor_app_factory) -> None:
    service = DiagnosticService(DiagnosticDependencies(
        queue=_Probe("queue-depth", ProbeResult.healthy(depth="0")),
        worker=_Probe("worker-heartbeat", ProbeResult.degraded("Slow response.")),
        database=_Probe("database", ProbeResult.unhealthy("Down.")),
    ))
    app = doctor_app_factory(service=service)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_doctor(pilot, app)
            # The empty-report state renders before any run.
            assert app.query_one_optional("#doctor-empty") is not None
            assert "No diagnostics" in _checks_text(app)
            await pilot.click("#doctor-local")
            await _wait_for(pilot, lambda: "[ok] queue-depth" in _checks_text(app))
            text = _checks_text(app)
            assert "Diagnostics (local) — failed" in text
            assert "[ok] queue-depth" in text
            assert "[warning] worker-heartbeat" in text
            assert "[failed] database" in text
            assert "[skipped] lease" in text
            # Per-check status is also styled via CSS classes.
            assert app.query_one("#doctor-checks").query(".check-ok")
            assert app.query_one("#doctor-checks").query(".check-failed")

    asyncio.run(drive())


# --- Pilot: full run ---


def test_full_run_renders_provider_ok_and_no_mcp(doctor_app_factory) -> None:
    service = DiagnosticService(DiagnosticDependencies(
        queue=_Probe("queue-depth", ProbeResult.healthy(depth="0")),
        providers=(ProviderProfileProbe("fake", FakeProvider(), NOW_PROFILE.profile_id),),
    ))
    app = doctor_app_factory(service=service)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_doctor(pilot, app)
            await pilot.click("#doctor-full")
            await _wait_for(pilot, lambda: "[ok] fake" in _checks_text(app))
            text = _checks_text(app)
            assert "Diagnostics (full) — ok" in text
            assert "[ok] fake" in text  # the FakeProvider probe reports ok
            assert "mcp" not in text    # no MCP servers configured

    asyncio.run(drive())


# --- Pilot: retry ---


def test_retry_reruns_the_same_scope(doctor_app_factory) -> None:
    probe = _Probe("queue-depth", ProbeResult.healthy(depth="0"))
    service = DiagnosticService(DiagnosticDependencies(queue=probe))
    app = doctor_app_factory(service=service)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_doctor(pilot, app)
            await pilot.click("#doctor-local")
            await _wait_for(pilot, lambda: "[ok] queue-depth" in _checks_text(app))
            assert probe.calls == 1
            await pilot.click("#doctor-retry")
            await _wait_for(pilot, lambda: probe.calls == 2)
            assert "Diagnostics (local)" in _checks_text(app)  # same scope re-ran
            assert "[ok] queue-depth" in _checks_text(app)

    asyncio.run(drive())


# --- Pilot: cancel (stops the worker, re-renders the empty-report state) ---


def test_cancel_stops_a_running_run_and_renders_idle(doctor_app_factory) -> None:
    blocking = _BlockingProbe()
    service = DiagnosticService(DiagnosticDependencies(queue=blocking), timeout_seconds=30)
    app = doctor_app_factory(service=service)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_doctor(pilot, app)
            cancel = app.query_one("#doctor-cancel", Button)
            assert cancel.disabled
            await pilot.click("#doctor-full")
            await _wait_for(pilot, lambda: blocking.started.is_set())
            assert not app.query_one("#doctor-cancel", Button).disabled
            await pilot.click("#doctor-cancel")
            # The cancelled worker finishes and the screen re-renders idle.
            await _wait_for(pilot, lambda: app.query_one("#doctor-cancel", Button).disabled)
            assert "cancelled" in _status_text(app).casefold()
            # The empty-report state renders again after the cancel.
            assert app.query_one_optional("#doctor-empty") is not None

    asyncio.run(drive())


# --- Pilot: copy-redacted report ---


def test_copy_redacts_secret_marker_from_report(doctor_app_factory) -> None:
    backend = _MemoryBackend()
    store = SecretStore(backend)
    store.store(SecretId("openai"), MARKER)
    profile = ProviderProfile(
        ProfileId("openai/gpt"), "OpenAI", "openai_responses", "gpt-5.2",
        "https://api.openai.com/v1", 32000, 1000, 0.2, secret_id="openai",
    )
    document = ConfigDocument(profiles=(profile,), default_profile_id=profile.profile_id)
    service = DiagnosticService(DiagnosticDependencies(
        queue=_Probe("auth", ProbeResult.unhealthy(f"authentication failed: {MARKER}")),
    ))
    app = doctor_app_factory(service=service, secret_store=store, document=document)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_doctor(pilot, app)
            await pilot.click("#doctor-local")
            await _wait_for(pilot, lambda: "[failed] auth" in _checks_text(app))
            assert MARKER in _checks_text(app)  # the report carries the secret text
            await pilot.click("#doctor-copy")
            await _wait_for(pilot, lambda: "Redacted report copied." in _status_text(app))
            assert MARKER not in app._clipboard
            assert "********" in app._clipboard

    asyncio.run(drive())
