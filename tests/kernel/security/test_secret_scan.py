from __future__ import annotations

from pathlib import Path

import pytest

from kairo_kernel import KernelConfig, build_kernel
from kairo_kernel.contracts.identifiers import SecretId
from kairo_kernel.contracts.json import JsonObject, freeze_json
from kairo_kernel.contracts.support import SecretInput
from kairo_kernel.services import ConfigField, ConfigSchema, ConfigValueKind
from kairo_kernel.testing import secret_leaks


@pytest.mark.asyncio
async def test_public_snapshots_events_results_and_exports_never_leak_secrets(tmp_path: Path) -> None:
    secret = "sk-conformance-secret-123456789"
    values = freeze_json({"provider": {"api_key": secret}})
    assert isinstance(values, JsonObject)
    kernel = build_kernel(
        KernelConfig(
            str(tmp_path),
            database_path=str(tmp_path / "kernel.db"),
            config_values=values,
            config_schema=ConfigSchema(
                (ConfigField(("provider", "api_key"), ConfigValueKind.STRING, secret=True),)
            ),
            enable_builtin_tools=False,
        )
    )
    async with kernel:
        stored = await kernel.providers.store_secret(SecretInput(SecretId("provider"), secret))
        assert stored.value is not None
        snapshot = await kernel.configuration.snapshot()
        exported = await kernel.configuration.export_json()
        status = await kernel.status()
        replay = await kernel.events.snapshot()
        leaks = secret_leaks((stored, snapshot, exported, status, replay), (secret,))
        assert leaks == ()
