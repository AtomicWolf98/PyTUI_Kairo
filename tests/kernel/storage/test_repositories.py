from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from kairo_kernel.contracts.content import Message, TextBlock
from kairo_kernel.contracts.enums import ErrorCode, MessageKind, MessageRole
from kairo_kernel.contracts.identifiers import MessageId, SessionId
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.support import ConfigSnapshot, SessionRecord, WorkspaceRecord
from kairo_kernel.ports.repositories import ConfigRepositoryPort, SessionRepositoryPort, WorkspaceRepositoryPort
from kairo_kernel.storage import (
    SQLiteConfigRepository,
    SQLiteDatabase,
    SQLiteSessionRepository,
    SQLiteWorkspaceRepository,
)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


def _session(identifier: str, name: str = "Session") -> SessionRecord:
    return SessionRecord(
        SessionId(identifier),
        name,
        (Message(MessageId(f"message-{identifier}"), MessageRole.SYSTEM, MessageKind.CHAT, (TextBlock("system"),)),),
        NOW,
        NOW + timedelta(seconds=1),
    )


def test_session_repository_crud_and_active_marker(tmp_path) -> None:
    async def exercise() -> None:
        database = SQLiteDatabase(tmp_path / "kernel.db")
        repository = SQLiteSessionRepository(database)
        port: SessionRepositoryPort = repository
        first = _session("1" * 32, "First")
        second = _session("2" * 32, "Second")
        assert (await port.save(first, active=True)).value == first
        assert (await port.save(second, active=True)).value == second
        assert await repository.active_session_id() == second.session_id
        assert (await port.load(first.session_id)).value == first
        summaries = await port.list()
        assert {item.session_id for item in summaries} == {first.session_id, second.session_id}
        assert all(item.message_count == 1 for item in summaries)
        deleted = await port.delete(first.session_id)
        assert deleted.value is True
        missing = await port.load(first.session_id)
        assert missing.error is not None and missing.error.code is ErrorCode.SESSION_NOT_FOUND
        await database.close()

    asyncio.run(exercise())


def test_session_repository_rejects_invalid_record(tmp_path) -> None:
    async def exercise() -> None:
        database = SQLiteDatabase(tmp_path / "kernel.db")
        repository = SQLiteSessionRepository(database)
        invalid = SessionRecord(SessionId(""), "", (), NOW, NOW)
        result = await repository.save(invalid, active=False)
        assert result.error is not None and result.error.code is ErrorCode.INVALID_ARGUMENT
        await database.close()

    asyncio.run(exercise())


def test_config_revisions_restore_and_prune(tmp_path) -> None:
    async def exercise() -> None:
        database = SQLiteDatabase(tmp_path / "kernel.db")
        repository = SQLiteConfigRepository(database)
        port: ConfigRepositoryPort = repository
        first = ConfigSnapshot(1, JsonObject.from_pairs(("model", "one")), False)
        second = ConfigSnapshot(2, JsonObject.from_pairs(("model", "two")), False)
        assert (await port.save(first)).value == first
        assert (await port.save(second)).value == second
        assert (await port.restore(1)).value == first
        assert (await port.load()).value == first
        assert (await port.save(second, create_backup=False)).value == second
        missing = await port.restore(1)
        assert missing.error is not None and missing.error.code is ErrorCode.NOT_FOUND
        await database.close()

    asyncio.run(exercise())


def test_workspace_repository_validates_applies_and_rolls_back(tmp_path) -> None:
    async def exercise() -> None:
        database = SQLiteDatabase(tmp_path / "kernel.db")
        repository = SQLiteWorkspaceRepository(database)
        port: WorkspaceRepositoryPort = repository
        assert await port.current() == WorkspaceRecord("", 0)
        validated = await port.validate(str(tmp_path))
        assert validated.ok and validated.value is not None
        assert (await port.apply(validated.value)).ok
        assert (await port.current()).root == str(tmp_path.resolve())
        previous = WorkspaceRecord(str(tmp_path), 0)
        assert (await port.rollback(previous)).value == previous
        invalid = await port.validate(str(tmp_path / "missing"))
        assert invalid.error is not None and invalid.error.code is ErrorCode.WORKSPACE_INVALID
        assert not tuple(tmp_path.glob(".kairo-write-probe-*"))
        await database.close()

    asyncio.run(exercise())
