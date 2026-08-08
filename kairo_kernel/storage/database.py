"""Shared aiosqlite connection, WAL configuration, and schema migrations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

SCHEMA_VERSION = 1

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS kernel_schema (
    version INTEGER NOT NULL
);
INSERT INTO kernel_schema(version)
SELECT 0 WHERE NOT EXISTS (SELECT 1 FROM kernel_schema);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    record_json TEXT NOT NULL,
    message_count INTEGER NOT NULL,
    context_used_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0, 1))
);
CREATE INDEX IF NOT EXISTS sessions_updated_idx ON sessions(updated_at DESC, session_id);
CREATE UNIQUE INDEX IF NOT EXISTS sessions_one_active_idx ON sessions(active) WHERE active = 1;

CREATE TABLE IF NOT EXISTS config_revisions (
    revision INTEGER PRIMARY KEY,
    snapshot_json TEXT NOT NULL,
    saved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS config_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    current_revision INTEGER NOT NULL REFERENCES config_revisions(revision)
);

CREATE TABLE IF NOT EXISTS workspace_history (
    revision INTEGER PRIMARY KEY,
    record_json TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspace_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    current_revision INTEGER NOT NULL REFERENCES workspace_history(revision)
);

CREATE TABLE IF NOT EXISTS memory_entries (
    memory_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    entry_json TEXT NOT NULL,
    searchable_text TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(namespace, key)
);
CREATE INDEX IF NOT EXISTS memory_namespace_updated_idx
    ON memory_entries(namespace, updated_at DESC, memory_id);
CREATE TABLE IF NOT EXISTS memory_tags (
    memory_id TEXT NOT NULL REFERENCES memory_entries(memory_id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY(memory_id, tag)
);
CREATE INDEX IF NOT EXISTS memory_tags_tag_idx ON memory_tags(tag, memory_id);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    searchable_text,
    content='memory_entries',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS memory_entries_ai AFTER INSERT ON memory_entries BEGIN
    INSERT INTO memory_fts(rowid, searchable_text) VALUES (new.rowid, new.searchable_text);
END;
CREATE TRIGGER IF NOT EXISTS memory_entries_ad AFTER DELETE ON memory_entries BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, searchable_text)
    VALUES ('delete', old.rowid, old.searchable_text);
END;
CREATE TRIGGER IF NOT EXISTS memory_entries_au AFTER UPDATE ON memory_entries BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, searchable_text)
    VALUES ('delete', old.rowid, old.searchable_text);
    INSERT INTO memory_fts(rowid, searchable_text) VALUES (new.rowid, new.searchable_text);
END;

UPDATE kernel_schema SET version = 1;
PRAGMA user_version = 1;
"""


class SQLiteDatabase:
    """Own one serialized aiosqlite connection shared by repositories."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._connection: aiosqlite.Connection | None = None
        self._open_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()

    async def open(self) -> SQLiteDatabase:
        if self._connection is not None:
            return self
        async with self._open_lock:
            if self._connection is not None:
                return self
            if self.path != ":memory:":
                Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
            connection = await aiosqlite.connect(self.path)
            connection.row_factory = aiosqlite.Row
            try:
                await connection.execute("PRAGMA foreign_keys = ON")
                await connection.execute("PRAGMA busy_timeout = 5000")
                await connection.execute("PRAGMA synchronous = NORMAL")
                if self.path != ":memory:":
                    await connection.execute("PRAGMA journal_mode = WAL")
                await connection.executescript(_SCHEMA_V1)
                await connection.commit()
            except BaseException:
                await connection.close()
                raise
            self._connection = connection
        return self

    async def close(self) -> None:
        async with self._open_lock:
            connection = self._connection
            self._connection = None
            if connection is not None:
                await connection.close()

    @asynccontextmanager
    async def read(self) -> AsyncIterator[aiosqlite.Connection]:
        await self.open()
        async with self._operation_lock:
            connection = self._require_connection()
            yield connection

    @asynccontextmanager
    async def write(self) -> AsyncIterator[aiosqlite.Connection]:
        await self.open()
        async with self._operation_lock:
            connection = self._require_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                await connection.rollback()
                raise
            else:
                await connection.commit()

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("SQLiteDatabase is not open.")
        return self._connection

    async def journal_mode(self) -> str:
        async with self.read() as connection:
            cursor = await connection.execute("PRAGMA journal_mode")
            row = await cursor.fetchone()
            await cursor.close()
        return "" if row is None else str(row[0]).lower()

    async def schema_version(self) -> int:
        async with self.read() as connection:
            cursor = await connection.execute("SELECT version FROM kernel_schema LIMIT 1")
            row = await cursor.fetchone()
            await cursor.close()
        return 0 if row is None else int(row[0])
