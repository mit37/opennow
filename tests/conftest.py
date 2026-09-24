from __future__ import annotations

import asyncio
import glob
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
        # Idempotent-ish for test runs: wipe and recreate the public schema.
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        # Supabase provides auth.users in production; stand in a minimal copy
        # here so migrations that FK into it (e.g. admin_user) apply cleanly
        # against a plain local/CI Postgres instance.
        await conn.execute("CREATE SCHEMA IF NOT EXISTS auth;")
        await conn.execute("CREATE TABLE IF NOT EXISTS auth.users (id uuid PRIMARY KEY);")
        for path in sorted(glob.glob("migrations/*.sql")):
            with open(path) as f:
                await conn.execute(f.read())
    yield pool
    await pool.close()
