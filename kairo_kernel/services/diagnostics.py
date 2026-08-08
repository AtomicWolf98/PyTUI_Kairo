"""Composable local and full kernel health diagnostics."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Protocol

from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.errors import KernelResult


@dataclass(frozen=True)
class ProbeResult:
    status: str
    message: str
    details: tuple[tuple[str, str], ...] = ()

    @classmethod
    def healthy(cls, message: str = "Healthy.", **details: str) -> ProbeResult:
        return cls("ok", message, tuple(sorted(details.items())))

    @classmethod
    def degraded(cls, message: str, **details: str) -> ProbeResult:
        return cls("warning", message, tuple(sorted(details.items())))

    @classmethod
    def unhealthy(cls, message: str, **details: str) -> ProbeResult:
        return cls("failed", message, tuple(sorted(details.items())))


class DiagnosticProbePort(Protocol):
    @property
    def name(self) -> str: ...

    async def probe(self) -> ProbeResult: ...


class ProviderProbePort(DiagnosticProbePort, Protocol):
    """Marker port for provider connectivity probes."""


class McpProbePort(DiagnosticProbePort, Protocol):
    """Marker port for MCP server/catalog probes."""


class ProviderHealthPort(Protocol):
    async def probe(self, profile_id: ProfileId) -> KernelResult[ProviderProfile]: ...


@dataclass(frozen=True)
class DiagnosticCheck:
    name: str
    category: str
    status: str
    message: str
    duration_ms: float
    details: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class DiagnosticReport:
    mode: str
    checks: tuple[DiagnosticCheck, ...]
    duration_ms: float

    @property
    def healthy(self) -> bool:
        return all(check.status in {"ok", "skipped"} for check in self.checks)

    @property
    def status(self) -> str:
        statuses = {check.status for check in self.checks}
        if "failed" in statuses:
            return "failed"
        if "warning" in statuses:
            return "warning"
        return "ok"


@dataclass(frozen=True)
class DiagnosticDependencies:
    queue: DiagnosticProbePort | None = None
    worker: DiagnosticProbePort | None = None
    lease: DiagnosticProbePort | None = None
    database: DiagnosticProbePort | None = None
    replay: DiagnosticProbePort | None = None
    blob: DiagnosticProbePort | None = None
    memory: DiagnosticProbePort | None = None
    providers: tuple[ProviderProbePort, ...] = ()
    mcp_servers: tuple[McpProbePort, ...] = ()


class DiagnosticService:
    """Run deterministic, bounded checks without owning concrete infrastructure."""

    _LOCAL_CATEGORIES = ("queue", "worker", "lease", "database", "replay", "blob", "memory")

    def __init__(self, dependencies: DiagnosticDependencies, *, timeout_seconds: float = 5.0):
        if timeout_seconds <= 0:
            raise ValueError("Diagnostic timeout must be positive.")
        self.dependencies = dependencies
        self.timeout_seconds = timeout_seconds

    async def local(self) -> DiagnosticReport:
        return await self._run("local", include_external=False)

    async def full(self) -> DiagnosticReport:
        return await self._run("full", include_external=True)

    async def _run(self, mode: str, *, include_external: bool) -> DiagnosticReport:
        started = time.perf_counter()
        work: list[tuple[str, DiagnosticProbePort | None]] = [
            (category, getattr(self.dependencies, category)) for category in self._LOCAL_CATEGORIES
        ]
        if include_external:
            work.extend(("provider", probe) for probe in self.dependencies.providers)
            work.extend(("mcp", probe) for probe in self.dependencies.mcp_servers)
        checks = await asyncio.gather(*(self._check(category, probe) for category, probe in work))
        return DiagnosticReport(mode, tuple(checks), _elapsed_ms(started))

    async def _check(self, category: str, probe: DiagnosticProbePort | None) -> DiagnosticCheck:
        if probe is None:
            return DiagnosticCheck(category, category, "skipped", "Probe is not configured.", 0)
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(probe.probe(), timeout=self.timeout_seconds)
        except TimeoutError:
            return DiagnosticCheck(
                probe.name,
                category,
                "failed",
                f"Probe timed out after {self.timeout_seconds:g}s.",
                _elapsed_ms(started),
            )
        except Exception as error:
            return DiagnosticCheck(probe.name, category, "failed", _safe_error(error), _elapsed_ms(started))
        status = result.status if result.status in {"ok", "warning", "failed", "skipped"} else "failed"
        message = result.message if status == result.status else f"Probe returned invalid status '{result.status}'."
        return DiagnosticCheck(probe.name, category, status, message, _elapsed_ms(started), result.details)


class ProviderProfileProbe:
    def __init__(self, name: str, provider: ProviderHealthPort, profile_id: ProfileId):
        self.name = name
        self.provider = provider
        self.profile_id = profile_id

    async def probe(self) -> ProbeResult:
        result = await self.provider.probe(self.profile_id)
        if result.ok and result.value is not None:
            return ProbeResult.healthy(model=result.value.model, provider=result.value.provider)
        message = result.error.message if result.error is not None else "Provider probe failed."
        return ProbeResult.unhealthy(message)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _safe_error(error: Exception) -> str:
    name = type(error).__name__
    return f"{name}: probe failed."
