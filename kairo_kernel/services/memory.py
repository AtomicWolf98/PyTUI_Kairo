"""Typed facade over the kernel memory port."""

from __future__ import annotations

import asyncio
from typing import TypeVar

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import MemoryId
from kairo_kernel.contracts.support import MemoryEntry, MemoryQuery
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.ports.services import MemoryPort

ResultT = TypeVar("ResultT")


class MemoryService:
    def __init__(self, memory: MemoryPort) -> None:
        self._memory = memory
        self._mutation_lock = asyncio.Lock()
        self._revision = 0

    @property
    def revision(self) -> int:
        return self._revision

    async def search(self, query: MemoryQuery) -> KernelResult[tuple[MemoryEntry, ...]]:
        if not query.namespace.strip():
            return _failure(ErrorCode.INVALID_ARGUMENT, "Memory namespace is required.", "memory.search")
        if query.limit < 0:
            return _failure(ErrorCode.INVALID_ARGUMENT, "Memory search limit cannot be negative.", "memory.search")
        try:
            return KernelResult.success(await self._memory.search(query))
        except Exception as exc:
            return _failure(ErrorCode.INTERNAL, f"Memory search failed: {exc}", "memory.search", retryable=True)

    async def get(self, memory_id: MemoryId) -> KernelResult[MemoryEntry]:
        return await self._memory.get(memory_id)

    async def put(self, entry: MemoryEntry) -> KernelResult[MemoryEntry]:
        async with self._mutation_lock:
            saved = await self._memory.put(entry)
            if saved.ok:
                self._revision += 1
            return saved

    async def delete(self, memory_id: MemoryId) -> KernelResult[bool]:
        async with self._mutation_lock:
            deleted = await self._memory.delete(memory_id)
            if deleted.ok:
                self._revision += 1
            return deleted


def _failure(
    code: ErrorCode,
    message: str,
    operation: str,
    *,
    retryable: bool = False,
) -> KernelResult[ResultT]:
    return KernelResult.failure(KernelError(code, message, retryable=retryable, operation=operation))
