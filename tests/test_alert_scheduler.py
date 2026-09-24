from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.models import Language
from app.security import encrypt_phone
from jobs.alert_scheduler import (
    can_send_alert,
    format_alert_body,
    group_by_zip,
    is_quiet_hours,
    run,
)
from tests.conftest import requires_db

_LA = ZoneInfo("America/Los_Angeles")
_ALERT_SEND_LOG_SQL = Path("migrations/003_alert_send_log.sql").read_text(encoding="utf-8")


@pytest.fixture
def settings(monkeypatch) -> Settings:
    s = Settings(
        alert_encryption_key=Fernet.generate_key().decode(),
        phone_hash_secret="test-secret-for-alert-scheduler",
        twilio_from_number="+14085550000",
    )
    monkeypatch.setattr("jobs.alert_scheduler.get_settings", lambda: s)
    monkeypatch.setattr("app.security.get_settings", lambda: s)
    return s


# ── is_quiet_hours ──────────────────────────────────────────────────────────


def test_is_quiet_hours_true_at_10pm_pacific():
    moment = datetime(2026, 1, 5, 22, 0, tzinfo=_LA)
    assert is_quiet_hours(moment) is True


def test_is_quiet_hours_true_at_3am_pacific():
    moment = datetime(2026, 1, 5, 3, 0, tzinfo=_LA)
    assert is_quiet_hours(moment) is True


def test_is_quiet_hours_false_at_noon_pacific():
    moment = datetime(2026, 1, 5, 12, 0, tzinfo=_LA)
    assert is_quiet_hours(moment) is False


def test_is_quiet_hours_boundary_8am_is_not_quiet():
    moment = datetime(2026, 1, 5, 8, 0, tzinfo=_LA)
    assert is_quiet_hours(moment) is False


def test_is_quiet_hours_boundary_9pm_is_quiet():
    moment = datetime(2026, 1, 5, 21, 0, tzinfo=_LA)
    assert is_quiet_hours(moment) is True


def test_is_quiet_hours_converts_from_utc():
    # 05:30 UTC in January is 21:30 the prior day in Los_Angeles (UTC-8) -> quiet.
    moment = datetime(2026, 1, 5, 5, 30, tzinfo=UTC)
    assert is_quiet_hours(moment) is True
    # 18:00 UTC in January is 10:00 Los_Angeles -> not quiet.
    moment = datetime(2026, 1, 5, 18, 0, tzinfo=UTC)
    assert is_quiet_hours(moment) is False


# ── group_by_zip ─────────────────────────────────────────────────────────────


def test_group_by_zip_groups_multiple_subscribers_same_zip():
    subs = [
        {"zip": "95112", "phone_hash": b"a"},
        {"zip": "95112", "phone_hash": b"b"},
        {"zip": "94085", "phone_hash": b"c"},
    ]
    groups = group_by_zip(subs)
    assert set(groups.keys()) == {"95112", "94085"}
    assert len(groups["95112"]) == 2
    assert len(groups["94085"]) == 1


def test_group_by_zip_empty_input():
    assert group_by_zip([]) == {}


# ── can_send_alert (3-per-week cap, FR-8) ────────────────────────────────────


def test_can_send_alert_under_cap():
    assert can_send_alert(0) is True
    assert can_send_alert(2) is True


def test_can_send_alert_at_or_over_cap():
    assert can_send_alert(3) is False
    assert can_send_alert(4) is False


def test_can_send_alert_custom_cap():
    assert can_send_alert(1, cap=1) is False
    assert can_send_alert(0, cap=1) is True


# ── format_alert_body ────────────────────────────────────────────────────────


def test_format_alert_body_includes_zip_and_all_listings():
    listings = [
        {"name": "Test Pantry", "address": "1 Test St"},
        {"name": "Test Shelter", "address": "2 Test Ave"},
    ]
    body = format_alert_body(Language.EN, listings, "95112")
    assert "95112" in body
    assert "Test Pantry" in body
    assert "Test Shelter" in body


def test_format_alert_body_varies_by_language():
    listings = [{"name": "Test Pantry", "address": "1 Test St"}]
    en = format_alert_body(Language.EN, listings, "95112")
    es = format_alert_body(Language.ES, listings, "95112")
    vi = format_alert_body(Language.VI, listings, "95112")
    assert en != es
    assert en != vi
    assert es != vi


