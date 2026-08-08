"""Explicit, UI-neutral session management."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from typing import TypeVar
from uuid import uuid4

from kairo_kernel.contracts.content import (
    AudioBlock,
    FileBlock,
    ImageBlock,
    Message,
    ReasoningBlock,
    ResourceBlock,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import SessionId
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.support import SessionRecord, SessionSummary
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.ports.repositories import SessionRepositoryPort
from kairo_kernel.runtime.turns import SessionTurnSupervisor

ResultT = TypeVar("ResultT")


class SessionService:
    """CRUD, search and serialization without an implicit active session."""

    def __init__(self, repository: SessionRepositoryPort, supervisor: SessionTurnSupervisor) -> None:
        self._repository = repository
        self._supervisor = supervisor
        self._mutation_lock = asyncio.Lock()
        self._revision = 0

    @property
    def revision(self) -> int:
        return self._revision

    async def list(self) -> KernelResult[tuple[SessionSummary, ...]]:
        try:
            return KernelResult.success(await self._repository.list())
        except Exception as exc:
            return _persistence_failure("session.list", exc)

    async def get(self, session_id: SessionId) -> KernelResult[SessionRecord]:
        if not str(session_id).strip():
            return _failure(ErrorCode.INVALID_ARGUMENT, "Session id is required.", "session.get")
        return await self._repository.load(session_id)

    async def create(
        self,
        name: str,
        messages: tuple[Message, ...] = (),
        *,
        session_id: SessionId | None = None,
    ) -> KernelResult[SessionRecord]:
        clean_name = name.strip()
        if not clean_name:
            return _failure(ErrorCode.INVALID_ARGUMENT, "Session name is required.", "session.create")
        identifier = session_id or SessionId(uuid4().hex)
        if not str(identifier).strip():
            return _failure(ErrorCode.INVALID_ARGUMENT, "Session id is required.", "session.create")
        now = datetime.now(timezone.utc)
        record = SessionRecord(identifier, clean_name, tuple(messages), now, now)
        async with self._mutation_lock:
            saved = await self._repository.save(record, active=False)
            if saved.ok:
                self._revision += 1
            return saved

    async def rename(self, session_id: SessionId, name: str) -> KernelResult[SessionRecord]:
        clean_name = name.strip()
        if not clean_name:
            return _failure(ErrorCode.INVALID_ARGUMENT, "Session name is required.", "session.rename")
        async with self._mutation_lock:
            busy = await self._busy(session_id, "session.rename")
            if busy is not None:
                return busy
            loaded = await self._repository.load(session_id)
            if loaded.error is not None:
                return KernelResult.failure(loaded.error)
            assert loaded.value is not None
            changed = replace(loaded.value, name=clean_name, updated_at=datetime.now(timezone.utc))
            saved = await self._repository.save(changed, active=False)
            if saved.ok:
                self._revision += 1
            return saved

    async def delete(self, session_id: SessionId) -> KernelResult[bool]:
        async with self._mutation_lock:
            busy = await self._busy_bool(session_id, "session.delete")
            if busy is not None:
                return busy
            deleted = await self._repository.delete(session_id)
            if deleted.ok:
                self._revision += 1
            return deleted

    async def search(self, text: str, *, limit: int = 50) -> KernelResult[tuple[SessionSummary, ...]]:
        if limit < 0:
            return _failure(ErrorCode.INVALID_ARGUMENT, "Search limit cannot be negative.", "session.search")
        if limit == 0:
            return KernelResult.success(())
        try:
            summaries = await self._repository.list()
        except Exception as exc:
            return _persistence_failure("session.search", exc)
        needle = text.strip().casefold()
        matches: list[SessionSummary] = []
        for summary in summaries:
            if not needle or needle in summary.name.casefold():
                matches.append(summary)
            else:
                loaded = await self._repository.load(summary.session_id)
                if loaded.error is not None:
                    return KernelResult.failure(loaded.error)
                assert loaded.value is not None
                if needle in _record_text(loaded.value).casefold():
                    matches.append(summary)
            if len(matches) >= limit:
                break
        return KernelResult.success(tuple(matches))

    async def export(self, session_id: SessionId, *, format: str = "json") -> KernelResult[str]:
        normalized = format.strip().lower()
        if normalized not in {"json", "markdown"}:
            return _failure(ErrorCode.INVALID_ARGUMENT, "Export format must be json or markdown.", "session.export")
        loaded = await self.get(session_id)
        if loaded.error is not None:
            return KernelResult.failure(loaded.error)
        assert loaded.value is not None
        if normalized == "json":
            return KernelResult.success(loaded.value.to_json())
        return KernelResult.success(_markdown(loaded.value))

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

    async def _busy_bool(self, session_id: SessionId, operation: str) -> KernelResult[bool] | None:
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


def _failure(code: ErrorCode, message: str, operation: str) -> KernelResult[ResultT]:
    return KernelResult.failure(KernelError(code, message, operation=operation))


def _persistence_failure(operation: str, exc: Exception) -> KernelResult[ResultT]:
    return KernelResult.failure(
        KernelError(
            ErrorCode.SESSION_PERSISTENCE_FAILED,
            f"Session persistence failed: {exc}",
            retryable=True,
            operation=operation,
        )
    )


def _record_text(record: SessionRecord) -> str:
    return "\n".join(_message_text(message) for message in record.messages)


def _message_text(message: Message) -> str:
    parts: list[str] = []
    for block in message.content:
        if isinstance(block, (TextBlock, ReasoningBlock)):
            parts.append(block.text)
        elif isinstance(block, ImageBlock):
            parts.extend((block.alt_text, block.uri))
        elif isinstance(block, AudioBlock):
            parts.extend((block.transcript, block.uri))
        elif isinstance(block, FileBlock):
            parts.extend((block.name, block.uri))
        elif isinstance(block, ResourceBlock):
            parts.extend((block.name, block.description, block.uri))
        elif isinstance(block, (ToolCallBlock, ToolResultBlock)):
            parts.append(block.name)
    return " ".join(part for part in parts if part)


def _markdown(record: SessionRecord) -> str:
    lines = [f"# {record.name}", ""]
    for message in record.messages:
        lines.extend((f"## {message.role.value}", "", _message_text(message), ""))
    return "\n".join(lines).rstrip() + "\n"
