from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from kairo_kernel.contracts.content import TextBlock
from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import MemoryId
from kairo_kernel.contracts.support import MemoryEntry, MemoryQuery
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.services.memory import MemoryService

NOW = datetime(2026, 8, 8, tzinfo=timezone.utc)


def _entry(identifier: str = "one", text: str = "remember this") -> MemoryEntry:
    return MemoryEntry(MemoryId(identifier), "workspace", identifier, (TextBlock(text),), NOW, NOW, ("tag",))


class FakeMemory:
    def __init__(self) -> None:
        self.entries: dict[MemoryId, MemoryEntry] = {}
        self.fail_put = False
        self.fail_delete = False
        self.fail_search = False

    async def search(self, query: MemoryQuery) -> tuple[MemoryEntry, ...]:
        if self.fail_search:
            raise OSError("search failed")
        return tuple(
            entry
            for entry in self.entries.values()
            if entry.namespace == query.namespace
            and query.text.casefold() in entry.to_json().casefold()
            and all(tag in entry.tags for tag in query.tags)
        )[: query.limit]

    async def get(self, memory_id: MemoryId) -> KernelResult[MemoryEntry]:
        entry = self.entries.get(memory_id)
        if entry is None:
            return KernelResult.failure(KernelError(ErrorCode.NOT_FOUND, "missing"))
        return KernelResult.success(entry)

    async def put(self, entry: MemoryEntry) -> KernelResult[MemoryEntry]:
        if self.fail_put:
            return KernelResult.failure(KernelError(ErrorCode.INTERNAL, "save failed"))
        self.entries[entry.memory_id] = entry
        return KernelResult.success(entry)

    async def delete(self, memory_id: MemoryId) -> KernelResult[bool]:
        if self.fail_delete:
            return KernelResult.failure(KernelError(ErrorCode.INTERNAL, "delete failed"))
        if memory_id not in self.entries:
            return KernelResult.failure(KernelError(ErrorCode.NOT_FOUND, "missing"))
        del self.entries[memory_id]
        return KernelResult.success(True)


def test_memory_crud_search_and_revision() -> None:
    async def exercise() -> None:
        memory = FakeMemory()
        service = MemoryService(memory)
        entry = _entry()
        put = await service.put(entry)
        assert put.value == entry and service.revision == 1
        got = await service.get(entry.memory_id)
        assert got.value == entry
        found = await service.search(MemoryQuery("workspace", "remember", tags=("tag",)))
        assert found.value == (entry,)
        deleted = await service.delete(entry.memory_id)
        assert deleted.value is True and service.revision == 2

    asyncio.run(exercise())


def test_memory_failures_are_typed_and_do_not_advance_revision() -> None:
    async def exercise() -> None:
        memory = FakeMemory()
        service = MemoryService(memory)
        memory.fail_put = True
        failed_put = await service.put(_entry())
        assert failed_put.error is not None and service.revision == 0 and not memory.entries
        memory.fail_search = True
        failed_search = await service.search(MemoryQuery("workspace", "x"))
        assert failed_search.error is not None and failed_search.error.code is ErrorCode.INTERNAL
        invalid = await service.search(MemoryQuery("", "x"))
        assert invalid.error is not None and invalid.error.code is ErrorCode.INVALID_ARGUMENT

    asyncio.run(exercise())
