from __future__ import annotations

import asyncio
import json
from datetime import time as dt_time

from jobs.ingest import (
    SOURCES,
    build_existing_index,
    city_guide_adapter,
    classify_candidate,
    classify_candidates,
    diff_against_db,
    hours_signature,
    listing_key,
    load_manual_seed_adapter,
    second_harvest_locator_adapter,
)
from tests.conftest import requires_db


def test_listing_key_normalizes_case_and_whitespace():
    a = listing_key("  Roosevelt Park  Pantry ", "901 E Santa Clara St", "PANTRY")
    b = listing_key("roosevelt park pantry", "901 e santa clara st", "pantry")
    assert a == b


def test_listing_key_distinguishes_category_at_same_address():
    a = listing_key("Community Center", "123 Main St", "food")
    b = listing_key("Community Center", "123 Main St", "shower")
    assert a != b


def test_hours_signature_ignores_order():
    hours_a = [
        {"weekday": 1, "opens": "09:00", "closes": "12:00"},
        {"weekday": 3, "opens": "09:00", "closes": "12:00"},
    ]
    hours_b = list(reversed(hours_a))
    assert hours_signature(hours_a) == hours_signature(hours_b)


def test_hours_signature_treats_db_time_objects_same_as_strings():
    from_db = [{"weekday": 1, "opens": dt_time(9, 0), "closes": dt_time(12, 0)}]
    from_candidate = [{"weekday": 1, "opens": "09:00", "closes": "12:00"}]
    assert hours_signature(from_db) == hours_signature(from_candidate)


def test_hours_signature_detects_changed_hours():
    original = [{"weekday": 1, "opens": "09:00", "closes": "12:00"}]
    changed = [{"weekday": 1, "opens": "09:00", "closes": "13:00"}]
    assert hours_signature(original) != hours_signature(changed)


def _existing_rows() -> list[dict]:
    return [
        {
            "location_name": "Roosevelt Park Community Pantry",
            "address": "901 E Santa Clara St, San Jose, CA 95116",
            "category": "pantry",
            "weekday": 2,
            "opens": dt_time(9, 0),
            "closes": dt_time(12, 0),
        },
        {
            "location_name": "Roosevelt Park Community Pantry",
            "address": "901 E Santa Clara St, San Jose, CA 95116",
            "category": "pantry",
            "weekday": 5,
            "opens": dt_time(9, 0),
            "closes": dt_time(12, 0),
        },
    ]


def test_build_existing_index_groups_schedule_rows_by_listing():
    index = build_existing_index(_existing_rows())
    key = listing_key(
        "Roosevelt Park Community Pantry", "901 E Santa Clara St, San Jose, CA 95116", "pantry"
    )
    assert key in index
    assert len(index[key]["hours"]) == 2


def test_build_existing_index_handles_service_with_no_schedule_rows():
    rows = [
        {
            "location_name": "No Hours Yet",
            "address": "5 Test Way",
            "category": "wifi",
            "weekday": None,
            "opens": None,
            "closes": None,
        }
    ]
    index = build_existing_index(rows)
    key = listing_key("No Hours Yet", "5 Test Way", "wifi")
    assert index[key]["hours"] == []


def test_classify_candidate_new_when_not_in_index():
    index = build_existing_index(_existing_rows())
    candidate = {
        "location_name": "Brand New Pantry",
        "address": "1 Nowhere Ln, San Jose, CA 95112",
        "category": "pantry",
        "hours": [{"weekday": 1, "opens": "09:00", "closes": "12:00"}],
    }
    assert classify_candidate(candidate, index) == "new"


def test_classify_candidate_unchanged_when_hours_match():
    index = build_existing_index(_existing_rows())
    candidate = {
        "location_name": "Roosevelt Park Community Pantry",
        "address": "901 E Santa Clara St, San Jose, CA 95116",
        "category": "pantry",
        "hours": [
            {"weekday": 2, "opens": "09:00", "closes": "12:00"},
            {"weekday": 5, "opens": "09:00", "closes": "12:00"},
        ],
    }
    assert classify_candidate(candidate, index) == "unchanged"


def test_classify_candidate_changed_when_hours_differ():
    index = build_existing_index(_existing_rows())
    candidate = {
        "location_name": "Roosevelt Park Community Pantry",
        "address": "901 E Santa Clara St, San Jose, CA 95116",
        "category": "pantry",
        "hours": [{"weekday": 2, "opens": "10:00", "closes": "12:00"}],
    }
    assert classify_candidate(candidate, index) == "changed"


def test_classify_candidates_buckets_all_three_kinds():
    index = build_existing_index(_existing_rows())
    candidates = [
        {
            "location_name": "Roosevelt Park Community Pantry",
            "address": "901 E Santa Clara St, San Jose, CA 95116",
            "category": "pantry",
            "hours": [
                {"weekday": 2, "opens": "09:00", "closes": "12:00"},
                {"weekday": 5, "opens": "09:00", "closes": "12:00"},
            ],
        },
        {
            "location_name": "Roosevelt Park Community Pantry",
            "address": "901 E Santa Clara St, San Jose, CA 95116",
            "category": "pantry",
            "hours": [{"weekday": 2, "opens": "10:00", "closes": "12:00"}],
        },
        {
            "location_name": "Totally New Place",
            "address": "2 Somewhere Ave, San Jose, CA 95112",
            "category": "shower",
            "hours": [],
        },
    ]
    result = classify_candidates(candidates, index)
    assert result["unchanged_count"] == 1
    assert len(result["changed"]) == 1
    assert len(result["new"]) == 1


