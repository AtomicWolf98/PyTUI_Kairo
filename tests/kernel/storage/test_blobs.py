from __future__ import annotations

import asyncio

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import ResourceId
from kairo_kernel.storage import BlobStore


def test_blob_store_is_content_addressed_and_deduplicated(tmp_path) -> None:
    async def exercise() -> None:
        store = BlobStore(tmp_path)
        first = await store.put(b"hello", name="hello.txt", media_type="text/plain")
        second = await store.put(b"hello")
        assert first.ok and first.value is not None
        assert second.value is not None
        assert first.value.resource_id == second.value.resource_id
        assert first.value.sha256 == str(first.value.resource_id)
        assert (await store.get(first.value.resource_id)).value == b"hello"
        assert await store.exists(first.value.resource_id)
        assert len(tuple(tmp_path.rglob(first.value.sha256[2:]))) == 1

    asyncio.run(exercise())


def test_blob_store_rejects_invalid_id_and_detects_corruption(tmp_path) -> None:
    async def exercise() -> None:
        store = BlobStore(tmp_path)
        invalid = await store.get(ResourceId("../escape"))
        assert invalid.error is not None and invalid.error.code is ErrorCode.INVALID_ARGUMENT
        stored = await store.put(b"original")
        assert stored.value is not None
        digest = stored.value.sha256
        (tmp_path / digest[:2] / digest[2:]).write_bytes(b"corrupt")
        corrupt = await store.get(stored.value.resource_id)
        assert corrupt.error is not None and corrupt.error.code is ErrorCode.INTERNAL

    asyncio.run(exercise())


def test_blob_delete_reports_missing(tmp_path) -> None:
    async def exercise() -> None:
        store = BlobStore(tmp_path)
        stored = await store.put(b"remove")
        assert stored.value is not None
        assert (await store.delete(stored.value.resource_id)).value is True
        missing = await store.delete(stored.value.resource_id)
        assert missing.error is not None and missing.error.code is ErrorCode.NOT_FOUND

    asyncio.run(exercise())
