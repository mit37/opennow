from __future__ import annotations

from datetime import date, datetime, time

import asyncpg

from app.hours import closes_at, is_open_now, next_opening
from app.models import Category, ScheduleWindow, ServiceResult

METERS_PER_MILE = 1609.34
INITIAL_RADIUS_MILES = 3.0
WIDENED_RADIUS_MILES = 10.0

_CANDIDATES_QUERY = """
    SELECT
        s.id AS service_id,
        s.name AS name,
        s.category AS category,
        l.address AS address,
        s.eligibility_note AS eligibility_note,
        ST_Distance(l.geom, ST_MakePoint($1, $2)::geography) AS distance_meters
    FROM service s
    JOIN location l ON l.id = s.location_id
    WHERE s.status = 'active'
      AND s.category = ANY($3::text[])
      AND ST_DWithin(l.geom, ST_MakePoint($1, $2)::geography, $4)
    ORDER BY ST_Distance(l.geom, ST_MakePoint($1, $2)::geography) ASC
    LIMIT $5
"""

_SCHEDULE_QUERY = """
    SELECT weekday, opens, closes
    FROM schedule
    WHERE service_id = $1
    ORDER BY weekday, opens
"""

_EXCEPTION_QUERY = """
    SELECT date, closed, opens, closes, reason
    FROM schedule_exception
    WHERE service_id = $1
      AND date >= CURRENT_DATE
      AND date <= CURRENT_DATE + INTERVAL '14 days'
    ORDER BY date
"""


async def find_candidates(
    pool: asyncpg.Pool,
    lat: float,
    lon: float,
    categories: tuple[Category, ...],
    radius_miles: float,
    limit_candidates: int = 20,
) -> list[dict]:
    radius_meters = radius_miles * METERS_PER_MILE
    category_values = [c.value for c in categories]

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _CANDIDATES_QUERY, lon, lat, category_values, radius_meters, limit_candidates
        )

        candidates: list[dict] = []
        for row in rows:
            schedule_rows = await conn.fetch(_SCHEDULE_QUERY, row["service_id"])
            exception_rows = await conn.fetch(_EXCEPTION_QUERY, row["service_id"])

            schedules = [
                ScheduleWindow(weekday=r["weekday"], opens=r["opens"], closes=r["closes"])
                for r in schedule_rows
            ]
            exceptions = [
                {
                    "date": r["date"],
                    "closed": r["closed"],
                    "opens": r["opens"],
                    "closes": r["closes"],
                    "reason": r["reason"],
                }
                for r in exception_rows
            ]

            candidates.append(
                {
                    "service_id": str(row["service_id"]),
                    "name": row["name"],
                    "category": Category(row["category"]),
                    "address": row["address"],
                    "distance_miles": float(row["distance_meters"]) / METERS_PER_MILE,
                    "eligibility_note": row["eligibility_note"],
                    "schedules": schedules,
                    "exceptions": exceptions,
                }
            )

        return candidates


async def _find_candidates_with_widening(
    pool: asyncpg.Pool,
    lat: float,
    lon: float,
    categories: tuple[Category, ...],
) -> list[dict]:
    candidates = await find_candidates(pool, lat, lon, categories, INITIAL_RADIUS_MILES)
    if not candidates:
        candidates = await find_candidates(pool, lat, lon, categories, WIDENED_RADIUS_MILES)
    return candidates


def _dedupe_upcoming_openings(
    candidates: list[dict], at: datetime, max_results: int
) -> list[tuple[str, date, time]]:
    upcoming: list[tuple[str, date, time]] = []
    seen_service_ids: set[str] = set()

    for candidate in candidates:
        if candidate["service_id"] in seen_service_ids:
            continue
        opening = next_opening(candidate["schedules"], candidate["exceptions"], at)
        if opening is None:
            continue
        seen_service_ids.add(candidate["service_id"])
        opening_date, opening_time = opening
        upcoming.append((candidate["name"], opening_date, opening_time))

    upcoming.sort(key=lambda item: (item[1], item[2]))
    return upcoming[:max_results]


async def get_open_now(
    pool: asyncpg.Pool,
    lat: float,
    lon: float,
    categories: tuple[Category, ...],
    at: datetime,
    limit: int = 3,
) -> tuple[list[ServiceResult], list[tuple[str, date, time]]]:
    candidates = await _find_candidates_with_widening(pool, lat, lon, categories)

    open_results: list[ServiceResult] = []
    for candidate in candidates:
        if len(open_results) >= limit:
            break
        if not is_open_now(candidate["schedules"], candidate["exceptions"], at):
            continue
        open_results.append(
            ServiceResult(
                service_id=candidate["service_id"],
                name=candidate["name"],
                category=candidate["category"],
                address=candidate["address"],
                distance_miles=candidate["distance_miles"],
                closes_at=closes_at(candidate["schedules"], candidate["exceptions"], at),
                next_opens_at=None,
                eligibility_note=candidate["eligibility_note"],
            )
        )

    if open_results:
        return open_results, []

    return [], _dedupe_upcoming_openings(candidates, at, max_results=2)
