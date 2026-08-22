"""Reverse-geocoding (location naming) for the /analyze response.

Resolves an AOI centroid to a human-readable place name via OpenStreetMap
Nominatim, so a custom map-clicked AOI shows "Kishanganj District, Bihar,
India" instead of raw coordinates.

Design (Phase 15):
- The three validated presets use hardcoded names (no live lookups).
- Custom AOIs are reverse-geocoded from their bounding-box centroid with a
  proper User-Agent, rate-limited to ~1 req/sec (Nominatim usage policy), and
  cached in-memory keyed by the centroid rounded to 2 decimal places (~1 km),
  so repeat runs at the same location never hit the network again.
- Every failure mode (timeout, HTTP error, malformed response, outage) degrades
  gracefully to ``(None, None)`` — the analysis result is never blocked or
  failed because geocoding failed.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request

logger = logging.getLogger("hackpreneur")

#: Overridable via env for testing / offline demos. Pointing this at an
#: unreachable URL is the cleanest way to simulate a Nominatim outage.
NOMINATIM_URL = os.environ.get(
    "NOMINATIM_URL", "https://nominatim.openstreetmap.org/reverse"
)

USER_AGENT = (
    "HackPreneur-Satellite-Change-Detector/1.0 (hackathon demo; "
    "satellite change detection MVP)"
)

#: Nominatim usage policy is ~1 request/second.
MIN_INTERVAL_S = 1.0
TIMEOUT_S = 5.0

#: Hardcoded names for the three validated presets (no live geocoding needed).
PRESET_LOCATIONS: dict[str, tuple[str, str]] = {
    "kishanganj-flood-2017": ("Kishanganj District, Bihar, India", "validated preset"),
    "po-valley-drought-2022": ("Po Valley, Emilia-Romagna, Italy", "validated preset"),
    "nsw-bushfires-2019": ("Gospers Mountain, NSW, Australia", "validated preset"),
}

#: In-memory cache keyed by centroid rounded to 2 decimal places.
_cache: dict[tuple[float, float], tuple[str | None, str | None]] = {}

_last_request_at = 0.0


def _throttle() -> None:
    """Enforce ~1 request/sec against Nominatim (shared across requests)."""
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < MIN_INTERVAL_S:
        time.sleep(MIN_INTERVAL_S - elapsed)
    _last_request_at = time.monotonic()


def _precision_from_address(address: dict) -> str:
    """Finest admin level Nominatim reports, as a short human label."""
    for key in (
        "suburb",
        "quarter",
        "neighbourhood",
        "village",
        "town",
        "city",
        "municipality",
        "county",
        "district",
        "state",
        "region",
        "country",
    ):
        if key in address:
            return key
    return "region"


def _display_name(payload: dict) -> str | None:
    """Curate a concise name from Nominatim's address parts.

    Prefers ``settlement, district/county, country`` instead of the raw
    ``display_name`` (which appends postcodes and intermediate divisions).
    """
    addr = payload.get("address") or {}
    primary = (
        addr.get("village")
        or addr.get("town")
        or addr.get("city")
        or addr.get("municipality")
        or addr.get("county")
        or addr.get("district")
        or addr.get("state")
        or addr.get("region")
    )
    secondary = (
        addr.get("county")
        or addr.get("district")
        or addr.get("state")
        or addr.get("region")
    )
    country = addr.get("country")
    parts: list[str] = []
    for p in (primary, secondary, country):
        if p and p != (parts[-1] if parts else None):
            parts.append(p)
    if parts:
        return ", ".join(parts)
    raw = payload.get("display_name")
    if raw:
        postcode = addr.get("postcode")
        if postcode:
            raw = ", ".join(p for p in raw.split(", ") if p != postcode)
        return raw
    return None


def reverse_geocode(lat: float, lon: float) -> tuple[str | None, str | None]:
    """Resolve (name, precision) for a coordinate; never raises.

    Results are cached by the centroid rounded to 2 decimal places so repeat
    lookups in the same ~1 km cell are free. Any failure returns
    ``(None, None)`` and is cached as such (no hammering a down service).
    """
    key = (round(lat, 2), round(lon, 2))
    if key in _cache:
        return _cache[key]
    try:
        _throttle()
        url = (
            f"{NOMINATIM_URL}?lat={lat}&lon={lon}"
            "&format=jsonv2&zoom=12&addressdetails=1&accept-language=en"
        )
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        name = _display_name(payload)
        if not name:
            _cache[key] = (None, None)
            return (None, None)
        precision = _precision_from_address(payload.get("address") or {})
        _cache[key] = (name, precision)
        return _cache[key]
    except Exception:  # noqa: BLE001 — geocoding must never break the analysis
        logger.warning(
            "reverse geocoding failed for (%.4f, %.4f) — falling back to coordinates",
            lat,
            lon,
            exc_info=True,
        )
        _cache[key] = (None, None)
        return (None, None)


def location_for(
    aoi_bounds: list[float], preset_id: str | None
) -> tuple[str | None, str | None]:
    """Resolve a display name for an AOI; never raises.

    Presets use hardcoded names; custom AOIs are reverse-geocoded from the
    bounding-box centroid. Returns ``(None, None)`` when no name is available.
    """
    if preset_id is not None:
        return PRESET_LOCATIONS.get(preset_id, (None, None))
    if not aoi_bounds or len(aoi_bounds) != 4:
        return (None, None)
    west, south, east, north = aoi_bounds
    return reverse_geocode((south + north) / 2.0, (west + east) / 2.0)
