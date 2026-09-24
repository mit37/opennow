from __future__ import annotations

import json

import asyncpg

from app.models import Language, SessionState

# `now()` is evaluated once per statement (stable within it), so the DELETE and
# SELECT below agree on what "expired" means even though they don't share a
# CTE data dependency — no python-side clock is involved.
_GET_OR_EXPIRE_SQL = """
    WITH expired AS (
        DELETE FROM session WHERE phone_hash = $1 AND expires_at < now()
    )
    SELECT phone_hash, lang, last_query, expires_at
    FROM session
    WHERE phone_hash = $1 AND expires_at >= now()
"""

_UPSERT_SQL = """
    INSERT INTO session (phone_hash, lang, last_query, expires_at)
    VALUES ($1, $2, $3::jsonb, now() + ($4 * interval '1 minute'))
    ON CONFLICT (phone_hash) DO UPDATE
    SET lang = EXCLUDED.lang,
        last_query = EXCLUDED.last_query,
        expires_at = EXCLUDED.expires_at
"""

_EXTEND_SQL = """
    UPDATE session
    SET expires_at = now() + ($2 * interval '1 minute')
    WHERE phone_hash = $1 AND expires_at >= now()
    RETURNING phone_hash, lang, last_query, expires_at
"""

_PURGE_SQL = "DELETE FROM session WHERE expires_at < now()"


def _row_to_state(row: asyncpg.Record) -> SessionState:
    raw_last_query = row["last_query"]
    last_query = json.loads(raw_last_query) if raw_last_query else {}
    return SessionState(
        phone_hash=bytes(row["phone_hash"]),
        lang=Language(row["lang"]),
        last_query=last_query,
    )


def _parse_delete_count(command_tag: str) -> int:
    parts = command_tag.split()
    return int(parts[-1]) if parts else 0


async def get_session(pool: asyncpg.Pool, phone_hash: bytes) -> SessionState | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_GET_OR_EXPIRE_SQL, phone_hash)
    if row is None:
        return None
    return _row_to_state(row)


async def save_session(pool: asyncpg.Pool, state: SessionState, ttl_minutes: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            _UPSERT_SQL,
            state.phone_hash,
            state.lang.value,
            json.dumps(state.last_query),
            ttl_minutes,
        )


async def touch_or_create(
    pool: asyncpg.Pool, phone_hash: bytes, lang: Language, ttl_minutes: int
) -> SessionState:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_EXTEND_SQL, phone_hash, ttl_minutes)
    if row is not None:
        return _row_to_state(row)

    state = SessionState(phone_hash=phone_hash, lang=lang, last_query={})
    await save_session(pool, state, ttl_minutes)
    return state


async def purge_expired(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        command_tag = await conn.execute(_PURGE_SQL)
    return _parse_delete_count(command_tag)
