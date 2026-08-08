from __future__ import annotations

import asyncio
import json

from kairo_kernel.contracts.content import TextBlock, ToolCallBlock, ToolResultBlock
from kairo_kernel.contracts.enums import ErrorCode, MessageKind
from kairo_kernel.contracts.identifiers import SessionId
from kairo_kernel.migrate import LegacyJsonImporter
from kairo_kernel.storage import SQLiteDatabase, SQLiteSessionRepository


def _write_json(path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _legacy_session(identifier: str) -> dict[str, object]:
    return {
        "version": 1,
        "id": identifier,
        "name": "Legacy 中文",
        "created_at": "2026-08-08T10:00:00Z",
        "updated_at": "2026-08-08T10:01:00Z",
        "workspace_root": "C:/workspace",
        "model_profile": "provider/model",
        "compression_count": 2,
        "token_usage": {"context_used_tokens": 321},
        "history": [
            {"role": "system", "content": "system"},
            {
                "role": "system",
                "name": "kairo_runtime_state",
                "content": "runtime",
            },
            {"role": "user", "content": "read"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path":"a.txt"}'},
                    }
                ],
            },
            {
                "role": "tool",
                "name": "read_file",
                "tool_call_id": "call-1",
                "content": "contents",
            },
            {"role": "assistant", "content": "done"},
        ],
    }


def test_imports_indexed_session_active_marker_usage_and_tools(tmp_path) -> None:
    async def exercise() -> None:
        legacy = tmp_path / "legacy"
        legacy.mkdir()
        identifier = "a" * 32
        _write_json(
            legacy / "index.json",
            {
                "version": 1,
                "active_session_id": identifier,
                "sessions": [{"id": identifier, "file": f"{identifier}.json"}],
            },
        )
        _write_json(legacy / f"{identifier}.json", _legacy_session(identifier))
        database = SQLiteDatabase(tmp_path / "kernel.db")
        repository = SQLiteSessionRepository(database)
        importer = LegacyJsonImporter(repository)
        result = await importer.import_directory(legacy)
        assert result.ok and result.value is not None and len(result.value) == 1
        assert importer.active_session_id == SessionId(identifier)
        record = result.value[0]
        assert record.name == "Legacy 中文"
        assert record.compression_count == 2
        assert record.messages[1].kind is MessageKind.RUNTIME_STATE
        assert any(isinstance(block, ToolCallBlock) for block in record.messages[3].content)
        assert isinstance(record.messages[4].content[0], ToolResultBlock)
        summaries = await repository.list()
        assert summaries[0].context_used_tokens == 321
        await database.close()

    asyncio.run(exercise())


def test_corrupt_index_recovers_orphan_and_skips_bad_files(tmp_path) -> None:
    async def exercise() -> None:
        legacy = tmp_path / "legacy"
        legacy.mkdir()
        identifier = "b" * 32
        (legacy / "index.json").write_text("{broken", encoding="utf-8")
        _write_json(legacy / f"{identifier}.json", _legacy_session(identifier))
        _write_json(legacy / "unsafe.json", {"id": "unsafe"})
        database = SQLiteDatabase(tmp_path / "kernel.db")
        importer = LegacyJsonImporter(SQLiteSessionRepository(database))
        result = await importer.import_directory(legacy)
        assert result.ok and result.value is not None
        assert result.value[0].session_id == SessionId(identifier)
        assert importer.active_session_id == SessionId(identifier)
        assert any("Failed to load legacy session index" in item for item in importer.warnings)
        assert any("Invalid orphan session filename" in item for item in importer.warnings)
        await database.close()

    asyncio.run(exercise())


def test_invalid_legacy_sessions_return_typed_failure(tmp_path) -> None:
    async def exercise() -> None:
        legacy = tmp_path / "legacy"
        legacy.mkdir()
        identifier = "c" * 32
        _write_json(
            legacy / "index.json",
            {"active_session_id": identifier, "sessions": [{"id": identifier}]},
        )
        _write_json(
            legacy / f"{identifier}.json",
            {"id": identifier, "history": [{"role": "user", "content": "no system"}]},
        )
        database = SQLiteDatabase(tmp_path / "kernel.db")
        importer = LegacyJsonImporter(SQLiteSessionRepository(database))
        result = await importer.import_directory(legacy)
        assert result.error is not None and result.error.code is ErrorCode.NOT_FOUND
        assert any("does not start with a system message" in item for item in importer.warnings)
        await database.close()

    asyncio.run(exercise())


def test_legacy_message_ids_are_deterministic(tmp_path) -> None:
    async def exercise() -> None:
        legacy = tmp_path / "legacy"
        legacy.mkdir()
        identifier = "d" * 32
        _write_json(legacy / f"{identifier}.json", _legacy_session(identifier))
        first_db = SQLiteDatabase(tmp_path / "first.db")
        second_db = SQLiteDatabase(tmp_path / "second.db")
        first = await LegacyJsonImporter(SQLiteSessionRepository(first_db)).import_directory(legacy)
        second = await LegacyJsonImporter(SQLiteSessionRepository(second_db)).import_directory(legacy)
        assert first.value is not None and second.value is not None
        assert [message.message_id for message in first.value[0].messages] == [
            message.message_id for message in second.value[0].messages
        ]
        assert isinstance(first.value[0].messages[0].content[0], TextBlock)
        await first_db.close()
        await second_db.close()

    asyncio.run(exercise())