def test_manual_seed_adapter_reads_seed_file_with_plausible_county_entries():
    listings = asyncio.run(load_manual_seed_adapter(object()))
    assert len(listings) >= 2
    for listing in listings:
        assert listing["category"] in {"food", "pantry", "shower", "dropin", "wifi", "clothes"}
        assert 36.5 < listing["lat"] < 37.6  # roughly Santa Clara County
        assert -122.3 < listing["lon"] < -121.1
        assert listing["source"]
        assert isinstance(listing["hours"], list)


def test_sources_list_is_non_empty_and_all_async_callables():
    assert len(SOURCES) >= 1
    for source in SOURCES:
        assert asyncio.iscoroutinefunction(source)


def test_stub_adapters_are_documented_no_ops():
    assert asyncio.run(second_harvest_locator_adapter(object())) == []
    assert asyncio.run(city_guide_adapter(object())) == []
    assert second_harvest_locator_adapter.__doc__
    assert city_guide_adapter.__doc__


class _FakeConn:
    def __init__(self, existing_rows: list[dict]):
        self._existing_rows = existing_rows
        self.executed: list[tuple[str, list]] = []

    async def fetch(self, query, *args):
        return self._existing_rows

    async def executemany(self, query, records):
        self.executed.append((query, list(records)))


class _FakeAcquire:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, existing_rows: list[dict]):
        self.conn = _FakeConn(existing_rows)

    def acquire(self):
        return _FakeAcquire(self.conn)


def test_diff_against_db_never_writes_service_or_location_but_queues_review():
    pool = _FakePool(_existing_rows())
    candidates = [
        {
            "location_name": "Roosevelt Park Community Pantry",
            "address": "901 E Santa Clara St, San Jose, CA 95116",
            "category": "pantry",
            "hours": [
                {"weekday": 2, "opens": "09:00", "closes": "12:00"},
                {"weekday": 5, "opens": "09:00", "closes": "12:00"},
            ],
        },
        {
            "location_name": "Totally New Place",
            "address": "2 Somewhere Ave, San Jose, CA 95112",
            "category": "shower",
            "hours": [],
        },
    ]

    result = asyncio.run(diff_against_db(pool, candidates))

    assert result["unchanged_count"] == 1
    assert len(result["new"]) == 1
    assert result["new"][0]["location_name"] == "Totally New Place"

    assert len(pool.conn.executed) == 1
    query, records = pool.conn.executed[0]
    assert "ingest_review" in query
    assert len(records) == 1
    kind, payload_json = records[0]
    assert kind == "new"
    payload = json.loads(payload_json)
    assert payload["location_name"] == "Totally New Place"


def test_diff_against_db_skips_write_when_nothing_new_or_changed():
    pool = _FakePool(_existing_rows())
    candidates = [
        {
            "location_name": "Roosevelt Park Community Pantry",
            "address": "901 E Santa Clara St, San Jose, CA 95116",
            "category": "pantry",
            "hours": [
                {"weekday": 2, "opens": "09:00", "closes": "12:00"},
                {"weekday": 5, "opens": "09:00", "closes": "12:00"},
            ],
        },
    ]
    result = asyncio.run(diff_against_db(pool, candidates))
    assert result["unchanged_count"] == 1
    assert pool.conn.executed == []


@requires_db
async def test_diff_against_db_against_real_postgres(db_pool):
    # migrations/002_ingest_review.sql is applied by the db_pool fixture already.
    async with db_pool.acquire() as conn:
        org_id = await conn.fetchval(
            "INSERT INTO organization (name) VALUES ('Test Org') RETURNING id"
        )
        loc_id = await conn.fetchval(
            """
            INSERT INTO location (organization_id, name, address, geom)
            VALUES ($1, 'Existing Pantry', '1 Test St, San Jose, CA 95112',
                    ST_SetSRID(ST_MakePoint(-121.88, 37.33), 4326)::geography)
            RETURNING id
            """,
            org_id,
        )
        service_id = await conn.fetchval(
            """
            INSERT INTO service (location_id, name, category)
            VALUES ($1, 'Existing Pantry Food', 'pantry')
            RETURNING id
            """,
            loc_id,
        )
        await conn.execute(
            "INSERT INTO schedule (service_id, weekday, opens, closes) "
            "VALUES ($1, 1, '09:00', '12:00')",
            service_id,
        )

    candidates = [
        {
            "location_name": "Existing Pantry",
            "address": "1 Test St, San Jose, CA 95112",
            "category": "pantry",
            "hours": [{"weekday": 1, "opens": "09:00", "closes": "12:00"}],
        },
        {
            "location_name": "Brand New Shelter",
            "address": "2 Test Ave, San Jose, CA 95112",
            "category": "dropin",
            "hours": [],
        },
    ]

    result = await diff_against_db(db_pool, candidates)

    assert result["unchanged_count"] == 1
    assert len(result["new"]) == 1

    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT kind, payload FROM ingest_review")
    assert len(rows) == 1
    assert rows[0]["kind"] == "new"
