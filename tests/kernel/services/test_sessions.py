from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from kairo_kernel.contracts.content import Message, TextBlock
from kairo_kernel.contracts.enums import ErrorCode, MessageKind, MessageRole
from kairo_kernel.contracts.identifiers import MessageId, SessionId, TurnId
from kairo_kernel.contracts.support import SessionRecord, SessionSummary
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.runtime.turns import SessionTurnSupervisor
from kairo_kernel.services.sessions import SessionService

NOW = datetime(2026, 8, 8, tzinfo=timezone.utc)


def _record(identifier: str, name: str = "Alpha", text: str = "needle") -> SessionRecord:
    message = Message(MessageId(f"m-{identifier}"), MessageRole.SYSTEM, MessageKind.CHAT, (TextBlock(text),))
    return SessionRecord(SessionId(identifier), name, (message,), NOW, NOW)


class FakeSessions:
    def __init__(self, *records: SessionRecord) -> None:
        self.records = {record.session_id: record for record in records}
        self.fail_save = False
        self.fail_delete = False
        self.fail_list = False

    async def list(self) -> tuple[SessionSummary, ...]:
        if self.fail_list:
            raise OSError("list failed")
        return tuple(
            SessionSummary(record.session_id, record.name, len(record.messages), record.created_at, record.updated_at)
            for record in self.records.values()
        )

    async def load(self, session_id: SessionId) -> KernelResult[SessionRecord]:
        record = self.records.get(session_id)
        if record is None:
            return KernelResult.failure(KernelError(ErrorCode.SESSION_NOT_FOUND, "missing"))
        return KernelResult.success(record)

    async def save(self, session: SessionRecord, active: bool) -> KernelResult[SessionRecord]:
        if self.fail_save:
            return KernelResult.failure(KernelError(ErrorCode.SESSION_PERSISTENCE_FAILED, "save failed"))
        self.records[session.session_id] = session
        return KernelResult.success(session)

    async def delete(self, session_id: SessionId) -> KernelResult[bool]:
        if self.fail_delete:
            return KernelResult.failure(KernelError(ErrorCode.SESSION_PERSISTENCE_FAILED, "delete failed"))
        if session_id not in self.records:
            return KernelResult.failure(KernelError(ErrorCode.SESSION_NOT_FOUND, "missing"))
        del self.records[session_id]
        return KernelResult.success(True)


def test_crud_search_export_and_revision() -> None:
    async def exercise() -> None:
        repository = FakeSessions(_record("one"))
        service = SessionService(repository, SessionTurnSupervisor())

        listed = await service.list()
        assert listed.value is not None and listed.value[0].session_id == SessionId("one")
        created = await service.create(" Beta ", session_id=SessionId("two"))
        assert created.value is not None and created.value.name == "Beta"
        renamed = await service.rename(SessionId("two"), "Gamma")
        assert renamed.value is not None and renamed.value.name == "Gamma"
        assert service.revision == 2

        by_name = await service.search("gamma")
        assert by_name.value is not None and tuple(item.session_id for item in by_name.value) == (SessionId("two"),)
        by_content = await service.search("needle")
        assert by_content.value is not None and by_content.value[0].session_id == SessionId("one")
        exported_json = await service.export(SessionId("one"))
        assert exported_json.value is not None
        assert SessionRecord.from_json(exported_json.value).session_id == SessionId("one")
        exported_markdown = await service.export(SessionId("one"), format="markdown")
        assert exported_markdown.value is not None and "# Alpha" in exported_markdown.value

        deleted = await service.delete(SessionId("two"))
        assert deleted.value is True and service.revision == 3

    asyncio.run(exercise())


def test_busy_and_persistence_failures_do_not_advance_revision() -> None:
    async def exercise() -> None:
        record = _record("one")
        repository = FakeSessions(record)
        supervisor = SessionTurnSupervisor()
        service = SessionService(repository, supervisor)
        lease = await supervisor.start(SessionId("one"), TurnId("turn"))
        assert lease.value is not None

        busy = await service.rename(SessionId("one"), "Blocked")
        assert busy.error is not None
        assert busy.error.code is ErrorCode.KERNEL_BUSY
        assert busy.error.message.startswith("SESSION_BUSY")
        assert repository.records[SessionId("one")] == record
        await lease.value.release()

        repository.fail_save = True
        failed = await service.rename(SessionId("one"), "Lost")
        assert failed.error is not None and service.revision == 0
        assert repository.records[SessionId("one")] == record
        repository.fail_list = True
        listed = await service.list()
        assert listed.error is not None and listed.error.code is ErrorCode.SESSION_PERSISTENCE_FAILED

    asyncio.run(exercise())


def test_invalid_inputs_are_typed() -> None:
    async def exercise() -> None:
        service = SessionService(FakeSessions(), SessionTurnSupervisor())
        assert (await service.create(" ")).error is not None
        assert (await service.search("x", limit=-1)).error is not None
        invalid_export = await service.export(SessionId("missing"), format="csv")
        assert invalid_export.error is not None

    asyncio.run(exercise())
