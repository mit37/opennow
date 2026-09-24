from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

from app.config import get_settings
from app.followup import (
    FOLLOWUP_BODY,
    is_eligible_for_followup,
    maybe_schedule,
    record_response,
    send_due,
)
from app.models import Language
from app.security import encrypt_phone
from tests.conftest import requires_db

PHONE = "+14085551212"


@pytest.fixture(autouse=True)
def _alert_key(monkeypatch):
    monkeypatch.setenv("ALERT_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _phone_hash(tag: str) -> bytes:
    return f"phone-{tag}".encode().ljust(32, b"\0")[:32]


# ── is_eligible_for_followup (pure, FR-11 opt-out + weekly-cap rules) ───────


def test_eligible_when_no_alert_subscription_and_no_recent_followup():
    assert is_eligible_for_followup(False, False) is True


def test_ineligible_when_active_alert_subscriber():
    assert is_eligible_for_followup(True, False) is False


def test_ineligible_when_within_weekly_cap():
    assert is_eligible_for_followup(False, True) is False


def test_ineligible_when_both_active_alert_subscriber_and_within_cap():
    assert is_eligible_for_followup(True, True) is False


# ── FOLLOWUP_BODY ────────────────────────────────────────────────────────────


def test_followup_body_covers_all_languages():
    assert set(FOLLOWUP_BODY.keys()) == {Language.EN, Language.ES, Language.VI}


def test_followup_body_varies_by_language():
    en, es, vi = (
        FOLLOWUP_BODY[Language.EN],
        FOLLOWUP_BODY[Language.ES],
        FOLLOWUP_BODY[Language.VI],
    )
    assert en != es
    assert en != vi
    assert es != vi


# ── send_due, fully faked pool + Twilio client ──────────────────────────────


class FakeTwilioMessages:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def create(self, to, from_, body):
        self.sent.append({"to": to, "from_": from_, "body": body})


class FakeTwilioClient:
    def __init__(self) -> None:
        self.messages = FakeTwilioMessages()


class _FakeConn:
    def __init__(self, due_rows):
        self._due_rows = due_rows
        self.executed: list[tuple] = []

    async def fetch(self, query, *args):
        if "FROM followup_queue" in query:
            return self._due_rows
        raise AssertionError(f"unexpected fetch query: {query!r}")

    async def execute(self, query, *args):
        self.executed.append((query, args))


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, due_rows):
        self.conn = _FakeConn(due_rows)

    def acquire(self):
        return _FakeAcquire(self.conn)


def test_send_due_sends_and_purges_encrypted_phone():
    due_rows = [
        {
            "id": "row-1",
            "phone_hash": b"a" * 32,
            "phone_e164_encrypted": encrypt_phone(PHONE),
            "service_name": "Test Pantry",
            "lang": "en",
            "expired": False,
        },
    ]
    pool = _FakePool(due_rows)
    client = FakeTwilioClient()

    result = asyncio.run(send_due(pool, twilio_client=client))

    assert result == {"sent": 1, "expired": 0, "due": 1}
    assert client.messages.sent == [
        {"to": PHONE, "from_": "", "body": FOLLOWUP_BODY[Language.EN]}
    ]
    assert len(pool.conn.executed) == 1
    query, args = pool.conn.executed[0]
    assert "delivered = true" in query
    assert args == ("row-1",)


def test_send_due_expires_stale_rows_without_sending():
    due_rows = [
        {
            "id": "row-1",
            "phone_hash": b"a" * 32,
            "phone_e164_encrypted": encrypt_phone(PHONE),
            "service_name": "Test Pantry",
            "lang": "en",
            "expired": True,
        },
    ]
    pool = _FakePool(due_rows)
    client = FakeTwilioClient()

    result = asyncio.run(send_due(pool, twilio_client=client))

    assert result == {"sent": 0, "expired": 1, "due": 1}
    assert client.messages.sent == []
    query, args = pool.conn.executed[0]
    assert "delivered = false" in query


def test_send_due_handles_no_due_rows():
    pool = _FakePool([])
    client = FakeTwilioClient()

    result = asyncio.run(send_due(pool, twilio_client=client))

    assert result == {"sent": 0, "expired": 0, "due": 0}
    assert client.messages.sent == []


# ── maybe_schedule, requires_db ──────────────────────────────────────────────


