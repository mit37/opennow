from __future__ import annotations

import time as time_module
from datetime import datetime
from zoneinfo import ZoneInfo

import asyncpg

from app import geocoder, parser, provider, query_engine, session_store, templates
from app.config import get_settings
from app.models import Category, Intent, Language, ParsedMessage, SessionState
from app.security import encrypt_phone, hash_phone

LOCAL_TZ = ZoneInfo("America/Los_Angeles")

_ALERT_UPSERT_SQL = """
    INSERT INTO alert_subscription (phone_e164_encrypted, phone_hash, zip, lang, opted_in_at, opted_out_at)
    VALUES ($1, $2, $3, $4, now(), NULL)
    ON CONFLICT (phone_hash) DO UPDATE
    SET phone_e164_encrypted = EXCLUDED.phone_e164_encrypted,
        zip = EXCLUDED.zip,
        lang = EXCLUDED.lang,
        opted_in_at = now(),
        opted_out_at = NULL
"""

_ALERT_OPT_OUT_SQL = """
    UPDATE alert_subscription SET opted_out_at = now() WHERE phone_hash = $1
"""


def _now_local() -> datetime:
    return datetime.now(LOCAL_TZ)


def _in_here4you_hours(at: datetime) -> bool:
    settings = get_settings()
    return settings.here4you_hours_start <= at.hour < settings.here4you_hours_end


def _effective_lang(parsed: ParsedMessage, session: SessionState | None) -> Language:
    if parsed.language_override is not None:
        return parsed.language_override
    if session is not None:
        return session.lang
    return Language.EN


def _categories_to_values(categories: tuple[Category, ...]) -> list[str]:
    return [c.value for c in categories]


def _categories_from_values(values: list[str]) -> tuple[Category, ...]:
    return tuple(Category(v) for v in values)


async def _confirm_alert_subscription(
    pool: asyncpg.Pool, phone_hash: bytes, phone_e164: str, zip_code: str, lang: Language
) -> None:
    encrypted = encrypt_phone(phone_e164)
    await pool.execute(_ALERT_UPSERT_SQL, encrypted, phone_hash, zip_code, lang.value)


async def _opt_out_alerts(pool: asyncpg.Pool, phone_hash: bytes) -> None:
    await pool.execute(_ALERT_OPT_OUT_SQL, phone_hash)


async def _handle_location_query(
    pool: asyncpg.Pool,
    parsed: ParsedMessage,
    lang: Language,
    phone_hash: bytes,
) -> str:
    settings = get_settings()

    if parsed.zip_code is not None:
        geo = await geocoder.geocode_zip(pool, parsed.zip_code)
        if geo is None:
            return templates.render_unknown_zip(lang)
        query_label = parsed.zip_code
    else:
        geo = await geocoder.geocode_text(pool, parsed.location_text or "", settings)
        if geo is None:
            return templates.render_generic_error(lang)
        if geo.ambiguous:
            return templates.render_ambiguous_location(lang)
        query_label = (parsed.location_text or "").title()

    if not geo.in_county:
        return templates.render_out_of_county(lang)

    max_results = templates.MAX_RESULTS_BY_LANG[lang]
    now = _now_local()
    open_results, next_openings = await query_engine.get_open_now(
        pool, geo.lat, geo.lon, parsed.categories, now, limit=max_results
    )

    if open_results:
        reply = templates.render_results(open_results, lang, query_label)
        last_query = {
            "lat": geo.lat,
            "lon": geo.lon,
            "categories": _categories_to_values(parsed.categories),
            "query_label": query_label,
            "offset": len(open_results),
        }
    else:
        reply = templates.render_no_results(next_openings, lang, query_label)
        last_query = {}

    state = SessionState(phone_hash=phone_hash, lang=lang, last_query=last_query)
    await session_store.save_session(pool, state, get_settings().session_ttl_minutes)
    return reply


