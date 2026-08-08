from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from kairo_kernel.contracts.content import Message, TextBlock, ToolCallBlock, ToolResultBlock
from kairo_kernel.contracts.enums import ErrorCode, MessageKind, MessageRole, ToolExecutionStatus
from kairo_kernel.contracts.identifiers import MessageId, SessionId, ToolCallId, TurnId
from kairo_kernel.contracts.support import SessionRecord, SessionSummary
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.runtime.turns import SessionTurnSupervisor
from kairo_kernel.services.conversations import ConversationService

NOW = datetime(2026, 8, 8, tzinfo=timezone.utc)


def _message(identifier: str, role: MessageRole, text: str) -> Message:
    return Message(MessageId(identifier), role, MessageKind.CHAT, (TextBlock(text),))


class FakeSessions:
    def __init__(self, record: SessionRecord) -> None:
        self.record = record
        self.fail_save = False
        self.save_started = asyncio.Event()
        self.save_allowed = asyncio.Event()
        self.block_save = False

    async def list(self) -> tuple[SessionSummary, ...]:
        return (SessionSummary(self.record.session_id, self.record.name, len(self.record.messages), NOW, NOW),)

    async def load(self, session_id: SessionId) -> KernelResult[SessionRecord]:
        if session_id != self.record.session_id:
            return KernelResult.failure(KernelError(ErrorCode.SESSION_NOT_FOUND, "missing"))
        return KernelResult.success(self.record)

    async def save(self, session: SessionRecord, active: bool) -> KernelResult[SessionRecord]:
        self.save_started.set()
        if self.block_save:
            await self.save_allowed.wait()
        if self.fail_save:
            return KernelResult.failure(KernelError(ErrorCode.SESSION_PERSISTENCE_FAILED, "save failed"))
        self.record = session
        return KernelResult.success(session)

    async def delete(self, session_id: SessionId) -> KernelResult[bool]:
        return KernelResult.success(True)


def _tool_history() -> SessionRecord:
    tool_call_id = ToolCallId("call")
    messages = (
        _message("system", MessageRole.SYSTEM, "system"),
        _message("u1", MessageRole.USER, "first"),
        _message("a1", MessageRole.ASSISTANT, "first answer"),
        _message("u2", MessageRole.USER, "second"),
        Message(
            MessageId("a2"),
            MessageRole.ASSISTANT,
            MessageKind.CHAT,
            (ToolCallBlock(tool_call_id, "read"),),
        ),
        Message(
            MessageId("t2"),
            MessageRole.TOOL,
            MessageKind.CHAT,
            (ToolResultBlock(tool_call_id, "read", ToolExecutionStatus.SUCCEEDED, (TextBlock("result"),)),),
        ),
        _message("a3", MessageRole.ASSISTANT, "second answer"),
    )
    return SessionRecord(SessionId("one"), "One", messages, NOW, NOW)


def test_history_clear_and_undo_whole_tool_turn() -> None:
    async def exercise() -> None:
        repository = FakeSessions(_tool_history())
        service = ConversationService(repository, SessionTurnSupervisor())
        history = await service.history(SessionId("one"))
        assert history.value is not None and len(history.value) == 7

        undone = await service.undo(SessionId("one"))
        assert undone.value is not None
        assert tuple(message.message_id for message in undone.value.messages) == (
            MessageId("system"),
            MessageId("u1"),
            MessageId("a1"),
        )
        assert service.revision == 1
        cleared = await service.clear(SessionId("one"))
        assert cleared.value is not None
        assert tuple(message.message_id for message in cleared.value.messages) == (MessageId("system"),)
        assert service.revision == 2

    asyncio.run(exercise())


def test_compress_preserves_four_recent_turns() -> None:
    async def exercise() -> None:
        messages = [_message("system", MessageRole.SYSTEM, "system")]
        for index in range(6):
            messages.extend(
                (
                    _message(f"u{index}", MessageRole.USER, f"question {index}"),
                    _message(f"a{index}", MessageRole.ASSISTANT, f"answer {index}"),
                )
            )
        record = SessionRecord(SessionId("one"), "One", tuple(messages), NOW, NOW)
        repository = FakeSessions(record)
        service = ConversationService(repository, SessionTurnSupervisor())
        result = await service.compress(SessionId("one"), "old turns", preserve_recent_turns=4)
        assert result.value is not None
        assert result.value.compression_count == 1
        assert result.value.messages[1].kind is MessageKind.SUMMARY
        identifiers = tuple(message.message_id for message in result.value.messages)
        assert MessageId("u0") not in identifiers and MessageId("u1") not in identifiers
        assert MessageId("u2") in identifiers and MessageId("u5") in identifiers

    asyncio.run(exercise())


def test_busy_failure_and_concurrent_snapshot_are_atomic() -> None:
    async def exercise() -> None:
        original = _tool_history()
        repository = FakeSessions(original)
        supervisor = SessionTurnSupervisor()
        service = ConversationService(repository, supervisor)
        lease = await supervisor.start(SessionId("one"), TurnId("turn"))
        assert lease.value is not None
        busy = await service.clear(SessionId("one"))
        assert busy.error is not None and busy.error.code is ErrorCode.KERNEL_BUSY
        await lease.value.release()

        repository.fail_save = True
        failed = await service.undo(SessionId("one"))
        assert failed.error is not None and service.revision == 0 and repository.record == original
        repository.fail_save = False
        repository.block_save = True
        task = asyncio.create_task(service.undo(SessionId("one")))
        await repository.save_started.wait()
        snapshot = await service.history(SessionId("one"))
        assert snapshot.value == original.messages
        repository.save_allowed.set()
        changed = await task
        assert changed.value is not None
        after = await service.history(SessionId("one"))
        assert after.value == changed.value.messages and snapshot.value == original.messages

    asyncio.run(exercise())


def test_empty_undo_and_compress_are_typed_conflicts() -> None:
    async def exercise() -> None:
        record = SessionRecord(SessionId("one"), "One", (_message("system", MessageRole.SYSTEM, "system"),), NOW, NOW)
        service = ConversationService(FakeSessions(record), SessionTurnSupervisor())
        undone = await service.undo(SessionId("one"))
        compressed = await service.compress(SessionId("one"), "summary")
        assert undone.error is not None and undone.error.code is ErrorCode.CONFLICT
        assert compressed.error is not None and compressed.error.code is ErrorCode.CONFLICT

    asyncio.run(exercise())
