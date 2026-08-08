from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from kairo_kernel.contracts.content import TextBlock
from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import MemoryId
from kairo_kernel.contracts.support import MemoryEntry, MemoryQuery
from kairo_kernel.memory import SQLiteMemoryStore
from kairo_kernel.ports.services import MemoryPort
from kairo_kernel.storage import SQLiteDatabase

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


def _entry(identifier: str, key: str, text: str, tags: tuple[str, ...] = ()) -> MemoryEntry:
    return MemoryEntry(MemoryId(identifier), "user", key, (TextBlock(text),), NOW, NOW, tags)


def test_memory_put_get_search_tags_update_and_delete(tmp_path) -> None:
    async def exercise() -> None:
        database = SQLiteDatabase(tmp_path / "kernel.db")
        memory = SQLiteMemoryStore(database)
        port: MemoryPort = memory
        first = _entry("memory-1", "alpha", "中文 长期记忆", ("important", "cn"))
        second = _entry("memory-2", "beta", "English durable note", ("important",))
        assert (await port.put(first)).value == first
        assert (await port.put(second)).value == second
        assert (await port.get(first.memory_id)).value == first
        assert await port.search(MemoryQuery("user", "长期记忆")) == (first,)
        assert await port.search(MemoryQuery("user", "", tags=("cn",))) == (first,)
        updated = MemoryEntry(
            first.memory_id,
            first.namespace,
            first.key,
            (TextBlock("updated searchable text"),),
            first.created_at,
            first.updated_at + timedelta(seconds=1),
            ("updated",),
        )
        assert (await port.put(updated)).value == updated
        assert await port.search(MemoryQuery("user", "长期记忆")) == ()
        assert await port.search(MemoryQuery("user", "updated")) == (updated,)
        assert (await port.delete(first.memory_id)).value is True
        missing = await port.get(first.memory_id)
        assert missing.error is not None and missing.error.code is ErrorCode.NOT_FOUND
        await database.close()

    asyncio.run(exercise())


def test_memory_namespace_key_conflict_and_limits(tmp_path) -> None:
    async def exercise() -> None:
        database = SQLiteDatabase(tmp_path / "kernel.db")
        memory = SQLiteMemoryStore(database)
        assert (await memory.put(_entry("memory-1", "same", "one"))).ok
        conflict = await memory.put(_entry("memory-2", "same", "two"))
        assert conflict.error is not None and conflict.error.code is ErrorCode.CONFLICT
        assert await memory.search(MemoryQuery("user", "", limit=0)) == ()
        assert await memory.search(MemoryQuery("other", "one")) == ()
        await database.close()

    asyncio.run(exercise())
