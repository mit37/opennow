from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import time as dt_time
from pathlib import Path
from typing import Any

import asyncpg

from app.config import Settings, get_settings
from app.db import close_pool, get_pool

SEED_DATA_PATH = Path(__file__).parent / "seed_data" / "manual_listings.json"


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def listing_key(location_name: str, address: str, category: str) -> tuple[str, str, str]:
    """Match key for a candidate/existing listing.

    Matched by name+address per the ingest spec; category is folded in too
    since one address can host more than one service (e.g. a pantry and a
    shower program at the same building), which would otherwise collide.
    """
    return (_normalize(location_name), _normalize(address), _normalize(category))


def _format_time(value: Any) -> str:
    if isinstance(value, dt_time):
        return f"{value.hour:02d}:{value.minute:02d}"
    return str(value)[:5]


def hours_signature(hours: list[dict]) -> tuple[tuple[Any, str, str], ...]:
    return tuple(
        sorted(
            (h["weekday"], _format_time(h["opens"]), _format_time(h["closes"])) for h in hours
        )
    )


def build_existing_index(rows: list[dict]) -> dict[tuple[str, str, str], dict]:
    """rows: one row per (location, service, schedule-entry) join, already
    plain dicts — either asyncpg Records converted with dict(), or fakes."""
    index: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        key = listing_key(row["location_name"] or "", row["address"], row["category"])
        entry = index.setdefault(key, {"category": row["category"], "hours": []})
        if row.get("weekday") is not None:
            entry["hours"].append(
                {"weekday": row["weekday"], "opens": row["opens"], "closes": row["closes"]}
            )
    return index


def classify_candidate(candidate: dict, existing_index: dict[tuple[str, str, str], dict]) -> str:
    """Returns 'new', 'changed', or 'unchanged'."""
    key = listing_key(candidate["location_name"], candidate["address"], candidate["category"])
    existing = existing_index.get(key)
    if existing is None:
        return "new"
    if hours_signature(existing["hours"]) != hours_signature(candidate["hours"]):
        return "changed"
    return "unchanged"


def classify_candidates(candidates: list[dict], existing_index: dict) -> dict:
    new_items: list[dict] = []
    changed_items: list[dict] = []
    unchanged_count = 0
    for candidate in candidates:
        outcome = classify_candidate(candidate, existing_index)
        if outcome == "new":
            new_items.append(candidate)
        elif outcome == "changed":
            changed_items.append(candidate)
        else:
            unchanged_count += 1
    return {"new": new_items, "changed": changed_items, "unchanged_count": unchanged_count}


async def load_manual_seed_adapter(settings: Settings) -> list[dict]:
    with SEED_DATA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


async def second_harvest_locator_adapter(settings: Settings) -> list[dict]:
    """Second Harvest's public food locator has no documented/stable page
    structure to scrape and no data-sharing agreement exists yet. Listings
    are entered manually via load_manual_seed_adapter until that agreement
    is in place; this stub marks the integration point for when it is."""
    return []


async def city_guide_adapter(settings: Settings) -> list[dict]:
    """City-run resource guides (San Jose, Sunnyvale, ...) are unstructured
    HTML with no feed. Same manual-entry-until-agreement path as Second
    Harvest; this stub marks the integration point."""
    return []


SourceAdapter = Callable[[Settings], Awaitable[list[dict]]]

SOURCES: list[SourceAdapter] = [
    load_manual_seed_adapter,
    second_harvest_locator_adapter,
    city_guide_adapter,
]


async def _fetch_existing_rows(pool: asyncpg.Pool) -> list[dict]:
    query = """
        SELECT l.name AS location_name, l.address AS address, s.category AS category,
               sc.weekday AS weekday, sc.opens AS opens, sc.closes AS closes
        FROM location l
        JOIN service s ON s.location_id = l.id
        LEFT JOIN schedule sc ON sc.service_id = s.id
        WHERE s.status = 'active'
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query)
    return [dict(row) for row in rows]


async def _write_review_rows(pool: asyncpg.Pool, new_items: list[dict], changed_items: list[dict]) -> None:
    records = [("new", json.dumps(item)) for item in new_items]
    records += [("changed", json.dumps(item)) for item in changed_items]
    if not records:
        return
    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO ingest_review (kind, payload) VALUES ($1, $2::jsonb)",
            records,
        )


async def diff_against_db(pool: asyncpg.Pool, candidates: list[dict]) -> dict:
    existing_rows = await _fetch_existing_rows(pool)
    existing_index = build_existing_index(existing_rows)
    result = classify_candidates(candidates, existing_index)
    await _write_review_rows(pool, result["new"], result["changed"])
    return result


async def run(pool: asyncpg.Pool) -> dict:
    settings = get_settings()
    candidates: list[dict] = []
    for source in SOURCES:
        candidates.extend(await source(settings))

    result = await diff_against_db(pool, candidates)
    print(
        f"ingest: {len(result['new'])} new, {len(result['changed'])} changed, "
        f"{result['unchanged_count']} unchanged"
    )
    return result


if __name__ == "__main__":

    async def _main() -> None:
        pool = await get_pool()
        try:
            await run(pool)
        finally:
            await close_pool()

    asyncio.run(_main())