@requires_db
async def test_maybe_schedule_skips_active_alert_subscriber(db_pool):
    phone_hash = _phone_hash("alerts-active")
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO alert_subscription (phone_e164_encrypted, phone_hash, zip, lang)
            VALUES ($1, $2, '95112', 'en')
            """,
            encrypt_phone(PHONE),
            phone_hash,
        )

    scheduled = await maybe_schedule(db_pool, phone_hash, PHONE, "Test Pantry", Language.EN)

    assert scheduled is False
    async with db_pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM followup_queue WHERE phone_hash = $1", phone_hash
        )
    assert count == 0


@requires_db
async def test_maybe_schedule_allows_when_alert_subscriber_opted_out(db_pool):
    phone_hash = _phone_hash("alerts-opted-out")
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO alert_subscription (phone_e164_encrypted, phone_hash, zip, lang, opted_out_at)
            VALUES ($1, $2, '95112', 'en', now())
            """,
            encrypt_phone(PHONE),
            phone_hash,
        )

    scheduled = await maybe_schedule(db_pool, phone_hash, PHONE, "Test Pantry", Language.EN)

    assert scheduled is True


@requires_db
async def test_maybe_schedule_skips_within_weekly_cap(db_pool):
    phone_hash = _phone_hash("weekly-cap")
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO followup_queue
                (phone_hash, phone_e164_encrypted, service_name, lang, scheduled_at, created_at)
            VALUES ($1, $2, 'Earlier Pantry', 'en', now() + interval '3 hours', now() - interval '1 day')
            """,
            phone_hash,
            encrypt_phone(PHONE),
        )

    scheduled = await maybe_schedule(db_pool, phone_hash, PHONE, "Test Pantry", Language.EN)

    assert scheduled is False
    async with db_pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM followup_queue WHERE phone_hash = $1", phone_hash
        )
    assert count == 1


@requires_db
async def test_maybe_schedule_allows_after_weekly_cap_window_expires(db_pool):
    phone_hash = _phone_hash("cap-expired")
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO followup_queue
                (phone_hash, phone_e164_encrypted, service_name, lang, scheduled_at, created_at)
            VALUES ($1, $2, 'Old Pantry', 'en', now() - interval '6 days 21 hours', now() - interval '8 days')
            """,
            phone_hash,
            encrypt_phone(PHONE),
        )

    scheduled = await maybe_schedule(db_pool, phone_hash, PHONE, "Test Pantry", Language.EN)

    assert scheduled is True


@requires_db
async def test_maybe_schedule_success_path_inserts_row_three_hours_out(db_pool):
    phone_hash = _phone_hash("success")

    scheduled = await maybe_schedule(db_pool, phone_hash, PHONE, "Test Pantry", Language.VI)

    assert scheduled is True
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT service_name, lang, scheduled_at, sent_at, phone_e164_encrypted "
            "FROM followup_queue WHERE phone_hash = $1",
            phone_hash,
        )
    assert row is not None
    assert row["service_name"] == "Test Pantry"
    assert row["lang"] == "vi"
    assert row["sent_at"] is None
    assert bytes(row["phone_e164_encrypted"]) != PHONE.encode()  # stored encrypted, not raw

    expected = datetime.now(UTC) + timedelta(hours=3)
    assert abs((row["scheduled_at"] - expected).total_seconds()) < 30


# ── send_due, requires_db (real due-row selection + delivery) ───────────────


@requires_db
async def test_send_due_only_selects_rows_that_are_actually_due(db_pool):
    due_hash = _phone_hash("due")
    future_hash = _phone_hash("future")
    already_sent_hash = _phone_hash("already-sent")

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO followup_queue (phone_hash, phone_e164_encrypted, service_name, lang, scheduled_at)
            VALUES ($1, $2, 'Due Pantry', 'en', now() - interval '1 minute')
            """,
            due_hash,
            encrypt_phone(PHONE),
        )
        await conn.execute(
            """
            INSERT INTO followup_queue (phone_hash, phone_e164_encrypted, service_name, lang, scheduled_at)
            VALUES ($1, $2, 'Future Pantry', 'en', now() + interval '2 hours')
            """,
            future_hash,
            encrypt_phone(PHONE),
        )
        await conn.execute(
            """
            INSERT INTO followup_queue
                (phone_hash, phone_e164_encrypted, service_name, lang, scheduled_at, sent_at, delivered)
            VALUES
                ($1, $2, 'Already Sent Pantry', 'en',
                 now() - interval '1 day', now() - interval '1 day', true)
            """,
            already_sent_hash,
            encrypt_phone(PHONE),
        )

    client = FakeTwilioClient()
    result = await send_due(db_pool, twilio_client=client)

    assert result == {"sent": 1, "expired": 0, "due": 1}
    assert client.messages.sent[0]["to"] == PHONE

    async with db_pool.acquire() as conn:
        due_row = await conn.fetchrow(
            "SELECT sent_at, delivered, phone_e164_encrypted FROM followup_queue WHERE phone_hash = $1",
            due_hash,
        )
    assert due_row["sent_at"] is not None
    assert due_row["delivered"] is True
    assert due_row["phone_e164_encrypted"] is None  # purged once delivered


@requires_db
async def test_send_due_expires_rows_older_than_max_send_age(db_pool):
    stale_hash = _phone_hash("stale")
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO followup_queue (phone_hash, phone_e164_encrypted, service_name, lang, scheduled_at)
            VALUES ($1, $2, 'Stale Pantry', 'en', now() - interval '3 days')
            """,
            stale_hash,
            encrypt_phone(PHONE),
        )

    client = FakeTwilioClient()
    result = await send_due(db_pool, twilio_client=client)

    assert result == {"sent": 0, "expired": 1, "due": 1}
    assert client.messages.sent == []

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT sent_at, delivered, phone_e164_encrypted FROM followup_queue WHERE phone_hash = $1",
            stale_hash,
        )
    assert row["sent_at"] is not None
    assert row["delivered"] is False
    assert row["phone_e164_encrypted"] is None


