from __future__ import annotations

from datetime import date, time

import pytest
from cryptography.fernet import Fernet

from app import templates
from app.config import get_settings
from app.router import handle_message
from app.security import decrypt_phone
from tests.conftest import requires_db

FROM_NUMBER = "+14085551212"


@pytest.fixture(autouse=True)
def _alert_key(monkeypatch):
    monkeypatch.setenv("ALERT_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _seed_open_service(conn, name: str, lat: float, lon: float):
    # Open every day of the week (00:00-23:59) so this test isn't date-bombed
    # by only being scheduled on whatever weekday it happened to be written.
    org_id = await conn.fetchval(
        "INSERT INTO organization (name) VALUES ($1) RETURNING id", "Test Org"
    )
    loc_id = await conn.fetchval(
        """
        INSERT INTO location (organization_id, name, address, geom)
        VALUES ($1, $2, $3, ST_SetSRID(ST_MakePoint($4, $5), 4326)::geography)
        RETURNING id
        """,
        org_id,
        name,
        "123 Test St, San Jose, CA",
        lon,
        lat,
    )
    service_id = await conn.fetchval(
        """
        INSERT INTO service (location_id, name, category, last_verified_at)
        VALUES ($1, $2, 'food', now())
        RETURNING id
        """,
        loc_id,
        name,
    )
    for weekday in range(7):
        await conn.execute(
            "INSERT INTO schedule (service_id, weekday, opens, closes) VALUES ($1, $2, $3, $4)",
            service_id,
            weekday,
            time(0, 0),
            time(23, 59),
        )
    return service_id


async def _seed_zip(conn, zip_code: str, lat: float, lon: float):
    await conn.execute(
        """
        INSERT INTO zip_centroid (zip, geom)
        VALUES ($1, ST_SetSRID(ST_MakePoint($2, $3), 4326)::geography)
        """,
        zip_code,
        lon,
        lat,
    )


@requires_db
async def test_help_intent(db_pool):
    reply = await handle_message(db_pool, FROM_NUMBER, "HELP")
    assert reply == templates.render_help(templates.Language.EN)


@requires_db
async def test_stop_then_start(db_pool):
    stop_reply = await handle_message(db_pool, FROM_NUMBER, "STOP")
    assert stop_reply == templates.render_stop_ack(templates.Language.EN)

    start_reply = await handle_message(db_pool, FROM_NUMBER, "START")
    assert start_reply == templates.render_start_ack(templates.Language.EN)


@requires_db
async def test_crisis_intent_returns_crisis_prefix(db_pool):
    reply = await handle_message(db_pool, FROM_NUMBER, "I want to kill myself")
    assert reply == templates.render_crisis_prefix(templates.Language.EN)


@requires_db
async def test_shelter_intent_mentions_here4you(db_pool):
    reply = await handle_message(db_pool, FROM_NUMBER, "SHELTER")
    assert get_settings().here4you_phone in reply


@requires_db
async def test_unknown_gibberish_falls_back_to_help(db_pool):
    reply = await handle_message(db_pool, FROM_NUMBER, "???...")
    assert reply == templates.render_help(templates.Language.EN)


@requires_db
async def test_location_query_by_zip_finds_open_service(db_pool):
    async with db_pool.acquire() as conn:
        await _seed_zip(conn, "95112", 37.3382, -121.8863)
        await _seed_open_service(conn, "Sacred Heart Pantry", 37.3390, -121.8870)

    reply = await handle_message(db_pool, FROM_NUMBER, "food 95112")
    assert "Sacred Heart Pantry" in reply
    assert "MORE" in reply


@requires_db
async def test_location_query_unknown_zip(db_pool):
    reply = await handle_message(db_pool, FROM_NUMBER, "88888")
    assert reply == templates.render_unknown_zip(templates.Language.EN)


@requires_db
async def test_more_with_no_prior_query_says_nothing_more(db_pool):
    reply = await handle_message(db_pool, FROM_NUMBER, "MORE")
    assert reply == templates.render_no_more_results(templates.Language.EN)


@requires_db
async def test_alerts_double_opt_in_flow_creates_subscription(db_pool):
    prompt = await handle_message(db_pool, FROM_NUMBER, "ALERTS 95112")
    assert "95112" in prompt

    confirm = await handle_message(db_pool, FROM_NUMBER, "YES")
    assert confirm == templates.render_alerts_confirmed(templates.Language.EN)

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT phone_e164_encrypted, zip, opted_out_at FROM alert_subscription"
        )
    assert row is not None
    assert row["zip"] == "95112"
    assert row["opted_out_at"] is None
    assert decrypt_phone(bytes(row["phone_e164_encrypted"])) == FROM_NUMBER


@requires_db
async def test_stop_opts_out_of_alerts(db_pool):
    await handle_message(db_pool, FROM_NUMBER, "ALERTS 95112")
    await handle_message(db_pool, FROM_NUMBER, "YES")
    await handle_message(db_pool, FROM_NUMBER, "STOP")

    async with db_pool.acquire() as conn:
        opted_out_at = await conn.fetchval(
            "SELECT opted_out_at FROM alert_subscription WHERE phone_hash = "
            "(SELECT phone_hash FROM alert_subscription LIMIT 1)"
        )
    assert opted_out_at is not None


@requires_db
async def test_every_message_writes_an_event_log_row(db_pool):
    await handle_message(db_pool, FROM_NUMBER, "HELP")
    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM event_log")
    assert count == 1


def test_day_label_helper_smoke():
    # Not DB-dependent: sanity check templates still import cleanly alongside router.
    assert templates._day_label(date(2026, 1, 1), templates.Language.EN, today=date(2026, 1, 1)) == "today"
