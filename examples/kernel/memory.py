from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from tempfile import TemporaryDirectory

from kairo_kernel import KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.contracts.content import TextBlock
from kairo_kernel.contracts.identifiers import MemoryId
from kairo_kernel.contracts.support import MemoryEntry, MemoryQuery

from ._support import EchoProvider, value


async def main() -> None:
    with TemporaryDirectory(prefix="kairo-memory-") as workspace:
        kernel = build_kernel(
            KernelConfig(workspace, database_path=":memory:", enable_builtin_tools=False),
            KernelDependencies(provider=EchoProvider()),
        )
        async with kernel:
            now = datetime.now(timezone.utc)
            entry = MemoryEntry(
                MemoryId("example-memory"),
                "project",
                "kernel-notes",
                (TextBlock("Kairo memory uses SQLite FTS5."),),
                now,
                now,
                ("docs",),
            )
            value(await kernel.memory.put(entry))
            matches = value(await kernel.memory.search(MemoryQuery("project", "SQLite", tags=("docs",))))
            assert matches == (entry,)
            assert value(await kernel.memory.delete(entry.memory_id))


if __name__ == "__main__":
    asyncio.run(main())
