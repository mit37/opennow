from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

import asyncpg

from app.config import get_settings
from app.db import close_pool, get_pool
from app.models import Language
from app.security import decrypt_phone


class _TwilioMessages(Protocol):
    def create(self, *, to: str, from_: str, body: str) -> object: ...


class TwilioClientLike(Protocol):
    messages: _TwilioMessages


_QUIET_TZ = ZoneInfo("America/Los_Angeles")
_RECENT_LISTING_DAYS = 7
_MAX_ALERTS_PER_WEEK = 3
_ALERT_RADIUS_MILES = 5.0
_METERS_PER_MILE = 1609.34

_ALERT_INTRO = {
    Language.EN: "OpenNow alert: new service near {zip}:",
    Language.ES: "Alerta de OpenNow: nuevo servicio cerca de {zip}:",
    Language.VI: "Cảnh báo OpenNow: dịch vụ mới gần {zip}:",
}


def is_quiet_hours(moment: datetime) -> bool:
    """Quiet hours are 9pm-8am America/Los_Angeles, evaluated in that zone
    regardless of what timezone `moment` is passed in as."""
    local = moment.astimezone(_QUIET_TZ)
    return local.hour >= 21 or local.hour < 8


def group_by_zip(subscriptions: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for sub in subscriptions:
        groups.setdefault(sub["zip"], []).append(sub)
    return groups


def can_send_alert(recent_send_count: int, cap: int = _MAX_ALERTS_PER_WEEK) -> bool:
    return recent_send_count < cap


def format_alert_body(lang: Language, listings: list[dict], zip_code: str) -> str:
    intro = _ALERT_INTRO.get(lang, _ALERT_INTRO[Language.EN]).format(zip=zip_code)
    lines = [intro]
    for listing in listings:
        lines.append(f"- {listing['name']}, {listing['address']}")
    return "\n".join(lines)


async def _load_active_subscriptions(pool: asyncpg.Pool) -> list[dict]:
    query = """
        SELECT phone_hash, phone_e164_encrypted, zip, lang
        FROM alert_subscription
        WHERE opted_out_at IS NULL
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query)
    return [dict(row) for row in rows]


async def _find_recent_listings_for_zip(pool: asyncpg.Pool, zip_code: str) -> list[dict]:
    query = """
        SELECT s.name AS name, l.address AS address,
               ST_Distance(l.geom, z.geom) AS distance_meters
        FROM service s
        JOIN location l ON l.id = s.location_id
        JOIN zip_centroid z ON z.zip = $1
        WHERE s.status = 'active'
          AND s.last_verified_at >= now() - make_interval(days => $2)
          AND ST_DWithin(l.geom, z.geom, $3)
        ORDER BY distance_meters ASC
        LIMIT 3
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            query, zip_code, _RECENT_LISTING_DAYS, _ALERT_RADIUS_MILES * _METERS_PER_MILE
        )
    return [dict(row) for row in rows]


async def _recent_send_count(pool: asyncpg.Pool, phone_hash: bytes) -> int:
    query = """
        SELECT count(*) FROM alert_send_log
        WHERE phone_hash = $1 AND sent_at >= now() - interval '7 days'
    """
    async with pool.acquire() as conn:
        return await conn.fetchval(query, phone_hash)


async def _record_send(pool: asyncpg.Pool, phone_hash: bytes) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO alert_send_log (phone_hash, sent_at) VALUES ($1, now())",
            phone_hash,
        )


async def run(pool: asyncpg.Pool, twilio_client: TwilioClientLike | None = None) -> dict:
    now = datetime.now(tz=UTC)
    if is_quiet_hours(now):
        return {
            "sent": 0,
            "skipped_quiet_hours": True,
            "skipped_cap": 0,
            "skipped_no_listings": 0,
            "subscribers_considered": 0,
        }

    settings = get_settings()
    subscriptions = await _load_active_subscriptions(pool)
    by_zip = group_by_zip(subscriptions)

    sent = 0
    skipped_cap = 0
    skipped_no_listings = 0

    for zip_code, subs in by_zip.items():
        listings = await _find_recent_listings_for_zip(pool, zip_code)
        if not listings:
            skipped_no_listings += len(subs)
            continue

        body_by_lang: dict[Language, str] = {}
        for sub in subs:
            phone_hash: bytes = sub["phone_hash"]
            recent_count = await _recent_send_count(pool, phone_hash)
            if not can_send_alert(recent_count):
                skipped_cap += 1
                continue

            lang = Language(sub["lang"])
            body = body_by_lang.get(lang)
            if body is None:
                body = format_alert_body(lang, listings, zip_code)
                body_by_lang[lang] = body

            phone_e164 = decrypt_phone(sub["phone_e164_encrypted"])
            if twilio_client is not None:
                twilio_client.messages.create(
                    to=phone_e164,
                    from_=settings.twilio_from_number,
                    body=body,
                )
            await _record_send(pool, phone_hash)
            sent += 1

    print(
        f"alert_scheduler: sent {sent}, skipped_cap {skipped_cap}, "
        f"skipped_no_listings {skipped_no_listings}, subscribers {len(subscriptions)}"
    )
    return {
        "sent": sent,
        "skipped_quiet_hours": False,
        "skipped_cap": skipped_cap,
        "skipped_no_listings": skipped_no_listings,
        "subscribers_considered": len(subscriptions),
    }


if __name__ == "__main__":
    from twilio.rest import Client as TwilioClient

    async def _main() -> None:
        settings = get_settings()
        pool = await get_pool()
        client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)
        try:
            await run(pool, twilio_client=client)
        finally:
            await close_pool()

    asyncio.run(_main())
