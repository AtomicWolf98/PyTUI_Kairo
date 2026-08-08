from __future__ import annotations

import asyncio

import pytest

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.services.diagnostics import (
    DiagnosticDependencies,
    DiagnosticService,
    ProbeResult,
    ProviderProfileProbe,
)


class Probe:
    def __init__(self, name: str, result: ProbeResult, *, delay: float = 0, error: Exception | None = None):
        self.name = name
        self.result = result
        self.delay = delay
        self.error = error
        self.calls = 0

    async def probe(self) -> ProbeResult:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.result


class Provider:
    def __init__(self, result):
        self.result = result

    async def probe(self, profile_id):
        return self.result


def local_dependencies() -> DiagnosticDependencies:
    return DiagnosticDependencies(
        queue=Probe("queue-depth", ProbeResult.healthy(depth="0")),
        worker=Probe("worker-heartbeat", ProbeResult.healthy(workers="2")),
        lease=Probe("lease-owner", ProbeResult.healthy(owner="worker-1")),
        database=Probe("database", ProbeResult.healthy(schema="3")),
        replay=Probe("event-replay", ProbeResult.healthy(lag="0")),
        blob=Probe("blob-store", ProbeResult.healthy(objects="4")),
        memory=Probe("memory-index", ProbeResult.healthy(entries="12")),
    )


@pytest.mark.asyncio
async def test_local_doctor_covers_runtime_storage_replay_blob_and_memory_only():
    provider = Probe("provider-a", ProbeResult.healthy())
    mcp = Probe("mcp-docs", ProbeResult.healthy())
    dependencies = local_dependencies()
    dependencies = DiagnosticDependencies(
        dependencies.queue,
        dependencies.worker,
        dependencies.lease,
        dependencies.database,
        dependencies.replay,
        dependencies.blob,
        dependencies.memory,
        (provider,),
        (mcp,),
    )

    report = await DiagnosticService(dependencies).local()

    assert report.mode == "local"
    assert report.status == "ok"
    assert [check.category for check in report.checks] == [
        "queue",
        "worker",
        "lease",
        "database",
        "replay",
        "blob",
        "memory",
    ]
    assert provider.calls == mcp.calls == 0


@pytest.mark.asyncio
async def test_full_doctor_adds_provider_and_mcp_probes_concurrently():
    dependencies = local_dependencies()
    provider = Probe("provider-a", ProbeResult.degraded("Slow response."), delay=0.03)
    mcp = Probe("mcp-docs", ProbeResult.healthy(), delay=0.03)
    dependencies = DiagnosticDependencies(
        dependencies.queue,
        dependencies.worker,
        dependencies.lease,
        dependencies.database,
        dependencies.replay,
        dependencies.blob,
        dependencies.memory,
        (provider,),
        (mcp,),
    )

    report = await DiagnosticService(dependencies).full()

    assert report.status == "warning"
    assert [check.category for check in report.checks[-2:]] == ["provider", "mcp"]
    assert report.duration_ms < 60


@pytest.mark.asyncio
async def test_missing_timeout_exception_and_invalid_status_are_fail_closed_and_redacted():
    dependencies = DiagnosticDependencies(
        queue=None,
        worker=Probe("slow", ProbeResult.healthy(), delay=0.03),
        lease=Probe("crash", ProbeResult.healthy(), error=RuntimeError("Bearer secret-token")),
        database=Probe("invalid", ProbeResult("mystery", "not valid")),
    )

    report = await DiagnosticService(dependencies, timeout_seconds=0.005).local()

    assert report.checks[0].status == "skipped"
    assert report.checks[1].status == "failed"
    assert "timed out" in report.checks[1].message
    assert report.checks[2].message == "RuntimeError: probe failed."
    assert "secret-token" not in report.checks[2].message
    assert report.checks[3].status == "failed"


@pytest.mark.asyncio
async def test_provider_port_adapter_normalizes_kernel_results():
    profile = ProviderProfile(ProfileId("p/model"), "Model", "p", "m", "https://example", 1, 1, 0)
    healthy = ProviderProfileProbe("p", Provider(KernelResult.success(profile)), profile.profile_id)
    failed = ProviderProfileProbe(
        "p",
        Provider(KernelResult.failure(KernelError(ErrorCode.PROVIDER_AUTH, "bad key"))),
        profile.profile_id,
    )

    assert (await healthy.probe()).details == (("model", "m"), ("provider", "p"))
    assert (await failed.probe()).status == "failed"
