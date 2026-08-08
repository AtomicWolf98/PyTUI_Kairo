from __future__ import annotations

import asyncio

from kairo_kernel.storage.database import SCHEMA_VERSION, SQLiteDatabase


def test_database_uses_wal_and_runs_schema_once(tmp_path) -> None:
    async def exercise() -> None:
        database = SQLiteDatabase(tmp_path / "kernel.db")
        assert await database.open() is database
        assert await database.open() is database
        assert await database.journal_mode() == "wal"
        assert await database.schema_version() == SCHEMA_VERSION
        async with database.read() as connection:
            cursor = await connection.execute("PRAGMA foreign_keys")
            row = await cursor.fetchone()
            assert row is not None and int(row[0]) == 1
        await database.close()
        await database.close()

    asyncio.run(exercise())


def test_write_transaction_rolls_back(tmp_path) -> None:
    async def exercise() -> None:
        database = SQLiteDatabase(tmp_path / "kernel.db")
        try:
            async with database.write() as connection:
                await connection.execute(
                    "INSERT INTO kernel_schema(version) VALUES (99)"
                )
                raise RuntimeError("rollback")
        except RuntimeError:
            pass
        async with database.read() as connection:
            cursor = await connection.execute("SELECT COUNT(*) FROM kernel_schema WHERE version = 99")
            row = await cursor.fetchone()
            assert row is not None and int(row[0]) == 0
        await database.close()

    asyncio.run(exercise())