# ── run() orchestration, fully faked pool + Twilio client ──────────────────


class FakeTwilioMessages:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def create(self, to, from_, body):
        self.sent.append({"to": to, "from_": from_, "body": body})


class FakeTwilioClient:
    def __init__(self) -> None:
        self.messages = FakeTwilioMessages()


class _FakeConn:
    def __init__(self, subscriptions, listings_by_zip, send_counts):
        self._subscriptions = subscriptions
        self._listings_by_zip = listings_by_zip
        self.send_counts = dict(send_counts)
        self.inserts: list[bytes] = []

    async def fetch(self, query, *args):
        if "FROM alert_subscription" in query:
            return self._subscriptions
        if "zip_centroid" in query:
            zip_code = args[0]
            return self._listings_by_zip.get(zip_code, [])
        raise AssertionError(f"unexpected fetch query: {query!r}")

    async def fetchval(self, query, *args):
        if "alert_send_log" in query:
            phone_hash = args[0]
            return self.send_counts.get(phone_hash, 0)
        raise AssertionError(f"unexpected fetchval query: {query!r}")

    async def execute(self, query, *args):
        if "INSERT INTO alert_send_log" in query:
            phone_hash = args[0]
            self.inserts.append(phone_hash)
            self.send_counts[phone_hash] = self.send_counts.get(phone_hash, 0) + 1
            return
        raise AssertionError(f"unexpected execute query: {query!r}")


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, subscriptions, listings_by_zip, send_counts=None):
        self.conn = _FakeConn(subscriptions, listings_by_zip, send_counts or {})

    def acquire(self):
        return _FakeAcquire(self.conn)


def test_run_returns_immediately_and_touches_nothing_during_quiet_hours(monkeypatch, settings):
    monkeypatch.setattr("jobs.alert_scheduler.is_quiet_hours", lambda moment: True)

    class ExplodingPool:
        def acquire(self):
            raise AssertionError("must not touch the DB during quiet hours")

    client = FakeTwilioClient()
    result = asyncio.run(run(ExplodingPool(), twilio_client=client))

    assert result == {
        "sent": 0,
        "skipped_quiet_hours": True,
        "skipped_cap": 0,
        "skipped_no_listings": 0,
        "subscribers_considered": 0,
    }
    assert client.messages.sent == []


def test_run_sends_one_alert_per_subscriber_under_cap(monkeypatch, settings):
    monkeypatch.setattr("jobs.alert_scheduler.is_quiet_hours", lambda moment: False)

    phone_a, phone_b = "+14085550101", "+14085550102"
    subs = [
        {
            "phone_hash": b"hash-a",
            "phone_e164_encrypted": encrypt_phone(phone_a),
            "zip": "95112",
            "lang": "en",
        },
        {
            "phone_hash": b"hash-b",
            "phone_e164_encrypted": encrypt_phone(phone_b),
            "zip": "95112",
            "lang": "es",
        },
    ]
    listings_by_zip = {
        "95112": [{"name": "Pop-up Pantry", "address": "1 Test St, San Jose, CA 95112"}]
    }
    pool = _FakePool(subs, listings_by_zip)
    client = FakeTwilioClient()

    result = asyncio.run(run(pool, twilio_client=client))

    assert result["sent"] == 2
    assert result["skipped_cap"] == 0
    assert result["skipped_no_listings"] == 0
    assert result["subscribers_considered"] == 2
    assert len(client.messages.sent) == 2

    sent_to = {m["to"] for m in client.messages.sent}
    assert sent_to == {phone_a, phone_b}
    for message in client.messages.sent:
        assert message["from_"] == "+14085550000"
        assert "Pop-up Pantry" in message["body"]

    # exactly one alert-send row recorded per subscriber
    assert pool.conn.send_counts[b"hash-a"] == 1
    assert pool.conn.send_counts[b"hash-b"] == 1


