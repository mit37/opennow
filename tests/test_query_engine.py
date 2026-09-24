from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.models import Category
from app.query_engine import (
    INITIAL_RADIUS_MILES,
    METERS_PER_MILE,
    WIDENED_RADIUS_MILES,
    _dedupe_upcoming_openings,
    _find_candidates_with_widening,
    find_candidates,
    get_open_now,
)
from tests.conftest import requires_db

BASE_LAT = 37.3382
BASE_LON = -121.8863


# ── Pure-Python tests: radius-widening retry logic (no DB) ──────────────────


class _FakePool:
    """Stand-in pool object; never touched because find_candidates is patched."""


async def test_widening_skips_second_call_when_first_finds_candidates(monkeypatch):
    calls: list[float] = []

    async def fake_find_candidates(pool, lat, lon, categories, radius_miles, limit_candidates=20):
        calls.append(radius_miles)
        return [{"service_id": "abc"}]

    monkeypatch.setattr("app.query_engine.find_candidates", fake_find_candidates)

    result = await _find_candidates_with_widening(
        _FakePool(), BASE_LAT, BASE_LON, (Category.FOOD,)
    )

    assert calls == [INITIAL_RADIUS_MILES]
    assert result == [{"service_id": "abc"}]


async def test_widening_retries_once_when_first_call_is_empty(monkeypatch):
    calls: list[float] = []

    async def fake_find_candidates(pool, lat, lon, categories, radius_miles, limit_candidates=20):
        calls.append(radius_miles)
        if radius_miles == INITIAL_RADIUS_MILES:
            return []
        return [{"service_id": "wide-hit"}]

    monkeypatch.setattr("app.query_engine.find_candidates", fake_find_candidates)

    result = await _find_candidates_with_widening(
        _FakePool(), BASE_LAT, BASE_LON, (Category.FOOD,)
    )

    assert calls == [INITIAL_RADIUS_MILES, WIDENED_RADIUS_MILES]
    assert result == [{"service_id": "wide-hit"}]


async def test_widening_does_not_retry_a_second_time_when_still_empty(monkeypatch):
    calls: list[float] = []

    async def fake_find_candidates(pool, lat, lon, categories, radius_miles, limit_candidates=20):
        calls.append(radius_miles)
        return []

    monkeypatch.setattr("app.query_engine.find_candidates", fake_find_candidates)

    result = await _find_candidates_with_widening(
        _FakePool(), BASE_LAT, BASE_LON, (Category.FOOD,)
    )

    assert calls == [INITIAL_RADIUS_MILES, WIDENED_RADIUS_MILES]
    assert result == []


def test_meters_per_mile_conversion_constant():
    assert METERS_PER_MILE == 1609.34


# ── Pure-Python tests: dedupe/sort of upcoming openings (no DB) ─────────────


def test_dedupe_upcoming_openings_sorts_and_dedupes(monkeypatch):
    at = datetime(2026, 9, 23, 10, 0)

    def fake_next_opening(schedules, exceptions, at_):
        return schedules["opening"]

    monkeypatch.setattr("app.query_engine.next_opening", fake_next_opening)

    candidates = [
        {
            "service_id": "svc-1",
            "name": "Late Service",
            "schedules": {"opening": (date(2026, 9, 24), time(9, 0))},
            "exceptions": [],
        },
        {
            "service_id": "svc-2",
            "name": "Early Service",
            "schedules": {"opening": (date(2026, 9, 23), time(14, 0))},
            "exceptions": [],
        },
        # Duplicate service id (e.g. re-queried) should not appear twice.
        {
            "service_id": "svc-2",
            "name": "Early Service",
            "schedules": {"opening": (date(2026, 9, 23), time(14, 0))},
            "exceptions": [],
        },
    ]

    result = _dedupe_upcoming_openings(candidates, at, max_results=2)

    assert result == [
        ("Early Service", date(2026, 9, 23), time(14, 0)),
        ("Late Service", date(2026, 9, 24), time(9, 0)),
    ]


def test_dedupe_upcoming_openings_skips_services_with_no_future_opening(monkeypatch):
    at = datetime(2026, 9, 23, 10, 0)

    def fake_next_opening(schedules, exceptions, at_):
        return None

    monkeypatch.setattr("app.query_engine.next_opening", fake_next_opening)

    candidates = [{"service_id": "svc-1", "name": "Never Opens", "schedules": {}, "exceptions": []}]

    result = _dedupe_upcoming_openings(candidates, at, max_results=2)

    assert result == []


