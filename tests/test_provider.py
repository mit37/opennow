from __future__ import annotations

from datetime import date

from app.provider import find_registered_service, flag_closed_today
from tests.conftest import requires_db

TODAY = date(2026, 9, 23)


async def _seed_service(conn) -> str:
    org_id = await conn.fetchval("INSERT INTO organization (name) VALUES ('Provider Org') RETURNING id")
    loc_id = await conn.fetchval(
        """
        INSERT INTO location (organization_id, name, address, geom)
        VALUES ($1, 'Loc', '1 Test St', ST_SetSRID(ST_MakePoint(-121.88, 37.33), 4326)::geography)
        RETURNING id
        """,
        org_id,
    )
    service_id = await conn.fetchval(
        """
        INSERT INTO service (location_id, name, category, last_verified_at)
        VALUES ($1, 'Test Pantry', 'food', now())
        RETURNING id
        """,
        loc_id,
    )
    return str(service_id)


@requires_db
async def test_find_registered_service_returns_none_when_unregistered(db_pool):
    result = await find_registered_service(db_pool, b"not-a-provider")
    assert result is None


@requires_db
async def test_find_registered_service_matches_registration(db_pool):
    async with db_pool.acquire() as conn:
        service_id = await _seed_service(conn)
        await conn.execute(
            "INSERT INTO provider_registration (service_id, phone_hash) VALUES ($1, $2)",
            service_id,
            b"provider-phone",
        )

    result = await find_registered_service(db_pool, b"provider-phone")
    assert result == service_id


@requires_db
async def test_flag_closed_today_creates_exception_and_log(db_pool):
    async with db_pool.acquire() as conn:
        service_id = await _seed_service(conn)

    await flag_closed_today(db_pool, service_id, TODAY)

    async with db_pool.acquire() as conn:
        exception_row = await conn.fetchrow(
            "SELECT closed, reason FROM schedule_exception WHERE service_id = $1 AND date = $2",
            service_id,
            TODAY,
        )
        log_row = await conn.fetchrow(
            "SELECT closed_for FROM provider_closure_log WHERE service_id = $1", service_id
        )

    assert exception_row["closed"] is True
    assert "CLOSED TODAY" in exception_row["reason"]
    assert log_row is not None
    assert log_row["closed_for"] == TODAY


@requires_db
async def test_flag_closed_today_is_idempotent(db_pool):
    async with db_pool.acquire() as conn:
        service_id = await _seed_service(conn)

    await flag_closed_today(db_pool, service_id, TODAY)
    await flag_closed_today(db_pool, service_id, TODAY)

    async with db_pool.acquire() as conn:
        exception_count = await conn.fetchval(
            "SELECT count(*) FROM schedule_exception WHERE service_id = $1 AND date = $2",
            service_id,
            TODAY,
        )
        log_count = await conn.fetchval(
            "SELECT count(*) FROM provider_closure_log WHERE service_id = $1", service_id
        )

    assert exception_count == 1
    assert log_count == 2
