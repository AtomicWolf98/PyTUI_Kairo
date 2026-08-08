"""One-way importer for Kairo's legacy JSON session directory."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from kairo_kernel.contracts.content import Message, TextBlock, ToolCallBlock, ToolResultBlock
from kairo_kernel.contracts.enums import ErrorCode, MessageKind, MessageRole, ToolExecutionStatus
from kairo_kernel.contracts.identifiers import MessageId, SessionId, ToolCallId
from kairo_kernel.contracts.json import JsonObject, freeze_json
from kairo_kernel.contracts.support import SessionRecord
from kairo_kernel.errors import KernelResult
from kairo_kernel.ports.repositories import SessionRepositoryPort
from kairo_kernel.storage._errors import failure
from kairo_kernel.storage.repositories import SQLiteSessionRepository

_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")
_MESSAGE_NAMESPACE = uuid.UUID("59027957-495a-4a4e-9168-1505e182cf87")


class LegacyJsonImporter:
    """Import valid legacy sessions while isolating corrupt files as warnings."""

    def __init__(self, sessions: SessionRepositoryPort):
        self.sessions = sessions
        self._warnings: list[str] = []
        self._active_session_id: SessionId | None = None

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(self._warnings)

    @property
    def active_session_id(self) -> SessionId | None:
        return self._active_session_id

    async def import_directory(self, storage_dir: str | Path) -> KernelResult[tuple[SessionRecord, ...]]:
        self._warnings = []
        self._active_session_id = None
        root = Path(storage_dir).expanduser().resolve()
        if not root.is_dir():
            return failure(ErrorCode.NOT_FOUND, "Legacy session directory was not found.", "migrate.sessions")

        indexed_ids, active_id = self._read_index(root / "index.json")
        if not indexed_ids:
            indexed_ids = self._scan_orphans(root)
        imported: list[SessionRecord] = []
        for session_id in indexed_ids:
            path = root / f"{session_id}.json"
            if not path.is_file():
                self._warnings.append(f"Session file missing for {session_id}; skipping")
                continue
            record, context_used = self._read_session(path, session_id)
            if record is None:
                continue
            is_active = session_id == active_id
            result = await self.sessions.save(record, active=is_active)
            if not result.ok:
                message = "unknown persistence error" if result.error is None else result.error.message
                self._warnings.append(f"Failed to import session {session_id}: {message}")
                continue
            if isinstance(self.sessions, SQLiteSessionRepository):
                await self.sessions.set_context_used_tokens(record.session_id, context_used)
            imported.append(record)
            if is_active:
                self._active_session_id = record.session_id

        if imported and self._active_session_id is None:
            first = imported[0]
            saved = await self.sessions.save(first, active=True)
            if saved.ok:
                self._active_session_id = first.session_id
        if not imported:
            return failure(
                ErrorCode.NOT_FOUND,
                "No valid legacy sessions were found.",
                "migrate.sessions",
            )
        return KernelResult.success(tuple(imported))

    def _read_index(self, path: Path) -> tuple[list[str], str]:
        if not path.is_file():
            self._warnings.append("Legacy session index is missing; scanning orphan files")
            return [], ""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping):
                raise ValueError("index is not a JSON object")
        except Exception as exc:
            self._warnings.append(f"Failed to load legacy session index: {exc}; scanning orphan files")
            return [], ""
        active_raw = raw.get("active_session_id", "")
        active_id = str(active_raw) if isinstance(active_raw, str) else ""
        if active_id and not _SESSION_ID.fullmatch(active_id):
            self._warnings.append(f"Invalid active session id in index: {active_id!r}")
            active_id = ""
        sessions_raw = raw.get("sessions", ())
        if not isinstance(sessions_raw, Sequence) or isinstance(sessions_raw, (str, bytes)):
            self._warnings.append("Legacy session index has an invalid sessions list")
            return [], active_id
        identifiers: list[str] = []
        for item in sessions_raw:
            if not isinstance(item, Mapping):
                continue
            session_raw = item.get("id", "")
            session_id = str(session_raw) if isinstance(session_raw, str) else ""
            if not _SESSION_ID.fullmatch(session_id):
                self._warnings.append(f"Invalid session id in index: {session_id!r}; skipping")
                continue
            expected = f"{session_id}.json"
            file_raw = item.get("file", expected)
            if not isinstance(file_raw, str) or file_raw != expected:
                self._warnings.append(f"Session index file mismatch for {session_id}; skipping")
                continue
            if session_id not in identifiers:
                identifiers.append(session_id)
        return identifiers, active_id

    def _scan_orphans(self, root: Path) -> list[str]:
        identifiers: list[str] = []
        for path in sorted(root.glob("*.json")):
            if path.name == "index.json":
                continue
            if not _SESSION_ID.fullmatch(path.stem):
                self._warnings.append(f"Invalid orphan session filename: {path.name!r}; skipping")
                continue
            identifiers.append(path.stem)
            self._warnings.append(f"Recovered session file without index entry: {path.name}")
        return identifiers

    def _read_session(self, path: Path, expected_id: str) -> tuple[SessionRecord | None, int]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping):
                raise ValueError("session file is not a JSON object")
            actual_id = raw.get("id", "")
            if actual_id != expected_id:
                raise ValueError("session id does not match filename")
            history = raw.get("history", ())
            if not isinstance(history, Sequence) or isinstance(history, (str, bytes)) or not history:
                raise ValueError("session history is empty")
            messages = tuple(
                message
                for index, item in enumerate(history)
                if (message := self._convert_message(item, expected_id, index)) is not None
            )
            if not messages or messages[0].role is not MessageRole.SYSTEM:
                raise ValueError("session history does not start with a system message")
            created_at = _parse_datetime(raw.get("created_at"))
            updated_at = _parse_datetime(raw.get("updated_at"), fallback=created_at)
            if updated_at < created_at:
                updated_at = created_at
            compression_count = _nonnegative_int(raw.get("compression_count"))
            token_usage = raw.get("token_usage", {})
            context_used = 0
            if isinstance(token_usage, Mapping):
                context_used = _nonnegative_int(token_usage.get("context_used_tokens"))
            name_raw = raw.get("name", "Conversation")
            name = str(name_raw).strip() if isinstance(name_raw, str) else "Conversation"
            return (
                SessionRecord(
                    session_id=SessionId(expected_id),
                    name=name or "Conversation",
                    messages=messages,
                    created_at=created_at,
                    updated_at=updated_at,
                    compression_count=compression_count,
                ),
                context_used,
            )
        except Exception as exc:
            self._warnings.append(f"Failed to load session {expected_id}: {exc}")
            return None, 0

    def _convert_message(self, raw: object, session_id: str, index: int) -> Message | None:
        if not isinstance(raw, Mapping):
            self._warnings.append(f"Session {session_id} message {index} is not an object; skipping")
            return None
        role_raw = raw.get("role", "")
        try:
            role = MessageRole(str(role_raw))
        except ValueError:
            self._warnings.append(f"Session {session_id} message {index} has invalid role; skipping")
            return None
        name_raw = raw.get("name", "")
        name = str(name_raw) if isinstance(name_raw, str) else ""
        kind = MessageKind.CHAT
        if role is MessageRole.SYSTEM and name == "kairo_runtime_state":
            kind = MessageKind.RUNTIME_STATE
        elif role is MessageRole.SYSTEM and name in ("context_summary", "kairo_context_summary"):
            kind = MessageKind.SUMMARY
        blocks: list[TextBlock | ToolCallBlock | ToolResultBlock] = []
        content_raw = raw.get("content", "")
        text = content_raw if isinstance(content_raw, str) else json.dumps(content_raw, ensure_ascii=False)
        tool_call_raw = raw.get("tool_call_id", "")
        if role is MessageRole.TOOL and isinstance(tool_call_raw, str) and tool_call_raw:
            blocks.append(
                ToolResultBlock(
                    tool_call_id=ToolCallId(tool_call_raw),
                    name=name,
                    status=ToolExecutionStatus.SUCCEEDED,
                    content=(TextBlock(text),),
                )
            )
        else:
            blocks.append(TextBlock(text))
        calls_raw = raw.get("tool_calls", ())
        if isinstance(calls_raw, Sequence) and not isinstance(calls_raw, (str, bytes)):
            for call in calls_raw:
                converted = _convert_tool_call(call)
                if converted is not None:
                    blocks.append(converted)
        message_raw = raw.get("id", "")
        if isinstance(message_raw, str) and message_raw.strip():
            message_id = message_raw.strip()
        else:
            fingerprint = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
            message_id = uuid.uuid5(_MESSAGE_NAMESPACE, f"{session_id}:{index}:{fingerprint}").hex
        return Message(
            message_id=MessageId(message_id),
            role=role,
            kind=kind,
            content=tuple(blocks),
            name=name,
        )


def _convert_tool_call(raw: object) -> ToolCallBlock | None:
    if not isinstance(raw, Mapping):
        return None
    call_raw = raw.get("id", "")
    function = raw.get("function", {})
    if not isinstance(call_raw, str) or not call_raw or not isinstance(function, Mapping):
        return None
    name_raw = function.get("name", "")
    if not isinstance(name_raw, str) or not name_raw:
        return None
    arguments_raw = function.get("arguments", {})
    if isinstance(arguments_raw, str):
        try:
            arguments_raw = json.loads(arguments_raw)
        except json.JSONDecodeError:
            arguments_raw = {"raw": arguments_raw}
    frozen = freeze_json(arguments_raw)
    arguments = frozen if isinstance(frozen, JsonObject) else JsonObject.from_pairs(("value", frozen))
    return ToolCallBlock(ToolCallId(call_raw), name_raw, arguments)


def _parse_datetime(value: object, *, fallback: datetime | None = None) -> datetime:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return fallback or datetime.now(timezone.utc)


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(str(value)))
    except (TypeError, ValueError):
        return 0
