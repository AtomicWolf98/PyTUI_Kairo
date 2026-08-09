from __future__ import annotations

import asyncio
from pathlib import Path

from kairo_kernel import KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.contracts.enums import ErrorCode, LifecycleState
from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.services.config_document import (
    DocumentProviderCatalog,
    KernelConfigDocument,
    KernelConfigStore,
)
from kairo_kernel.services.providers import ProviderCatalogSnapshot, ProviderRoleMapping


def _profile(identifier: str, kind: str) -> ProviderProfile:
    return ProviderProfile(
        ProfileId(identifier), identifier, kind, "model", f"https://{kind}.example.test/v1", 32000, 2000, 0.2
    )


def _kernel(root: Path, store: KernelConfigStore):
    config = KernelConfig(
        str(root),
        database_path=str(root / "kernel.db"),
        enable_builtin_tools=False,
    )
    return build_kernel(config, KernelDependencies(provider_catalog=DocumentProviderCatalog(store)))


def test_start_restores_persisted_catalog_when_override_injected(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = KernelConfigStore(tmp_path / "config-v1.json")
        chat = _profile("openai/gpt", "openai_chat")
        role = ProviderRoleMapping("chat", chat.profile_id)
        saved = await store.save(KernelConfigDocument(profiles=(chat,), roles=(role,)))
        assert saved.ok

        kernel = _kernel(tmp_path, store)  # config.profiles is empty; the document is the source of truth
        started = await kernel.start()
        try:
            assert started.ok and started.value is LifecycleState.RUNNING
            snapshot = await kernel.providers.snapshot()
            assert snapshot.profiles == (chat,)
            assert snapshot.roles == (role,)
        finally:
            await kernel.shutdown()  # safe from CREATED; releases the aiosqlite worker thread

    asyncio.run(exercise())


def test_start_with_missing_document_yields_empty_catalog(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = KernelConfigStore(tmp_path / "config-v1.json")
        kernel = _kernel(tmp_path, store)
        started = await kernel.start()
        try:
            assert started.ok and started.value is LifecycleState.RUNNING
            assert await kernel.providers.snapshot() == ProviderCatalogSnapshot(0)
        finally:
            await kernel.shutdown()

    asyncio.run(exercise())


def test_start_with_corrupt_document_fails_typed_and_never_runs(tmp_path: Path) -> None:
    async def exercise() -> None:
        (tmp_path / "config-v1.json").write_text("{ not json", encoding="utf-8")
        store = KernelConfigStore(tmp_path / "config-v1.json")
        kernel = _kernel(tmp_path, store)
        started = await kernel.start()
        try:
            assert not started.ok and started.error is not None
            assert started.error.code is ErrorCode.CONFIG_INVALID
            assert kernel.state is not LifecycleState.RUNNING
        finally:
            await kernel.shutdown()

    asyncio.run(exercise())
