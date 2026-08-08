"""SQLite FTS5 implementation of the frozen MemoryPort."""

from __future__ import annotations

import json
import re

from kairo_kernel.contracts.content import (
    AudioBlock,
    FileBlock,
    ImageBlock,
    ReasoningBlock,
    ResourceBlock,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import MemoryId
from kairo_kernel.contracts.json import thaw_json
from kairo_kernel.contracts.support import MemoryEntry, MemoryQuery
from kairo_kernel.errors import KernelResult
from kairo_kernel.storage._errors import failure
from kairo_kernel.storage.database import SQLiteDatabase


class SQLiteMemoryStore:
    """Namespace-scoped memory with exact tag filters and ranked FTS5 search."""

    def __init__(self, database: SQLiteDatabase):
        self.database = database

    async def search(self, query: MemoryQuery) -> tuple[MemoryEntry, ...]:
        namespace = query.namespace.strip()
        limit = min(max(int(query.limit), 0), 100)
        if not namespace or limit == 0:
            return ()
        match = _fts_query(query.text)
        parameters: list[str | int] = [namespace]
        tag_clauses: list[str] = []
        for tag in tuple(dict.fromkeys(item.strip() for item in query.tags if item.strip())):
            tag_clauses.append(
                "EXISTS (SELECT 1 FROM memory_tags mt "
                "WHERE mt.memory_id = e.memory_id AND mt.tag = ?)"
            )
            parameters.append(tag)
        tags_sql = "" if not tag_clauses else " AND " + " AND ".join(tag_clauses)
        if match:
            sql = (
                "SELECT e.entry_json FROM memory_fts "
                "JOIN memory_entries e ON e.rowid = memory_fts.rowid "
                "WHERE e.namespace = ? AND memory_fts MATCH ?"
                + tags_sql
                + " ORDER BY bm25(memory_fts), e.updated_at DESC, e.memory_id LIMIT ?"
            )
            parameters.insert(1, match)
        else:
            sql = (
                "SELECT e.entry_json FROM memory_entries e WHERE e.namespace = ?"
                + tags_sql
                + " ORDER BY e.updated_at DESC, e.memory_id LIMIT ?"
            )
        parameters.append(limit)
        async with self.database.read() as connection:
            cursor = await connection.execute(sql, tuple(parameters))
            rows = await cursor.fetchall()
            await cursor.close()
        entries: list[MemoryEntry] = []
        for row in rows:
            try:
                entries.append(MemoryEntry.from_json(str(row["entry_json"])))
            except (TypeError, ValueError):
                continue
        return tuple(entries)

    async def get(self, memory_id: MemoryId) -> KernelResult[MemoryEntry]:
        if not str(memory_id).strip():
            return failure(ErrorCode.INVALID_ARGUMENT, "Memory id is required.", "memory.get")
        try:
            async with self.database.read() as connection:
                cursor = await connection.execute(
                    "SELECT entry_json FROM memory_entries WHERE memory_id = ?",
                    (str(memory_id),),
                )
                row = await cursor.fetchone()
                await cursor.close()
            if row is None:
                return failure(ErrorCode.NOT_FOUND, "Memory entry not found.", "memory.get")
            return KernelResult.success(MemoryEntry.from_json(str(row["entry_json"])))
        except Exception as exc:
            return failure(ErrorCode.INTERNAL, f"Failed to load memory: {exc}", "memory.get", retryable=True)

    async def put(self, entry: MemoryEntry) -> KernelResult[MemoryEntry]:
        if not str(entry.memory_id).strip() or not entry.namespace.strip() or not entry.key.strip():
            return failure(
                ErrorCode.INVALID_ARGUMENT,
                "Memory id, namespace, and key are required.",
                "memory.put",
            )
        if entry.updated_at < entry.created_at:
            return failure(
                ErrorCode.INVALID_ARGUMENT,
                "Memory updated_at cannot precede created_at.",
                "memory.put",
            )
        tags = tuple(dict.fromkeys(tag.strip() for tag in entry.tags if tag.strip()))
        try:
            async with self.database.write() as connection:
                cursor = await connection.execute(
                    "SELECT memory_id FROM memory_entries WHERE namespace = ? AND key = ?",
                    (entry.namespace, entry.key),
                )
                existing = await cursor.fetchone()
                await cursor.close()
                if existing is not None and str(existing["memory_id"]) != str(entry.memory_id):
                    return failure(
                        ErrorCode.CONFLICT,
                        "A different memory entry already uses this namespace and key.",
                        "memory.put",
                    )
                await connection.execute(
                    """
                    INSERT INTO memory_entries(
                        memory_id, namespace, key, entry_json, searchable_text,
                        tags_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(memory_id) DO UPDATE SET
                        namespace = excluded.namespace,
                        key = excluded.key,
                        entry_json = excluded.entry_json,
                        searchable_text = excluded.searchable_text,
                        tags_json = excluded.tags_json,
                        created_at = excluded.created_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(entry.memory_id),
                        entry.namespace,
                        entry.key,
                        entry.to_json(),
                        _searchable_text(entry),
                        json.dumps(tags, ensure_ascii=False, separators=(",", ":")),
                        entry.created_at.isoformat(),
                        entry.updated_at.isoformat(),
                    ),
                )
                await connection.execute("DELETE FROM memory_tags WHERE memory_id = ?", (str(entry.memory_id),))
                await connection.executemany(
                    "INSERT INTO memory_tags(memory_id, tag) VALUES (?, ?)",
                    ((str(entry.memory_id), tag) for tag in tags),
                )
            return KernelResult.success(entry)
        except Exception as exc:
            return failure(ErrorCode.INTERNAL, f"Failed to save memory: {exc}", "memory.put", retryable=True)

    async def delete(self, memory_id: MemoryId) -> KernelResult[bool]:
        if not str(memory_id).strip():
            return failure(ErrorCode.INVALID_ARGUMENT, "Memory id is required.", "memory.delete")
        try:
            async with self.database.write() as connection:
                cursor = await connection.execute(
                    "DELETE FROM memory_entries WHERE memory_id = ?",
                    (str(memory_id),),
                )
                deleted = cursor.rowcount > 0
                await cursor.close()
            if not deleted:
                return failure(ErrorCode.NOT_FOUND, "Memory entry not found.", "memory.delete")
            return KernelResult.success(True)
        except Exception as exc:
            return failure(ErrorCode.INTERNAL, f"Failed to delete memory: {exc}", "memory.delete", retryable=True)


def _fts_query(text: str) -> str:
    tokens = re.findall(r"\w+", text, flags=re.UNICODE)
    return " ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def _searchable_text(entry: MemoryEntry) -> str:
    parts = [entry.namespace, entry.key, *entry.tags]
    for block in entry.content:
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
        elif isinstance(block, ToolCallBlock):
            parts.extend((block.name, json.dumps(thaw_json(block.arguments), ensure_ascii=False, sort_keys=True)))
        elif isinstance(block, ToolResultBlock):
            parts.append(block.name)
            for nested in block.content:
                if isinstance(nested, (TextBlock, ReasoningBlock)):
                    parts.append(nested.text)
    return "\n".join(part for part in parts if part)
