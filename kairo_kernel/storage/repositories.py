"""Concrete SQLite implementations of frozen repository ports."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import SessionId
from kairo_kernel.contracts.support import ConfigSnapshot, SessionRecord, SessionSummary, WorkspaceRecord
from kairo_kernel.errors import KernelResult
from kairo_kernel.storage._errors import failure
from kairo_kernel.storage.database import SQLiteDatabase


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteSessionRepository:
    """Persist immutable session records and one active-session marker."""

    def __init__(self, database: SQLiteDatabase):
        self.database = database

    async def list(self) -> tuple[SessionSummary, ...]:
        async with self.database.read() as connection:
            cursor = await connection.execute(
                """
                SELECT session_id, name, message_count, created_at, updated_at,
                       context_used_tokens
                FROM sessions
                ORDER BY updated_at DESC, session_id
                """
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return tuple(
            SessionSummary(
                session_id=SessionId(str(row["session_id"])),
                name=str(row["name"]),
                message_count=int(row["message_count"]),
                created_at=datetime.fromisoformat(str(row["created_at"])),
                updated_at=datetime.fromisoformat(str(row["updated_at"])),
                context_used_tokens=int(row["context_used_tokens"]),
            )
            for row in rows
        )

    async def load(self, session_id: SessionId) -> KernelResult[SessionRecord]:
        if not str(session_id).strip():
            return failure(ErrorCode.INVALID_ARGUMENT, "Session id is required.", "session.load")
        try:
            async with self.database.read() as connection:
                cursor = await connection.execute(
                    "SELECT record_json FROM sessions WHERE session_id = ?",
                    (str(session_id),),
                )
                row = await cursor.fetchone()
                await cursor.close()
            if row is None:
                return failure(ErrorCode.SESSION_NOT_FOUND, "Session not found.", "session.load")
            return KernelResult.success(SessionRecord.from_json(str(row["record_json"])))
        except Exception as exc:
            return failure(
                ErrorCode.SESSION_PERSISTENCE_FAILED,
                f"Failed to load session: {exc}",
                "session.load",
                retryable=True,
            )

    async def save(self, session: SessionRecord, active: bool) -> KernelResult[SessionRecord]:
        if not str(session.session_id).strip() or not session.name.strip():
            return failure(
                ErrorCode.INVALID_ARGUMENT,
                "Session id and name are required.",
                "session.save",
            )
        if session.updated_at < session.created_at:
            return failure(
                ErrorCode.INVALID_ARGUMENT,
                "Session updated_at cannot precede created_at.",
                "session.save",
            )
        try:
            async with self.database.write() as connection:
                if active:
                    await connection.execute("UPDATE sessions SET active = 0 WHERE active = 1")
                await connection.execute(
                    """
                    INSERT INTO sessions(
                        session_id, name, record_json, message_count,
                        context_used_tokens, created_at, updated_at, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        name = excluded.name,
                        record_json = excluded.record_json,
                        message_count = excluded.message_count,
                        created_at = excluded.created_at,
                        updated_at = excluded.updated_at,
                        active = excluded.active
                    """,
                    (
                        str(session.session_id),
                        session.name,
                        session.to_json(),
                        len(session.messages),
                        0,
                        session.created_at.isoformat(),
                        session.updated_at.isoformat(),
                        int(active),
                    ),
                )
            return KernelResult.success(session)
        except Exception as exc:
            return failure(
                ErrorCode.SESSION_PERSISTENCE_FAILED,
                f"Failed to save session: {exc}",
                "session.save",
                retryable=True,
            )

    async def delete(self, session_id: SessionId) -> KernelResult[bool]:
        if not str(session_id).strip():
            return failure(ErrorCode.INVALID_ARGUMENT, "Session id is required.", "session.delete")
        try:
            async with self.database.write() as connection:
                cursor = await connection.execute(
                    "DELETE FROM sessions WHERE session_id = ?",
                    (str(session_id),),
                )
                deleted = cursor.rowcount > 0
                await cursor.close()
            if not deleted:
                return failure(ErrorCode.SESSION_NOT_FOUND, "Session not found.", "session.delete")
            return KernelResult.success(True)
        except Exception as exc:
            return failure(
                ErrorCode.SESSION_PERSISTENCE_FAILED,
                f"Failed to delete session: {exc}",
                "session.delete",
                retryable=True,
            )

    async def active_session_id(self) -> SessionId | None:
        async with self.database.read() as connection:
            cursor = await connection.execute("SELECT session_id FROM sessions WHERE active = 1 LIMIT 1")
            row = await cursor.fetchone()
            await cursor.close()
        return None if row is None else SessionId(str(row["session_id"]))

    async def set_context_used_tokens(self, session_id: SessionId, value: int) -> None:
        """Importer-only metadata hook; the frozen SessionRecord has no usage field."""
        async with self.database.write() as connection:
            await connection.execute(
                "UPDATE sessions SET context_used_tokens = ? WHERE session_id = ?",
                (max(0, int(value)), str(session_id)),
            )


class SQLiteConfigRepository:
    """Store immutable config snapshots with revision restore support."""

    def __init__(self, database: SQLiteDatabase):
        self.database = database

    async def load(self) -> KernelResult[ConfigSnapshot]:
        try:
            async with self.database.read() as connection:
                cursor = await connection.execute(
                    """
                    SELECT r.snapshot_json
                    FROM config_state s
                    JOIN config_revisions r ON r.revision = s.current_revision
                    WHERE s.singleton = 1
                    """
                )
                row = await cursor.fetchone()
                await cursor.close()
            if row is None:
                return failure(ErrorCode.NOT_FOUND, "Configuration has not been saved.", "config.load")
            return KernelResult.success(ConfigSnapshot.from_json(str(row["snapshot_json"])))
        except Exception as exc:
            return failure(
                ErrorCode.CONFIG_PERSISTENCE_FAILED,
                f"Failed to load configuration: {exc}",
                "config.load",
                retryable=True,
            )

    async def validate(self, snapshot: ConfigSnapshot) -> KernelResult[ConfigSnapshot]:
        if snapshot.revision < 0:
            return failure(
                ErrorCode.CONFIG_INVALID,
                "Configuration revision cannot be negative.",
                "config.validate",
            )
        return KernelResult.success(snapshot)

    async def save(
        self,
        snapshot: ConfigSnapshot,
        create_backup: bool = True,
    ) -> KernelResult[ConfigSnapshot]:
        validated = await self.validate(snapshot)
        if not validated.ok:
            return validated
        try:
            async with self.database.write() as connection:
                await connection.execute(
                    """
                    INSERT INTO config_revisions(revision, snapshot_json, saved_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(revision) DO UPDATE SET
                        snapshot_json = excluded.snapshot_json,
                        saved_at = excluded.saved_at
                    """,
                    (snapshot.revision, snapshot.to_json(), _utc_now()),
                )
                await connection.execute(
                    """
                    INSERT INTO config_state(singleton, current_revision) VALUES (1, ?)
                    ON CONFLICT(singleton) DO UPDATE SET current_revision = excluded.current_revision
                    """,
                    (snapshot.revision,),
                )
                if not create_backup:
                    await connection.execute(
                        "DELETE FROM config_revisions WHERE revision <> ?",
                        (snapshot.revision,),
                    )
            return KernelResult.success(snapshot)
        except Exception as exc:
            return failure(
                ErrorCode.CONFIG_PERSISTENCE_FAILED,
                f"Failed to save configuration: {exc}",
                "config.save",
                retryable=True,
            )

    async def restore(self, revision: int) -> KernelResult[ConfigSnapshot]:
        try:
            async with self.database.write() as connection:
                cursor = await connection.execute(
                    "SELECT snapshot_json FROM config_revisions WHERE revision = ?",
                    (revision,),
                )
                row = await cursor.fetchone()
                await cursor.close()
                if row is None:
                    return failure(
                        ErrorCode.NOT_FOUND,
                        f"Configuration revision {revision} was not found.",
                        "config.restore",
                    )
                await connection.execute(
                    """
                    INSERT INTO config_state(singleton, current_revision) VALUES (1, ?)
                    ON CONFLICT(singleton) DO UPDATE SET current_revision = excluded.current_revision
                    """,
                    (revision,),
                )
            return KernelResult.success(ConfigSnapshot.from_json(str(row["snapshot_json"])))
        except Exception as exc:
            return failure(
                ErrorCode.CONFIG_PERSISTENCE_FAILED,
                f"Failed to restore configuration: {exc}",
                "config.restore",
                retryable=True,
            )


class SQLiteWorkspaceRepository:
    """Persist current workspace and its revision history."""

    def __init__(self, database: SQLiteDatabase):
        self.database = database

    async def current(self) -> WorkspaceRecord:
        async with self.database.read() as connection:
            cursor = await connection.execute(
                """
                SELECT h.record_json
                FROM workspace_state s
                JOIN workspace_history h ON h.revision = s.current_revision
                WHERE s.singleton = 1
                """
            )
            row = await cursor.fetchone()
            await cursor.close()
        return WorkspaceRecord(root="", revision=0) if row is None else WorkspaceRecord.from_json(str(row["record_json"]))

    async def validate(self, root: str) -> KernelResult[WorkspaceRecord]:
        clean = root.strip()
        if not clean:
            return failure(ErrorCode.WORKSPACE_INVALID, "Workspace root is required.", "workspace.validate")
        try:
            path = Path(clean).expanduser().resolve(strict=True)
            if not path.is_dir():
                raise ValueError("path is not a directory")
            probe: Path | None = None
            try:
                with NamedTemporaryFile(prefix=".kairo-write-probe-", dir=path, delete=False) as handle:
                    probe = Path(handle.name)
            finally:
                if probe is not None:
                    probe.unlink(missing_ok=True)
        except Exception as exc:
            return failure(
                ErrorCode.WORKSPACE_INVALID,
                f"Workspace is invalid or not writable: {exc}",
                "workspace.validate",
            )
        previous = await self.current()
        return KernelResult.success(
            WorkspaceRecord(
                root=str(path),
                revision=previous.revision + 1,
                previous_root=previous.root,
            )
        )

    async def apply(self, workspace: WorkspaceRecord) -> KernelResult[WorkspaceRecord]:
        if not workspace.root.strip() or workspace.revision < 0:
            return failure(ErrorCode.WORKSPACE_INVALID, "Workspace record is invalid.", "workspace.apply")
        try:
            async with self.database.write() as connection:
                await connection.execute(
                    """
                    INSERT INTO workspace_history(revision, record_json, applied_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(revision) DO UPDATE SET
                        record_json = excluded.record_json,
                        applied_at = excluded.applied_at
                    """,
                    (workspace.revision, workspace.to_json(), _utc_now()),
                )
                await connection.execute(
                    """
                    INSERT INTO workspace_state(singleton, current_revision) VALUES (1, ?)
                    ON CONFLICT(singleton) DO UPDATE SET current_revision = excluded.current_revision
                    """,
                    (workspace.revision,),
                )
            return KernelResult.success(workspace)
        except Exception as exc:
            return failure(
                ErrorCode.RUNTIME_SYNC_FAILED,
                f"Failed to apply workspace: {exc}",
                "workspace.apply",
                retryable=True,
            )

    async def rollback(self, workspace: WorkspaceRecord) -> KernelResult[WorkspaceRecord]:
        return await self.apply(workspace)
