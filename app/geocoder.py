from __future__ import annotations

import re
from typing import Any

import asyncpg
import httpx

from app.config import Settings, get_settings
from app.models import GeocodeResult

_WHITESPACE_RE = re.compile(r"\s+")

_ZIP_CENTROID_QUERY = """
    SELECT ST_Y(geom::geometry) AS lat, ST_X(geom::geometry) AS lon
    FROM zip_centroid
    WHERE zip = $1
"""

_CACHE_LOOKUP_QUERY = """
    SELECT ST_Y(geom::geometry) AS lat, ST_X(geom::geometry) AS lon
    FROM geocode_cache
    WHERE query_text = $1
"""

_CACHE_UPSERT_POINT_QUERY = """
    INSERT INTO geocode_cache (query_text, geom, resolved_at)
    VALUES ($1, ST_SetSRID(ST_MakePoint($2, $3), 4326)::geography, now())
    ON CONFLICT (query_text) DO UPDATE
        SET geom = EXCLUDED.geom, resolved_at = EXCLUDED.resolved_at
"""

_CACHE_UPSERT_AMBIGUOUS_QUERY = """
    INSERT INTO geocode_cache (query_text, geom, resolved_at)
    VALUES ($1, NULL, now())
    ON CONFLICT (query_text) DO UPDATE
        SET geom = NULL, resolved_at = now()
"""


def _normalize_query_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text.strip().lower())


def _in_county(lat: float, lon: float, settings: Settings) -> bool:
    return (
        settings.county_min_lat <= lat <= settings.county_max_lat
        and settings.county_min_lon <= lon <= settings.county_max_lon
    )


async def geocode_zip(pool: asyncpg.Pool, zip_code: str) -> GeocodeResult | None:
    settings = get_settings()
    row = await pool.fetchrow(_ZIP_CENTROID_QUERY, zip_code.strip())
    if row is None:
        return None
    lat = float(row["lat"])
    lon = float(row["lon"])
    return GeocodeResult(lat=lat, lon=lon, ambiguous=False, in_county=_in_county(lat, lon, settings))


async def _lookup_cache(pool: asyncpg.Pool, query_text: str) -> GeocodeResult | None:
    row = await pool.fetchrow(_CACHE_LOOKUP_QUERY, query_text)
    if row is None:
        return None
    if row["lat"] is None:
        return GeocodeResult(lat=0.0, lon=0.0, ambiguous=True)
    return GeocodeResult(lat=float(row["lat"]), lon=float(row["lon"]))


async def _store_cache(pool: asyncpg.Pool, query_text: str, lat: float | None, lon: float | None) -> None:
    if lat is None or lon is None:
        await pool.execute(_CACHE_UPSERT_AMBIGUOUS_QUERY, query_text)
    else:
        await pool.execute(_CACHE_UPSERT_POINT_QUERY, query_text, lon, lat)


async def _fetch_census_matches(settings: Settings, text: str) -> list[dict[str, Any]]:
    url = f"{settings.census_geocoder_base_url}/locations/onelineaddress"
    params = {"address": text, "benchmark": "Public_AR_Current", "format": "json"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
    result: dict[str, Any] = payload.get("result", {}) if isinstance(payload, dict) else {}
    matches = result.get("addressMatches", [])
    return matches if isinstance(matches, list) else []


async def geocode_text(pool: asyncpg.Pool, text: str, settings: Settings) -> GeocodeResult | None:
    normalized = _normalize_query_text(text)

    cached = await _lookup_cache(pool, normalized)
    if cached is not None:
        if cached.ambiguous:
            return cached
        cached.in_county = _in_county(cached.lat, cached.lon, settings)
        return cached

    try:
        matches = await _fetch_census_matches(settings, text)
    except (httpx.HTTPError, ValueError):
        return None

    if len(matches) != 1:
        await _store_cache(pool, normalized, None, None)
        return GeocodeResult(lat=0.0, lon=0.0, ambiguous=True)

    coordinates = matches[0]["coordinates"]
    lat = float(coordinates["y"])
    lon = float(coordinates["x"])
    await _store_cache(pool, normalized, lat, lon)
    return GeocodeResult(lat=lat, lon=lon, ambiguous=False, in_county=_in_county(lat, lon, settings))