def test_dedupe_upcoming_openings_truncates_to_max_results(monkeypatch):
    at = datetime(2026, 9, 23, 10, 0)

    openings = {
        "svc-1": (date(2026, 9, 23), time(11, 0)),
        "svc-2": (date(2026, 9, 23), time(12, 0)),
        "svc-3": (date(2026, 9, 23), time(13, 0)),
    }

    def fake_next_opening(schedules, exceptions, at_):
        return openings[schedules["id"]]

    monkeypatch.setattr("app.query_engine.next_opening", fake_next_opening)

    candidates = [
        {"service_id": sid, "name": sid, "schedules": {"id": sid}, "exceptions": []}
        for sid in ("svc-1", "svc-2", "svc-3")
    ]

    result = _dedupe_upcoming_openings(candidates, at, max_results=2)

    assert len(result) == 2
    assert result[0][0] == "svc-1"
    assert result[1][0] == "svc-2"


# ── DB-backed tests: find_candidates + get_open_now against real PostGIS ────


async def _seed_service(
    conn,
    *,
    name: str,
    category: Category,
    lat: float,
    lon: float,
    weekday: int | None,
    opens: time | None,
    closes: time | None,
    address: str = "123 Test St, San Jose, CA",
) -> str:
    org_id = await conn.fetchval(
        "INSERT INTO organization (name) VALUES ($1) RETURNING id", f"{name} Org"
    )
    location_id = await conn.fetchval(
        """
        INSERT INTO location (organization_id, name, address, geom)
        VALUES ($1, $2, $3, ST_MakePoint($4, $5)::geography)
        RETURNING id
        """,
        org_id,
        name,
        address,
        lon,
        lat,
    )
    service_id = await conn.fetchval(
        """
        INSERT INTO service (location_id, name, category, status)
        VALUES ($1, $2, $3, 'active')
        RETURNING id
        """,
        location_id,
        name,
        category.value,
    )
    if weekday is not None:
        await conn.execute(
            "INSERT INTO schedule (service_id, weekday, opens, closes) VALUES ($1, $2, $3, $4)",
            service_id,
            weekday,
            opens,
            closes,
        )
    return str(service_id)


@requires_db
async def test_find_candidates_orders_by_distance(db_pool):
    async with db_pool.acquire() as conn:
        far_id = await _seed_service(
            conn,
            name="Far Pantry",
            category=Category.FOOD,
            lat=BASE_LAT + 0.03,
            lon=BASE_LON,
            weekday=0,
            opens=time(9, 0),
            closes=time(17, 0),
        )
        near_id = await _seed_service(
            conn,
            name="Near Pantry",
            category=Category.FOOD,
            lat=BASE_LAT + 0.001,
            lon=BASE_LON,
            weekday=0,
            opens=time(9, 0),
            closes=time(17, 0),
        )
        mid_id = await _seed_service(
            conn,
            name="Mid Pantry",
            category=Category.FOOD,
            lat=BASE_LAT + 0.01,
            lon=BASE_LON,
            weekday=0,
            opens=time(9, 0),
            closes=time(17, 0),
        )

    results = await find_candidates(
        db_pool, BASE_LAT, BASE_LON, (Category.FOOD,), radius_miles=10.0
    )

    assert [r["service_id"] for r in results] == [near_id, mid_id, far_id]
    distances = [r["distance_miles"] for r in results]
    assert distances == sorted(distances)
    for r in results:
        assert r["category"] == Category.FOOD
        assert isinstance(r["distance_miles"], float)
        assert r["schedules"]


@requires_db
async def test_find_candidates_filters_by_category_and_status(db_pool):
    async with db_pool.acquire() as conn:
        food_id = await _seed_service(
            conn,
            name="Food Spot",
            category=Category.FOOD,
            lat=BASE_LAT,
            lon=BASE_LON,
            weekday=0,
            opens=time(9, 0),
            closes=time(17, 0),
        )
        await _seed_service(
            conn,
            name="Shower Spot",
            category=Category.SHOWER,
            lat=BASE_LAT,
            lon=BASE_LON,
            weekday=0,
            opens=time(9, 0),
            closes=time(17, 0),
        )
        hidden_service_id = await _seed_service(
            conn,
            name="Hidden Food Spot",
            category=Category.FOOD,
            lat=BASE_LAT,
            lon=BASE_LON,
            weekday=0,
            opens=time(9, 0),
            closes=time(17, 0),
        )
        await conn.execute(
            "UPDATE service SET status = 'hidden' WHERE id = $1", hidden_service_id
        )

    results = await find_candidates(
        db_pool, BASE_LAT, BASE_LON, (Category.FOOD,), radius_miles=10.0
    )

    ids = [r["service_id"] for r in results]
    assert food_id in ids
    assert hidden_service_id not in ids
    assert all(r["category"] == Category.FOOD for r in results)


@requires_db
async def test_find_candidates_respects_radius(db_pool):
    async with db_pool.acquire() as conn:
        far_id = await _seed_service(
            conn,
            name="Far Away Pantry",
            category=Category.FOOD,
            lat=BASE_LAT + 1.0,  # ~69 miles away
            lon=BASE_LON,
            weekday=0,
            opens=time(9, 0),
            closes=time(17, 0),
        )

    results = await find_candidates(
        db_pool, BASE_LAT, BASE_LON, (Category.FOOD,), radius_miles=3.0
    )

    assert far_id not in [r["service_id"] for r in results]


