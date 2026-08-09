from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.mcp import McpServerConfig
from kairo_kernel.services.config_document import (
    CONFIG_DOCUMENT_VERSION,
    DocumentProviderCatalog,
    KernelConfigDocument,
    KernelConfigStore,
    document_from_json,
    document_to_json,
)
from kairo_kernel.services.providers import ProviderCatalogSnapshot, ProviderRoleMapping

PROFILE = ProviderProfile(ProfileId("openai/gpt-test"), "GPT", "openai_chat", "gpt-test", "https://x.test/v1", 32000, 1000, 0.2)


def _document() -> KernelConfigDocument:
    return KernelConfigDocument(
        CONFIG_DOCUMENT_VERSION,
        (PROFILE,),
        (ProviderRoleMapping("chat", PROFILE.profile_id),),
        (McpServerConfig("docs", "stdio", command="mcp-docs", arguments=("--quiet",)),),
        PROFILE.profile_id,
        "dark",
        (("ctrl+k", "palette"),),
        ("C:/one", "C:/two"),
    )


def test_document_round_trips_through_plain_json() -> None:
    document = _document()
    assert document_from_json(document_to_json(document)) == document


def test_store_load_save_and_missing_file(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = KernelConfigStore(tmp_path / "config-v1.json")
        missing = await store.load()
        assert missing.error is not None and missing.error.code is ErrorCode.NOT_FOUND

        saved = await store.save(_document())
        assert saved.ok
        assert (tmp_path / "config-v1.json").is_file()
        assert not list(tmp_path.glob("*.tmp"))

        loaded = await store.load()
        assert loaded.ok and loaded.value == _document()

    asyncio.run(exercise())


def test_store_rejects_corrupt_and_unsupported_documents(tmp_path: Path) -> None:
    async def exercise() -> None:
        path = tmp_path / "config-v1.json"
        path.write_text("{not json", encoding="utf-8")
        store = KernelConfigStore(path)
        corrupt = await store.load()
        assert corrupt.error is not None and corrupt.error.code is ErrorCode.CONFIG_INVALID

        path.write_text('{"version": 99}', encoding="utf-8")
        unsupported = await store.load()
        assert unsupported.error is not None and unsupported.error.code is ErrorCode.CONFIG_INVALID

        wrong_version = KernelConfigDocument(99)
        rejected = await store.save(wrong_version)
        assert rejected.error is not None and rejected.error.code is ErrorCode.CONFIG_INVALID

    asyncio.run(exercise())


def test_store_rejects_undecodable_utf8(tmp_path: Path) -> None:
    async def exercise() -> None:
        path = tmp_path / "config-v1.json"
        path.write_bytes(b'\xff\xfe{"version": 1}')
        store = KernelConfigStore(path)
        corrupt = await store.load()
        assert corrupt.error is not None and corrupt.error.code is ErrorCode.CONFIG_INVALID

    asyncio.run(exercise())


def test_document_provider_catalog_persists_and_preserves_other_fields(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = KernelConfigStore(tmp_path / "config-v1.json")
        await store.save(_document())
        catalog = DocumentProviderCatalog(store)

        extra = ProviderProfile(
            ProfileId("anthropic/claude"), "Claude", "anthropic", "claude", "https://a.test/v1", 200000, 8000, 0.2
        )
        snapshot = ProviderCatalogSnapshot(1, (PROFILE, extra), (ProviderRoleMapping("chat", PROFILE.profile_id),))
        saved = await catalog.save(snapshot)
        assert saved.ok

        loaded = await catalog.load()
        assert loaded.ok and loaded.value is not None
        assert tuple(profile.profile_id for profile in loaded.value.profiles) == (PROFILE.profile_id, extra.profile_id)

        document = (await store.load()).value
        assert document is not None
        assert document.theme == "dark"  # non-catalog fields survive catalog saves
        assert len(document.profiles) == 2

    asyncio.run(exercise())


def test_document_load_of_empty_catalog_returns_zero_snapshot(tmp_path: Path) -> None:
    async def exercise() -> None:
        catalog = DocumentProviderCatalog(KernelConfigStore(tmp_path / "missing.json"))
        loaded = await catalog.load()
        assert loaded.ok and loaded.value is not None
        assert loaded.value.revision == 0 and loaded.value.profiles == ()

    asyncio.run(exercise())


def test_store_update_first_write_advances_document_revision(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = KernelConfigStore(tmp_path / "config-v1.json")
        updated = await store.update(0, lambda document: replace(document, theme="dark"))
        assert updated.ok and updated.value == 1
        loaded = (await store.load()).value
        assert loaded is not None
        assert loaded.revision == 1 and loaded.theme == "dark"
        assert document_to_json(loaded)["revision"] == 1

    asyncio.run(exercise())


def test_store_update_stale_expected_revision_is_conflict(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = KernelConfigStore(tmp_path / "config-v1.json")
        assert (await store.update(0, lambda document: document)).ok
        conflict = await store.update(0, lambda document: document)
        assert conflict.error is not None and conflict.error.code is ErrorCode.CONFLICT
        loaded = (await store.load()).value
        assert loaded is not None and loaded.revision == 1

    asyncio.run(exercise())


def test_store_update_concurrent_writers_one_wins_one_conflicts(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = KernelConfigStore(tmp_path / "config-v1.json")
        first = asyncio.create_task(store.update(0, lambda document: replace(document, theme="one")))
        second = asyncio.create_task(store.update(0, lambda document: replace(document, theme="two")))
        results = await asyncio.gather(first, second)
        codes = sorted(result.error.code.value if result.error is not None else "ok" for result in results)
        assert codes == ["conflict", "ok"]
        loaded = (await store.load()).value
        assert loaded is not None and loaded.revision == 1
        assert loaded.theme in ("one", "two")

    asyncio.run(exercise())


def test_store_update_rejects_invalid_transforms(tmp_path: Path) -> None:
    def broken(_document: KernelConfigDocument) -> KernelConfigDocument:
        raise ValueError("transform exploded")

    def wrong_type(_document: KernelConfigDocument) -> KernelConfigDocument:
        return "not a document"  # type: ignore[return-value]

    async def exercise() -> None:
        store = KernelConfigStore(tmp_path / "config-v1.json")
        exploded = await store.update(0, broken)
        assert exploded.error is not None and exploded.error.code is ErrorCode.CONFIG_INVALID
        mistyped = await store.update(0, wrong_type)
        assert mistyped.error is not None and mistyped.error.code is ErrorCode.CONFIG_INVALID
        assert (await store.load()).error is not None  # nothing was persisted

    asyncio.run(exercise())
