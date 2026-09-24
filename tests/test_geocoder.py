from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app import geocoder
from app.config import get_settings
from app.models import GeocodeResult
from tests.conftest import requires_db

SETTINGS = get_settings()

IN_COUNTY_LAT = 37.33
IN_COUNTY_LON = -121.89
OUT_OF_COUNTY_LAT = 40.71
OUT_OF_COUNTY_LON = -74.01


class FakePool:
    def __init__(self, fetchrow_result: dict[str, Any] | None = None) -> None:
        self.fetchrow = AsyncMock(return_value=fetchrow_result)
        self.execute = AsyncMock(return_value=None)


_RealAsyncClient = httpx.AsyncClient


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    transport = httpx.MockTransport(handler)

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return _RealAsyncClient(*args, **kwargs)

    monkeypatch.setattr(geocoder.httpx, "AsyncClient", _factory)


def _install_network_error(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    _install_fake_client(monkeypatch, handler)


# ── geocode_zip ──────────────────────────────────────────────────────────────


async def test_geocode_zip_found_in_county() -> None:
    pool = FakePool({"lat": IN_COUNTY_LAT, "lon": IN_COUNTY_LON})
    result = await geocoder.geocode_zip(pool, "95112")
    assert result == GeocodeResult(lat=IN_COUNTY_LAT, lon=IN_COUNTY_LON, ambiguous=False, in_county=True)
    pool.fetchrow.assert_awaited_once()


async def test_geocode_zip_found_out_of_county() -> None:
    pool = FakePool({"lat": OUT_OF_COUNTY_LAT, "lon": OUT_OF_COUNTY_LON})
    result = await geocoder.geocode_zip(pool, "10001")
    assert result is not None
    assert result.in_county is False


async def test_geocode_zip_not_found() -> None:
    pool = FakePool(None)
    result = await geocoder.geocode_zip(pool, "00000")
    assert result is None


# ── geocode_text: cache hits ─────────────────────────────────────────────────


async def test_geocode_text_cache_hit_resolved() -> None:
    pool = FakePool({"lat": IN_COUNTY_LAT, "lon": IN_COUNTY_LON})
    result = await geocoder.geocode_text(pool, "  100 Main St  ", SETTINGS)
    assert result == GeocodeResult(lat=IN_COUNTY_LAT, lon=IN_COUNTY_LON, ambiguous=False, in_county=True)
    pool.fetchrow.assert_awaited_once()
    args, _ = pool.fetchrow.call_args
    assert args[1] == "100 main st"
    pool.execute.assert_not_awaited()


async def test_geocode_text_cache_hit_previously_ambiguous() -> None:
    pool = FakePool({"lat": None, "lon": None})
    result = await geocoder.geocode_text(pool, "Somewhere Vague", SETTINGS)
    assert result == GeocodeResult(lat=0.0, lon=0.0, ambiguous=True)
    pool.execute.assert_not_awaited()


# ── geocode_text: cache miss -> Census API ──────────────────────────────────


async def test_geocode_text_zero_matches_is_ambiguous_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakePool(None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"addressMatches": []}})

    _install_fake_client(monkeypatch, handler)

    result = await geocoder.geocode_text(pool, "1 Nowhere Ave", SETTINGS)
    assert result == GeocodeResult(lat=0.0, lon=0.0, ambiguous=True)
    pool.execute.assert_awaited_once()
    args, _ = pool.execute.call_args
    assert args[1] == "1 nowhere ave"


async def test_geocode_text_one_match_returns_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakePool(None)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["address"] == "First St & Second St, San Jose, CA"
        assert request.url.params["benchmark"] == "Public_AR_Current"
        assert request.url.params["format"] == "json"
        return httpx.Response(
            200,
            json={
                "result": {
                    "addressMatches": [
                        {"coordinates": {"x": IN_COUNTY_LON, "y": IN_COUNTY_LAT}},
                    ]
                }
            },
        )

    _install_fake_client(monkeypatch, handler)

    result = await geocoder.geocode_text(pool, "First St & Second St, San Jose, CA", SETTINGS)
    assert result == GeocodeResult(lat=IN_COUNTY_LAT, lon=IN_COUNTY_LON, ambiguous=False, in_county=True)
    pool.execute.assert_awaited_once()
    args, _ = pool.execute.call_args
    assert args[2] == IN_COUNTY_LON
    assert args[3] == IN_COUNTY_LAT