async def _handle_more(pool: asyncpg.Pool, lang: Language, phone_hash: bytes) -> str:
    session = await session_store.get_session(pool, phone_hash)
    last_query = session.last_query if session else {}
    if not last_query or "lat" not in last_query:
        return templates.render_no_more_results(lang)

    offset = int(last_query.get("offset", 0))
    categories = _categories_from_values(last_query["categories"])
    now = _now_local()
    page_size = templates.MAX_RESULTS_BY_LANG[lang]

    open_results, _ = await query_engine.get_open_now(
        pool, last_query["lat"], last_query["lon"], categories, now, limit=offset + page_size
    )
    page = open_results[offset : offset + page_size]
    if not page:
        return templates.render_no_more_results(lang)

    reply = templates.render_results(page, lang, last_query["query_label"])
    last_query["offset"] = offset + len(page)
    state = SessionState(phone_hash=phone_hash, lang=lang, last_query=last_query)
    await session_store.save_session(pool, state, get_settings().session_ttl_minutes)
    return reply


def _is_provider_closed_today_text(body: str) -> bool:
    return body.strip().upper() == "CLOSED TODAY"


async def handle_message(pool: asyncpg.Pool, from_number: str, body: str) -> str:
    phone_hash = hash_phone(from_number)

    if _is_provider_closed_today_text(body):
        service_id = await provider.find_registered_service(pool, phone_hash)
        if service_id is not None:
            await provider.flag_closed_today(pool, service_id, _now_local().date())
            return templates.render_provider_closed_ack(Language.EN)

    session = await session_store.get_session(pool, phone_hash)
    session_lang = session.lang if session else Language.EN

    parsed = parser.parse(body, session_lang=session_lang)
    lang = _effective_lang(parsed, session)

    started = time_module.monotonic()
    reply: str

    if parsed.intent is Intent.CRISIS:
        reply = templates.render_crisis_prefix(lang)

    elif parsed.intent is Intent.STOP:
        await _opt_out_alerts(pool, phone_hash)
        reply = templates.render_stop_ack(lang)

    elif parsed.intent is Intent.START:
        pending_zip = (session.last_query or {}).get("pending_alert_zip") if session else None
        if pending_zip:
            await _confirm_alert_subscription(pool, phone_hash, from_number, pending_zip, lang)
            reply = templates.render_alerts_confirmed(lang)
        else:
            reply = templates.render_start_ack(lang)

    elif parsed.intent is Intent.HELP:
        reply = templates.render_help(lang)

    elif parsed.intent is Intent.SHELTER:
        in_hours = _in_here4you_hours(_now_local())
        reply = templates.render_shelter(in_hours, lang)
        if not in_hours:
            last_query = session.last_query if session else {}
            if last_query.get("lat") is not None:
                dropin, _ = await query_engine.get_open_now(
                    pool, last_query["lat"], last_query["lon"], (Category.DROPIN,), _now_local(), limit=1
                )
                if dropin:
                    reply = f"{reply}\n{templates.render_nearest_dropin_line(dropin[0], lang)}"

    elif parsed.intent is Intent.ALERTS_OPT_IN:
        if parsed.alert_zip is None:
            reply = templates.render_unknown_zip(lang)
        else:
            reply = templates.render_alerts_confirm_prompt(parsed.alert_zip, lang)
            state = SessionState(
                phone_hash=phone_hash,
                lang=lang,
                last_query={"pending_alert_zip": parsed.alert_zip},
            )
            await session_store.save_session(pool, state, get_settings().session_ttl_minutes)

    elif parsed.intent is Intent.MORE:
        reply = await _handle_more(pool, lang, phone_hash)

    elif parsed.intent is Intent.LOCATION_QUERY:
        reply = await _handle_location_query(pool, parsed, lang, phone_hash)

    else:
        reply = templates.render_help(lang)

    latency_ms = int((time_module.monotonic() - started) * 1000)
    await _log_event(pool, phone_hash, parsed, latency_ms)
    return reply


_EVENT_LOG_SQL = """
    INSERT INTO event_log (phone_hash, command, zip, results_count, latency_ms)
    VALUES ($1, $2, $3, $4, $5)
"""


async def _log_event(
    pool: asyncpg.Pool, phone_hash: bytes, parsed: ParsedMessage, latency_ms: int
) -> None:
    zip_code = parsed.zip_code or parsed.alert_zip
    await pool.execute(
        _EVENT_LOG_SQL, phone_hash, parsed.intent.value, zip_code, None, latency_ms
    )
