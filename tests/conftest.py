from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest

DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://opennow:opennow@localhost:5432/opennow_test",
)


def _db_reachable() -> bool:
    async def _try() -> bool:
        try:
            conn = await asyncpg.connect(DATABASE_URL, timeout=1.5)
        except Exception:
            return False
        await conn.close()
        return True

    return asyncio.run(_try())


DB_AVAILABLE = _db_reachable()

requires_db = pytest.mark.skipif(
    not DB_AVAILABLE,
    reason="no Postgres/PostGIS reachable at TEST_DATABASE_URL (start `docker compose up -d db`)",
)


@pytest.fixture
async def db_pool():
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4)
    async with pool.acquire() as conn:
        with open("migrations/001_init.sql") as f:
            schema_sql = f.read()
        # Idempotent-ish for test runs: wipe and recreate the public schema.
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        await conn.execute(schema_sql)
    yield pool
    await pool.close()
