from __future__ import annotations

import pytest

from kairo_kernel.services.capabilities import Capability, CapabilityService


class Reporter:
    def __init__(self, *items: Capability):
        self.items = items

    async def capabilities(self):
        return self.items


@pytest.mark.asyncio
async def test_public_matrix_exposes_stable_kernel_surface_and_queries_operations():
    matrix = await CapabilityService().snapshot()

    assert matrix.version == "1"
    assert matrix.supports("turns", "cancel")
    assert matrix.supports("providers", "probe")
    assert matrix.supports("skills", "trust")
    assert matrix.supports("mcp", "catalog")
    assert matrix.supports("diagnostics", "full")
    assert matrix.get("observability") is not None
    assert list(matrix.capabilities) == sorted(matrix.capabilities, key=lambda item: (item.area, item.name))


@pytest.mark.asyncio
async def test_dynamic_reporters_extend_matrix_and_internal_capabilities_are_not_public():
    reporter = Reporter(
        Capability("provider.openai", "provider", "available", ("stream", "probe"), source="openai"),
        Capability("debug.raw_events", "debug", "available", ("read",), visibility="internal"),
    )
    service = CapabilityService((reporter,))

    public = await service.snapshot()
    complete = await service.snapshot(public_only=False)

    assert public.supports("provider.openai", "stream")
    assert public.get("debug.raw_events") is None
    assert complete.get("debug.raw_events") is not None


@pytest.mark.asyncio
async def test_duplicate_invalid_status_and_invalid_visibility_are_rejected():
    duplicate = Reporter(Capability("turns", "agent", "available", ("run",)))
    invalid_status = CapabilityService(baseline=(Capability("x", "x", "maybe", ()),))
    invalid_visibility = CapabilityService(
        baseline=(Capability("x", "x", "available", (), visibility="secret"),)
    )

    with pytest.raises(ValueError, match="Duplicate"):
        await CapabilityService((duplicate,)).snapshot()
    with pytest.raises(ValueError, match="Invalid capability"):
        await invalid_status.snapshot()
    with pytest.raises(ValueError, match="visibility"):
        await invalid_visibility.snapshot()


@pytest.mark.asyncio
async def test_degraded_capability_is_visible_but_not_reported_as_supported():
    matrix = await CapabilityService(
        baseline=(Capability("mcp.docs", "integration", "degraded", ("catalog",), ("server offline",)),)
    ).snapshot()

    assert matrix.get("mcp.docs") is not None
    assert not matrix.supports("mcp.docs", "catalog")
