from __future__ import annotations

from datetime import date

import asyncpg

_FIND_SERVICE_SQL = """
    SELECT service_id FROM provider_registration WHERE phone_hash = $1
"""

_UPSERT_EXCEPTION_SQL = """
    INSERT INTO schedule_exception (service_id, date, closed, reason)
    VALUES ($1, $2, true, 'Provider texted CLOSED TODAY')
    ON CONFLICT (service_id, date) DO UPDATE
    SET closed = true, opens = NULL, closes = NULL, reason = EXCLUDED.reason
"""

_LOG_CLOSURE_SQL = """
    INSERT INTO provider_closure_log (service_id, closed_for) VALUES ($1, $2)
"""


async def find_registered_service(pool: asyncpg.Pool, phone_hash: bytes) -> str | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_FIND_SERVICE_SQL, phone_hash)
    return str(row["service_id"]) if row is not None else None


async def flag_closed_today(pool: asyncpg.Pool, service_id: str, local_date: date) -> None:
    # `local_date` must be the caller's America/Los_Angeles "today" — Postgres's
    # own CURRENT_DATE resolves in the server's timezone (typically UTC), which
    # would mark the wrong calendar day closed near local midnight.
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(_UPSERT_EXCEPTION_SQL, service_id, local_date)
            await conn.execute(_LOG_CLOSURE_SQL, service_id, local_date)
