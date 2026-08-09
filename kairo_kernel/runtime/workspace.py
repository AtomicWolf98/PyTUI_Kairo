"""Writer-preferring asyncio workspace read/write leases."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: str
    revision: int


class WorkspaceLease:
    def __init__(self, manager: WorkspaceLeaseManager, write: bool, snapshot: WorkspaceSnapshot) -> None:
        self._manager = manager
        self.write = write
        self.snapshot = snapshot
        self._released = False

    async def __aenter__(self) -> WorkspaceLease:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.release()

    async def release(self) -> bool:
        if self._released:
            return False
        self._released = True
        await self._manager._release(self.write)
        return True


class WorkspaceLeaseManager:
    """Keep workspace identity stable for readers and exclusive for writers."""

    def __init__(self, root: str, revision: int = 0) -> None:
        self._snapshot = WorkspaceSnapshot(root, revision)
        self._condition = asyncio.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0
        self._closed = False

    async def read(self) -> WorkspaceLease:
        async with self._condition:
            while not self._closed and (self._writer or self._waiting_writers > 0):
                await self._condition.wait()
            if self._closed:
                raise RuntimeError("Workspace lease manager is closed.")
            self._readers += 1
            return WorkspaceLease(self, False, self._snapshot)

    async def write(self) -> WorkspaceLease:
        async with self._condition:
            self._waiting_writers += 1
            try:
                while not self._closed and (self._writer or self._readers > 0):
                    await self._condition.wait()
                if self._closed:
                    raise RuntimeError("Workspace lease manager is closed.")
                self._writer = True
                return WorkspaceLease(self, True, self._snapshot)
            finally:
                self._waiting_writers -= 1
                self._condition.notify_all()

    async def update(self, lease: WorkspaceLease, root: str) -> WorkspaceSnapshot:
        async with self._condition:
            if lease._manager is not self or lease._released or not lease.write or not self._writer:
                raise RuntimeError("An active write lease is required.")
            self._snapshot = WorkspaceSnapshot(root, self._snapshot.revision + 1)
            return self._snapshot

    async def bump_revision(self) -> WorkspaceSnapshot:
        """Atomically advance the revision by one without changing the root."""
        lease = await self.write()
        async with lease:
            return await self.update(lease, lease.snapshot.root)

    async def snapshot(self) -> WorkspaceSnapshot:
        async with self._condition:
            return self._snapshot

    async def close(self) -> None:
        async with self._condition:
            self._closed = True
            self._condition.notify_all()

    async def _release(self, write: bool) -> None:
        async with self._condition:
            if write:
                if not self._writer:
                    raise RuntimeError("Workspace write lease is not active.")
                self._writer = False
            else:
                if self._readers < 1:
                    raise RuntimeError("Workspace read lease is not active.")
                self._readers -= 1
            self._condition.notify_all()