# ── record_response, requires_db ─────────────────────────────────────────────


@requires_db
async def test_record_response_updates_pending_row(db_pool):
    phone_hash = _phone_hash("responds")
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO followup_queue
                (phone_hash, phone_e164_encrypted, service_name, lang, scheduled_at, sent_at, delivered)
            VALUES
                ($1, $2, 'Test Pantry', 'en', now() - interval '3 hours', now() - interval '5 minutes', true)
            """,
            phone_hash,
            encrypt_phone(PHONE),
        )

    updated = await record_response(db_pool, phone_hash, "yes")

    assert updated is True
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT response, responded_at FROM followup_queue WHERE phone_hash = $1", phone_hash
        )
    assert row["response"] == "yes"
    assert row["responded_at"] is not None


@requires_db
async def test_record_response_returns_false_when_nothing_pending(db_pool):
    phone_hash = _phone_hash("nothing-pending")

    updated = await record_response(db_pool, phone_hash, "no")

    assert updated is False


@requires_db
async def test_record_response_ignores_row_not_yet_sent(db_pool):
    phone_hash = _phone_hash("not-yet-sent")
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO followup_queue (phone_hash, phone_e164_encrypted, service_name, lang, scheduled_at)
            VALUES ($1, $2, 'Test Pantry', 'en', now() + interval '1 hour')
            """,
            phone_hash,
            encrypt_phone(PHONE),
        )

    updated = await record_response(db_pool, phone_hash, "yes")

    assert updated is False


@requires_db
async def test_record_response_ignores_expired_undelivered_row(db_pool):
    phone_hash = _phone_hash("expired-undelivered")
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO followup_queue
                (phone_hash, phone_e164_encrypted, service_name, lang, scheduled_at, sent_at, delivered)
            VALUES ($1, $2, 'Test Pantry', 'en', now() - interval '3 days', now(), false)
            """,
            phone_hash,
            encrypt_phone(PHONE),
        )

    updated = await record_response(db_pool, phone_hash, "yes")

    assert updated is False


@requires_db
async def test_record_response_ignores_already_answered_row(db_pool):
    phone_hash = _phone_hash("already-answered")
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO followup_queue
                (phone_hash, phone_e164_encrypted, service_name, lang, scheduled_at, sent_at, delivered,
                 response, responded_at)
            VALUES
                ($1, $2, 'Test Pantry', 'en', now() - interval '3 hours', now() - interval '1 hour', true,
                 'yes', now() - interval '30 minutes')
            """,
            phone_hash,
            encrypt_phone(PHONE),
        )

    updated = await record_response(db_pool, phone_hash, "no")

    assert updated is False


@requires_db
async def test_record_response_picks_most_recently_sent_row(db_pool):
    phone_hash = _phone_hash("multiple-sent")
    async with db_pool.acquire() as conn:
        older_id = await conn.fetchval(
            """
            INSERT INTO followup_queue
                (phone_hash, phone_e164_encrypted, service_name, lang, scheduled_at, sent_at, delivered)
            VALUES ($1, $2, 'Older Pantry', 'en', now() - interval '2 days', now() - interval '2 days', true)
            RETURNING id
            """,
            phone_hash,
            encrypt_phone(PHONE),
        )
        newer_id = await conn.fetchval(
            """
            INSERT INTO followup_queue
                (phone_hash, phone_e164_encrypted, service_name, lang, scheduled_at, sent_at, delivered)
            VALUES ($1, $2, 'Newer Pantry', 'en', now() - interval '1 hour', now() - interval '1 hour', true)
            RETURNING id
            """,
            phone_hash,
            encrypt_phone(PHONE),
        )

    updated = await record_response(db_pool, phone_hash, "yes")
    assert updated is True

    async with db_pool.acquire() as conn:
        older_row = await conn.fetchrow(
            "SELECT response FROM followup_queue WHERE id = $1", older_id
        )
        newer_row = await conn.fetchrow(
            "SELECT response FROM followup_queue WHERE id = $1", newer_id
        )
    assert older_row["response"] is None
    assert newer_row["response"] == "yes"