async def test_geocode_text_one_match_out_of_county(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakePool(None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "result": {
                    "addressMatches": [
                        {"coordinates": {"x": OUT_OF_COUNTY_LON, "y": OUT_OF_COUNTY_LAT}},
                    ]
                }
            },
        )

    _install_fake_client(monkeypatch, handler)

    result = await geocoder.geocode_text(pool, "Times Square, NY", SETTINGS)
    assert result is not None
    assert result.ambiguous is False
    assert result.in_county is False


async def test_geocode_text_multiple_matches_is_ambiguous_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakePool(None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "result": {
                    "addressMatches": [
                        {"coordinates": {"x": IN_COUNTY_LON, "y": IN_COUNTY_LAT}},
                        {"coordinates": {"x": IN_COUNTY_LON + 0.01, "y": IN_COUNTY_LAT + 0.01}},
                    ]
                }
            },
        )

    _install_fake_client(monkeypatch, handler)

    result = await geocoder.geocode_text(pool, "Main St, San Jose, CA", SETTINGS)
    assert result == GeocodeResult(lat=0.0, lon=0.0, ambiguous=True)
    pool.execute.assert_awaited_once()


# ── geocode_text: network errors ────────────────────────────────────────────


async def test_geocode_text_network_error_returns_none_without_caching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakePool(None)
    _install_network_error(monkeypatch, httpx.ConnectError("boom"))

    result = await geocoder.geocode_text(pool, "Unreachable St", SETTINGS)
    assert result is None
    pool.execute.assert_not_awaited()


async def test_geocode_text_timeout_returns_none_without_caching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakePool(None)
    _install_network_error(monkeypatch, httpx.ConnectTimeout("timed out"))

    result = await geocoder.geocode_text(pool, "Slow St", SETTINGS)
    assert result is None
    pool.execute.assert_not_awaited()


async def test_geocode_text_http_status_error_returns_none_without_caching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakePool(None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server error"})

    _install_fake_client(monkeypatch, handler)

    result = await geocoder.geocode_text(pool, "Broken St", SETTINGS)
    assert result is None
    pool.execute.assert_not_awaited()


# ── normalization ────────────────────────────────────────────────────────────


def test_normalize_query_text_collapses_whitespace_and_lowercases() -> None:
    assert geocoder._normalize_query_text("  100   MAIN   St \n") == "100 main st"


def test_in_county_bbox() -> None:
    settings = get_settings()
    assert geocoder._in_county(IN_COUNTY_LAT, IN_COUNTY_LON, settings) is True
    assert geocoder._in_county(OUT_OF_COUNTY_LAT, OUT_OF_COUNTY_LON, settings) is False


# ── requires a real Postgres/PostGIS instance ───────────────────────────────


@requires_db
async def test_geocode_zip_against_real_db(db_pool: Any) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO zip_centroid (zip, geom) VALUES ($1, "
            "ST_SetSRID(ST_MakePoint($2, $3), 4326)::geography)",
            "95112",
            IN_COUNTY_LON,
            IN_COUNTY_LAT,
        )

    result = await geocoder.geocode_zip(db_pool, "95112")
    assert result is not None
    assert result.in_county is True
    assert result.lat == pytest.approx(IN_COUNTY_LAT, abs=1e-6)
    assert result.lon == pytest.approx(IN_COUNTY_LON, abs=1e-6)


@requires_db
async def test_geocode_text_caches_against_real_db(db_pool: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "result": {
                    "addressMatches": [
                        {"coordinates": {"x": IN_COUNTY_LON, "y": IN_COUNTY_LAT}},
                    ]
                }
            },
        )

    _install_fake_client(monkeypatch, handler)

    settings = get_settings()
    first = await geocoder.geocode_text(db_pool, "First St & Second St", settings)
    assert first is not None
    assert first.ambiguous is False

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Census API should not be called again on a cache hit")

    monkeypatch.setattr(geocoder, "_fetch_census_matches", _boom)

    second = await geocoder.geocode_text(db_pool, "first st & second st", settings)
    assert second == GeocodeResult(
        lat=pytest.approx(IN_COUNTY_LAT, abs=1e-6),
        lon=pytest.approx(IN_COUNTY_LON, abs=1e-6),
        ambiguous=False,
        in_county=True,
    )