def test_run_skips_subscriber_already_at_weekly_cap(monkeypatch, settings):
    monkeypatch.setattr("jobs.alert_scheduler.is_quiet_hours", lambda moment: False)

    phone = "+14085550199"
    subs = [
        {
            "phone_hash": b"hash-capped",
            "phone_e164_encrypted": encrypt_phone(phone),
            "zip": "95112",
            "lang": "en",
        }
    ]
    listings_by_zip = {"95112": [{"name": "Pop-up Pantry", "address": "1 Test St"}]}
    pool = _FakePool(subs, listings_by_zip, send_counts={b"hash-capped": 3})
    client = FakeTwilioClient()

    result = asyncio.run(run(pool, twilio_client=client))

    assert result["sent"] == 0
    assert result["skipped_cap"] == 1
    assert client.messages.sent == []


def test_run_skips_zip_with_no_recent_listings(monkeypatch, settings):
    monkeypatch.setattr("jobs.alert_scheduler.is_quiet_hours", lambda moment: False)

    subs = [
        {
            "phone_hash": b"hash-x",
            "phone_e164_encrypted": encrypt_phone("+14085550150"),
            "zip": "95999",
            "lang": "en",
        }
    ]
    pool = _FakePool(subs, listings_by_zip={})
    client = FakeTwilioClient()

    result = asyncio.run(run(pool, twilio_client=client))

    assert result["sent"] == 0
    assert result["skipped_no_listings"] == 1
    assert client.messages.sent == []


def test_run_works_without_a_twilio_client_injected(monkeypatch, settings):
    monkeypatch.setattr("jobs.alert_scheduler.is_quiet_hours", lambda moment: False)

    subs = [
        {
            "phone_hash": b"hash-y",
            "phone_e164_encrypted": encrypt_phone("+14085550160"),
            "zip": "95112",
            "lang": "en",
        }
    ]
    listings_by_zip = {"95112": [{"name": "Pop-up Pantry", "address": "1 Test St"}]}
    pool = _FakePool(subs, listings_by_zip)

    result = asyncio.run(run(pool, twilio_client=None))

    assert result["sent"] == 1
    assert pool.conn.send_counts[b"hash-y"] == 1


# ── requires_db: real Postgres/PostGIS round trip ───────────────────────────


@requires_db
async def test_run_against_real_postgres(db_pool, monkeypatch, settings):
    monkeypatch.setattr("jobs.alert_scheduler.is_quiet_hours", lambda moment: False)

    async with db_pool.acquire() as conn:
        await conn.execute(_ALERT_SEND_LOG_SQL)

        await conn.execute(
            """
            INSERT INTO zip_centroid (zip, geom)
            VALUES ('95112', ST_SetSRID(ST_MakePoint(-121.88, 37.33), 4326)::geography)
            """
        )
        org_id = await conn.fetchval(
            "INSERT INTO organization (name) VALUES ('Test Org') RETURNING id"
        )
        loc_id = await conn.fetchval(
            """
            INSERT INTO location (organization_id, name, address, geom)
            VALUES ($1, 'Pop-up Pantry', '1 Test St, San Jose, CA 95112',
                    ST_SetSRID(ST_MakePoint(-121.88, 37.33), 4326)::geography)
            RETURNING id
            """,
            org_id,
        )
        await conn.execute(
            "INSERT INTO service (location_id, name, category) VALUES ($1, 'Pop-up Food', 'food')",
            loc_id,
        )

        phone = "+14085550170"
        phone_hash = b"\x01" * 32
        await conn.execute(
            """
            INSERT INTO alert_subscription (phone_e164_encrypted, phone_hash, zip, lang)
            VALUES ($1, $2, '95112', 'en')
            """,
            encrypt_phone(phone),
            phone_hash,
        )

    client = FakeTwilioClient()
    result = await run(db_pool, twilio_client=client)

    assert result["sent"] == 1
    assert len(client.messages.sent) == 1
    assert client.messages.sent[0]["to"] == phone

    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM alert_send_log WHERE phone_hash = $1", phone_hash)
    assert count == 1

    # A second run within the same week should still send (1 < cap of 3).
    client2 = FakeTwilioClient()
    result2 = await run(db_pool, twilio_client=client2)
    assert result2["sent"] == 1

    # Manually push this subscriber to the cap and verify the third run skips them.
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO alert_send_log (phone_hash) VALUES ($1)", phone_hash
        )
    client3 = FakeTwilioClient()
    result3 = await run(db_pool, twilio_client=client3)
    assert result3["sent"] == 0
    assert result3["skipped_cap"] == 1
