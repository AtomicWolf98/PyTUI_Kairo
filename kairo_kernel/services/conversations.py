"""Transactional conversation history operations."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from kairo_kernel.contracts.content import Message
from kairo_kernel.contracts.enums import ErrorCode, MessageRole
from kairo_kernel.contracts.identifiers import MessageId, SessionId
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.support import SessionRecord
from kairo_kernel.engine.context import ContextPacker
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.ports.repositories import SessionRepositoryPort
from kairo_kernel.runtime.turns import SessionTurnSupervisor


class ConversationService:
    """History mutations bound to an explicit session identifier."""

    def __init__(self, repository: SessionRepositoryPort, supervisor: SessionTurnSupervisor) -> None:
        self._repository = repository
        self._supervisor = supervisor
        self._mutation_lock = asyncio.Lock()
        self._revision = 0

    @property
    def revision(self) -> int:
        return self._revision

    async def history(self, session_id: SessionId) -> KernelResult[tuple[Message, ...]]:
        loaded = await self._repository.load(session_id)
        if loaded.error is not None:
            return KernelResult.failure(loaded.error)
        assert loaded.value is not None
        return KernelResult.success(loaded.value.messages)

    async def clear(self, session_id: SessionId) -> KernelResult[SessionRecord]:
        def transform(record: SessionRecord) -> KernelResult[SessionRecord]:
            prefix: list[Message] = []
            for message in record.messages:
                if message.role is not MessageRole.SYSTEM:
                    break
                prefix.append(message)
            return KernelResult.success(
                replace(
                    record,
                    messages=tuple(prefix),
                    updated_at=datetime.now(timezone.utc),
                    compression_count=0,
                )
            )

        return await self._mutate(session_id, "conversation.clear", transform)

    async def undo(self, session_id: SessionId) -> KernelResult[SessionRecord]:
        def transform(record: SessionRecord) -> KernelResult[SessionRecord]:
            last_user = next(
                (index for index in range(len(record.messages) - 1, -1, -1) if record.messages[index].role is MessageRole.USER),
                None,
            )
            if last_user is None:
                return _failure(ErrorCode.CONFLICT, "Conversation has no user turn to undo.", "conversation.undo")
            return KernelResult.success(
                replace(record, messages=record.messages[:last_user], updated_at=datetime.now(timezone.utc))
            )

        return await self._mutate(session_id, "conversation.undo", transform)

    async def compress(
        self,
        session_id: SessionId,
        summary: str,
        *,
        preserve_recent_turns: int = 4,
    ) -> KernelResult[SessionRecord]:
        clean_summary = summary.strip()
        if not clean_summary:
            return _failure(ErrorCode.INVALID_ARGUMENT, "Compression summary is required.", "conversation.compress")
        if preserve_recent_turns < 0:
            return _failure(
                ErrorCode.INVALID_ARGUMENT,
                "preserve_recent_turns cannot be negative.",
                "conversation.compress",
            )

        def transform(record: SessionRecord) -> KernelResult[SessionRecord]:
            packer = ContextPacker(preserve_turns=preserve_recent_turns)
            source, retained = packer.source_and_retained(record.messages)
            if not source:
                return _failure(
                    ErrorCode.CONFLICT,
                    "Conversation has no old turns to compress.",
                    "conversation.compress",
                )
            messages = packer.insert_summary(retained, clean_summary, MessageId(uuid4().hex))
            return KernelResult.success(
                replace(
                    record,
                    messages=messages,
                    updated_at=datetime.now(timezone.utc),
                    compression_count=record.compression_count + 1,
                )
            )

        return await self._mutate(session_id, "conversation.compress", transform)

    async def _mutate(
        self,
        session_id: SessionId,
        operation: str,
        transform: Callable[[SessionRecord], KernelResult[SessionRecord]],
    ) -> KernelResult[SessionRecord]:
        async with self._mutation_lock:
            busy = await self._busy(session_id, operation)
            if busy is not None:
                return busy
            loaded = await self._repository.load(session_id)
            if loaded.error is not None:
                return KernelResult.failure(loaded.error)
            assert loaded.value is not None
            changed = transform(loaded.value)
            if changed.error is not None:
                return changed
            assert changed.value is not None
            saved = await self._repository.save(changed.value, active=False)
            if saved.ok:
                self._revision += 1
            return saved

    async def _busy(self, session_id: SessionId, operation: str) -> KernelResult[SessionRecord] | None:
        active = dict(await self._supervisor.active()).get(session_id)
        if active is None:
            return None
        return KernelResult.failure(
            KernelError(
                ErrorCode.KERNEL_BUSY,
                "SESSION_BUSY: Session has an active turn.",
                retryable=True,
                operation=operation,
                details=JsonObject.from_pairs(("active_turn_id", str(active))),
            )
        )


def _failure(code: ErrorCode, message: str, operation: str) -> KernelResult[SessionRecord]:
    return KernelResult.failure(KernelError(code, message, operation=operation))