@requires_db
async def test_find_candidates_includes_exceptions_within_14_days(db_pool):
    async with db_pool.acquire() as conn:
        service_id = await _seed_service(
            conn,
            name="Exception Pantry",
            category=Category.FOOD,
            lat=BASE_LAT,
            lon=BASE_LON,
            weekday=0,
            opens=time(9, 0),
            closes=time(17, 0),
        )
        near_date = date.today() + timedelta(days=2)
        far_date = date.today() + timedelta(days=30)
        await conn.execute(
            """
            INSERT INTO schedule_exception (service_id, date, closed, reason)
            VALUES ($1, $2, true, 'holiday')
            """,
            service_id,
            near_date,
        )
        await conn.execute(
            """
            INSERT INTO schedule_exception (service_id, date, closed, reason)
            VALUES ($1, $2, true, 'too far to matter')
            """,
            service_id,
            far_date,
        )

    results = await find_candidates(
        db_pool, BASE_LAT, BASE_LON, (Category.FOOD,), radius_miles=10.0
    )

    match = next(r for r in results if r["service_id"] == service_id)
    exception_dates = [e["date"] for e in match["exceptions"]]
    assert near_date in exception_dates
    assert far_date not in exception_dates


@requires_db
async def test_get_open_now_includes_open_and_excludes_closed(db_pool):
    at = datetime(2026, 9, 23, 12, 0)  # Wednesday = weekday 2
    async with db_pool.acquire() as conn:
        open_id = await _seed_service(
            conn,
            name="Open Now Pantry",
            category=Category.FOOD,
            lat=BASE_LAT,
            lon=BASE_LON,
            weekday=2,
            opens=time(9, 0),
            closes=time(17, 0),
        )
        closed_id = await _seed_service(
            conn,
            name="Closed Now Pantry",
            category=Category.FOOD,
            lat=BASE_LAT + 0.002,
            lon=BASE_LON,
            weekday=2,
            opens=time(18, 0),
            closes=time(20, 0),
        )

    open_results, upcoming = await get_open_now(
        db_pool, BASE_LAT, BASE_LON, (Category.FOOD,), at, limit=3
    )

    open_ids = [r.service_id for r in open_results]
    assert open_id in open_ids
    assert closed_id not in open_ids
    assert upcoming == []
    matched = next(r for r in open_results if r.service_id == open_id)
    assert matched.closes_at == time(17, 0)
    assert matched.next_opens_at is None


@requires_db
async def test_get_open_now_widens_radius_when_nothing_within_initial_radius(db_pool):
    at = datetime(2026, 9, 23, 12, 0)  # Wednesday = weekday 2
    async with db_pool.acquire() as conn:
        far_id = await _seed_service(
            conn,
            name="Far But Open Pantry",
            category=Category.FOOD,
            lat=BASE_LAT + 0.08,  # ~5.5 miles: outside 3mi, inside 10mi
            lon=BASE_LON,
            weekday=2,
            opens=time(9, 0),
            closes=time(17, 0),
        )

    open_results, upcoming = await get_open_now(
        db_pool, BASE_LAT, BASE_LON, (Category.FOOD,), at, limit=3
    )

    assert [r.service_id for r in open_results] == [far_id]
    assert upcoming == []


@requires_db
async def test_get_open_now_returns_next_openings_when_nothing_open(db_pool):
    at = datetime(2026, 9, 23, 3, 0)  # Wednesday 3am, before any window opens
    async with db_pool.acquire() as conn:
        await _seed_service(
            conn,
            name="Morning Pantry",
            category=Category.FOOD,
            lat=BASE_LAT,
            lon=BASE_LON,
            weekday=2,
            opens=time(9, 0),
            closes=time(17, 0),
        )

    open_results, upcoming = await get_open_now(
        db_pool, BASE_LAT, BASE_LON, (Category.FOOD,), at, limit=3
    )

    assert open_results == []
    assert len(upcoming) == 1
    name, opening_date, opening_time = upcoming[0]
    assert name == "Morning Pantry"
    assert opening_date == at.date()
    assert opening_time == time(9, 0)


@requires_db
async def test_get_open_now_respects_limit(db_pool):
    at = datetime(2026, 9, 23, 12, 0)  # Wednesday = weekday 2
    async with db_pool.acquire() as conn:
        for i in range(4):
            await _seed_service(
                conn,
                name=f"Pantry {i}",
                category=Category.FOOD,
                lat=BASE_LAT + i * 0.001,
                lon=BASE_LON,
                weekday=2,
                opens=time(9, 0),
                closes=time(17, 0),
            )

    open_results, upcoming = await get_open_now(
        db_pool, BASE_LAT, BASE_LON, (Category.FOOD,), at, limit=3
    )

    assert len(open_results) == 3
    assert upcoming == []
    distances = [r.distance_miles for r in open_results]
    assert distances == sorted(distances)
