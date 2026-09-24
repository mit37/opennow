from __future__ import annotations

from app.models import Language, SessionState
from app.session_store import (
    get_session,
    purge_expired,
    save_session,
    touch_or_create,
)
from tests.conftest import requires_db


def _phone_hash(tag: str) -> bytes:
    return f"phone-{tag}".encode().ljust(32, b"\0")[:32]


@requires_db
async def test_get_session_missing_returns_none(db_pool):
    result = await get_session(db_pool, _phone_hash("missing"))
    assert result is None


@requires_db
async def test_save_and_get_round_trip(db_pool):
    phone_hash = _phone_hash("create")
    state = SessionState(
        phone_hash=phone_hash,
        lang=Language.ES,
        last_query={"lat": 37.33, "lon": -121.88, "categories": ["food"], "offset": 0},
    )
    await save_session(db_pool, state, ttl_minutes=30)

    fetched = await get_session(db_pool, phone_hash)

    assert fetched is not None
    assert fetched.phone_hash == phone_hash
    assert fetched.lang == Language.ES
    assert fetched.last_query == {
        "lat": 37.33,
        "lon": -121.88,
        "categories": ["food"],
        "offset": 0,
    }


@requires_db
async def test_expired_row_treated_as_absent_and_deleted(db_pool):
    phone_hash = _phone_hash("expired")
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO session (phone_hash, lang, last_query, expires_at)
            VALUES ($1, 'en', '{}'::jsonb, now() - interval '5 minutes')
            """,
            phone_hash,
        )

    result = await get_session(db_pool, phone_hash)
    assert result is None

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM session WHERE phone_hash = $1", phone_hash)
    assert row is None


@requires_db
async def test_save_session_upsert_overwrites_existing_row(db_pool):
    phone_hash = _phone_hash("upsert")
    first = SessionState(phone_hash=phone_hash, lang=Language.EN, last_query={"offset": 0})
    await save_session(db_pool, first, ttl_minutes=30)

    second = SessionState(phone_hash=phone_hash, lang=Language.VI, last_query={"offset": 3})
    await save_session(db_pool, second, ttl_minutes=30)

    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM session WHERE phone_hash = $1", phone_hash)
    assert count == 1

    fetched = await get_session(db_pool, phone_hash)
    assert fetched is not None
    assert fetched.lang == Language.VI
    assert fetched.last_query == {"offset": 3}


@requires_db
async def test_last_query_jsonb_round_trips_nested_structures(db_pool):
    phone_hash = _phone_hash("nested")
    nested_query = {
        "lat": 37.3382,
        "lon": -121.8863,
        "categories": ["food", "shower", "dropin"],
        "offset": 3,
        "seen_ids": ["a1", "b2", "c3"],
        "meta": {"source": "zip", "ambiguous": False, "candidates": [1, 2, 3]},
    }
    state = SessionState(phone_hash=phone_hash, lang=Language.EN, last_query=nested_query)
    await save_session(db_pool, state, ttl_minutes=30)

    fetched = await get_session(db_pool, phone_hash)

    assert fetched is not None
    assert fetched.last_query == nested_query


@requires_db
async def test_touch_or_create_creates_new_session_with_empty_last_query(db_pool):
    phone_hash = _phone_hash("touch-new")

    state = await touch_or_create(db_pool, phone_hash, Language.VI, ttl_minutes=30)

    assert state.phone_hash == phone_hash
    assert state.lang == Language.VI
    assert state.last_query == {}

    fetched = await get_session(db_pool, phone_hash)
    assert fetched is not None
    assert fetched.lang == Language.VI
    assert fetched.last_query == {}


@requires_db
async def test_touch_or_create_extends_ttl_and_preserves_existing_content(db_pool):
    phone_hash = _phone_hash("touch-existing")
    original = SessionState(
        phone_hash=phone_hash, lang=Language.ES, last_query={"offset": 3, "categories": ["pantry"]}
    )
    await save_session(db_pool, original, ttl_minutes=30)

    async with db_pool.acquire() as conn:
        before_expiry = await conn.fetchval(
            "SELECT expires_at FROM session WHERE phone_hash = $1", phone_hash
        )
        await conn.execute(
            "UPDATE session SET expires_at = now() + interval '1 minute' WHERE phone_hash = $1",
            phone_hash,
        )

    touched = await touch_or_create(db_pool, phone_hash, Language.EN, ttl_minutes=30)

    assert touched.lang == Language.ES
    assert touched.last_query == {"offset": 3, "categories": ["pantry"]}

    async with db_pool.acquire() as conn:
        after_expiry = await conn.fetchval(
            "SELECT expires_at FROM session WHERE phone_hash = $1", phone_hash
        )
    assert after_expiry > before_expiry


@requires_db
async def test_touch_or_create_does_not_revive_expired_session(db_pool):
    phone_hash = _phone_hash("touch-expired")
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO session (phone_hash, lang, last_query, expires_at)
            VALUES ($1, 'es', '{"offset": 9}'::jsonb, now() - interval '5 minutes')
            """,
            phone_hash,
        )

    touched = await touch_or_create(db_pool, phone_hash, Language.EN, ttl_minutes=30)

    assert touched.lang == Language.EN
    assert touched.last_query == {}


@requires_db
async def test_purge_expired_deletes_only_expired_rows_and_returns_count(db_pool):
    live_hash = _phone_hash("purge-live")
    expired_hash_1 = _phone_hash("purge-exp-1")
    expired_hash_2 = _phone_hash("purge-exp-2")

    await save_session(
        db_pool, SessionState(phone_hash=live_hash, lang=Language.EN, last_query={}), ttl_minutes=30
    )
    async with db_pool.acquire() as conn:
        for h in (expired_hash_1, expired_hash_2):
            await conn.execute(
                """
                INSERT INTO session (phone_hash, lang, last_query, expires_at)
                VALUES ($1, 'en', '{}'::jsonb, now() - interval '1 minute')
                """,
                h,
            )

    deleted = await purge_expired(db_pool)
    assert deleted == 2

    async with db_pool.acquire() as conn:
        remaining = await conn.fetch("SELECT phone_hash FROM session")
    remaining_hashes = {bytes(r["phone_hash"]) for r in remaining}
    assert remaining_hashes == {live_hash}
