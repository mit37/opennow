from __future__ import annotations

from typing import Protocol

import asyncpg

from app.config import get_settings
from app.models import Language
from app.security import decrypt_phone, encrypt_phone

# Rows still unsent after this long are given up on rather than retried
# forever — e.g. a scheduler outage. Keeps followup_queue from accumulating
# permanently-due rows with no terminal state.
MAX_SEND_AGE = "2 days"


class _TwilioMessages(Protocol):
    def create(self, *, to: str, from_: str, body: str) -> object: ...


class TwilioClientLike(Protocol):
    messages: _TwilioMessages


# EN/ES/VI, 6th-grade reading level, no jargon — same rules as app/templates.py.
# No diacritics, matching app/templates.py's ES/VI strings, so this stays one
# GSM-7 SMS segment instead of switching to UCS-2.
FOLLOWUP_BODY: dict[Language, str] = {
    Language.EN: "Did you make it? Reply Y or N.",
    Language.ES: "Pudiste llegar? Responde Y o N.",
    Language.VI: "Ban co den duoc khong? Tra loi Y hoac N.",
}

_ACTIVE_ALERT_SUBSCRIBER_SQL = """
    SELECT 1 FROM alert_subscription WHERE phone_hash = $1 AND opted_out_at IS NULL
"""

_RECENT_FOLLOWUP_SQL = """
    SELECT 1 FROM followup_queue
    WHERE phone_hash = $1 AND created_at >= now() - interval '7 days'
    LIMIT 1
"""

_INSERT_FOLLOWUP_SQL = """
    INSERT INTO followup_queue (phone_hash, phone_e164_encrypted, service_name, lang, scheduled_at)
    VALUES ($1, $2, $3, $4, now() + interval '3 hours')
"""

_DUE_QUERY = f"""
    SELECT id, phone_hash, phone_e164_encrypted, service_name, lang,
           scheduled_at < now() - interval '{MAX_SEND_AGE}' AS expired
    FROM followup_queue
    WHERE sent_at IS NULL AND scheduled_at <= now()
"""

_MARK_DELIVERED_SQL = """
    UPDATE followup_queue SET sent_at = now(), delivered = true, phone_e164_encrypted = NULL
    WHERE id = $1
"""

_MARK_EXPIRED_SQL = """
    UPDATE followup_queue SET sent_at = now(), delivered = false, phone_e164_encrypted = NULL
    WHERE id = $1
"""

_RECORD_RESPONSE_SQL = """
    UPDATE followup_queue
    SET response = $2, responded_at = now()
    WHERE id = (
        SELECT id FROM followup_queue
        WHERE phone_hash = $1 AND delivered AND responded_at IS NULL
        ORDER BY sent_at DESC
        LIMIT 1
    )
    RETURNING id
"""


def is_eligible_for_followup(has_active_alert_subscription: bool, has_recent_followup: bool) -> bool:
    """Pure decision behind the "off by default for alerts users" and "max 1
    number per week" rules in FR-11, split out from maybe_schedule so it can
    be unit-tested without a database (mirrors can_send_alert in
    jobs/alert_scheduler.py)."""
    return not has_active_alert_subscription and not has_recent_followup


async def maybe_schedule(
    pool: asyncpg.Pool, phone_hash: bytes, phone_e164: str, service_name: str, lang: Language
) -> bool:
    # Check-then-act, same as jobs/alert_scheduler.py's weekly-cap check — a
    # concurrent request for the same phone number in the same instant could
    # in theory schedule twice. Accepted here for the same reason it's
    # accepted there: per-phone SMS traffic is far too low for that race to
    # matter in practice.
    async with pool.acquire() as conn:
        has_active_alert_subscription = (
            await conn.fetchval(_ACTIVE_ALERT_SUBSCRIBER_SQL, phone_hash) is not None
        )
        has_recent_followup = await conn.fetchval(_RECENT_FOLLOWUP_SQL, phone_hash) is not None

        if not is_eligible_for_followup(has_active_alert_subscription, has_recent_followup):
            return False

        await conn.execute(
            _INSERT_FOLLOWUP_SQL, phone_hash, encrypt_phone(phone_e164), service_name, lang.value
        )
    return True


async def send_due(pool: asyncpg.Pool, twilio_client: TwilioClientLike) -> dict:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_DUE_QUERY)

    sent = 0
    expired = 0

    for row in rows:
        if row["expired"]:
            # Given up on: e.g. the scheduler was down for MAX_SEND_AGE. The
            # encrypted number is purged either way, so there's no lingering
            # PII from a row that outlives its delivery window.
            async with pool.acquire() as conn:
                await conn.execute(_MARK_EXPIRED_SQL, row["id"])
            expired += 1
            continue

        lang = Language(row["lang"])
        body = FOLLOWUP_BODY[lang]
        phone_e164 = decrypt_phone(bytes(row["phone_e164_encrypted"]))

        twilio_client.messages.create(
            to=phone_e164, from_=get_settings().twilio_from_number, body=body
        )
        async with pool.acquire() as conn:
            await conn.execute(_MARK_DELIVERED_SQL, row["id"])
        sent += 1

    print(f"followup_scheduler: sent {sent}, expired {expired}, due {len(rows)}")
    return {"sent": sent, "expired": expired, "due": len(rows)}


async def record_response(pool: asyncpg.Pool, phone_hash: bytes, response: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_RECORD_RESPONSE_SQL, phone_hash, response)
    return row is not None
