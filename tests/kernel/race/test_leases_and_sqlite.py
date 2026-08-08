from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kairo_kernel.contracts.identifiers import SessionId
from kairo_kernel.contracts.support import SessionRecord
from kairo_kernel.storage import SQLiteDatabase, SQLiteSessionRepository
from kairo_kernel.testing import ConformanceHarness


@pytest.mark.asyncio
async def test_turn_cancellation_releases_session_lease_for_next_turn(tmp_path: Path) -> None:
    harness = ConformanceHarness.create(tmp_path, session_count=1)
    async with harness.kernel:
        report = await harness.run(rounds=2)
        assert report.submitted == 2
        assert all(count == 1 for _, count in report.terminal_counts)
        status = await harness.kernel.status()
        assert status.active_turn_id is None


@pytest.mark.asyncio
async def test_sqlite_contention_serializes_and_commits_all_sessions(tmp_path: Path) -> None:
    database = await SQLiteDatabase(tmp_path / "contention.db").open()
    repository = SQLiteSessionRepository(database)
    now = datetime.now(timezone.utc)
    blocker_entered = asyncio.Event()

    async def hold_writer() -> None:
        async with database.write():
            blocker_entered.set()
            await asyncio.sleep(0.02)

    blocker = asyncio.create_task(hold_writer())
    await blocker_entered.wait()
    records = tuple(SessionRecord(SessionId(f"s-{index}"), f"Session {index}", (), now, now) for index in range(40))
    saves = await asyncio.gather(*(repository.save(record, active=False) for record in records))
    await blocker
    assert all(result.ok for result in saves)
    listed = await repository.list()
    assert {item.session_id for item in listed} == {record.session_id for record in records}
    await database.close()
