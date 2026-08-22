"""AOI / date-range ingestion: build cloud-masked median composites from Sentinel-2."""

from __future__ import annotations

from datetime import date, timedelta

import ee

from backend.pipeline.masking import mask_scl

S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"

#: Minimum fraction of AOI pixels that must be clear (have composite data) for
#: a composite to be considered usable. Below this we widen the window and retry.
MIN_VALID_FRACTION = 0.3
#: Widening ladder for the adaptive composite window (days), capped here.
WIDENING_WINDOWS = (10, 14)
MAX_WINDOW_DAYS = 14
#: Sampling scale used for the cheap valid-fraction check (fast, pixel-count-safe).
_VALID_CHECK_SCALE = 200


class NoImageryError(RuntimeError):
    """Raised when a date window has no usable Sentinel-2 scenes."""


def median_composite(
    geometry: ee.Geometry,
    center_date: date,
    window_days: int = 6,
    min_scenes: int = 1,
    min_valid_fraction: float = MIN_VALID_FRACTION,
    max_window_days: int = MAX_WINDOW_DAYS,
    attempts_log: list[int] | None = None,
) -> tuple[ee.Image, int, int, float]:
    """Return a cloud-masked median composite around ``center_date``.

    Filters the Sentinel-2 SR collection to a ``window_days`` buffer on either
    side of the requested date, masks clouds/shadows/snow per scene via the SCL
    band, and collapses the stack to a per-pixel median. Medians are robust to
    residual cloud noise and missing orbits.

    **Adaptive windowing:** if the composite has fewer than ``min_valid_fraction``
    valid (non-cloud) AOI pixels, the window is automatically widened (e.g.
    ±6d → ±10d → ±14d, capped at ``max_window_days``) and retried before
    failing. This keeps regionally cloudy areas from silently producing a
    cloud-contaminated composite.

    Returns ``(composite, scene_count, window_used_days, valid_fraction)`` and
    raises ``NoImageryError`` when no window yields usable imagery. When an
    ``attempts_log`` list is supplied, each window tried is appended to it (so
    callers can see how far the adaptive ladder climbed).
    """
    candidates = _window_candidates(window_days, max_window_days)

    for win in candidates:
        if attempts_log is not None:
            attempts_log.append(win)
        count = _scene_count(geometry, center_date, win)
        if count < min_scenes:
            continue  # wider window may find scenes
        composite = _median_for_window(geometry, center_date, win)
        valid_frac = _valid_fraction(composite, geometry)
        if valid_frac >= min_valid_fraction:
            return composite, count, win, valid_frac
        # else: too cloudy for this window — widen and retry

    raise NoImageryError(
        f"No usable Sentinel-2 imagery for {center_date.isoformat()} over the "
        f"requested AOI — tried windows up to ±{max_window_days}d; fewer than "
        f"{int(min_valid_fraction * 100)}% of pixels are clear (persistent "
        f"cloud/snow) or no scenes were found."
    )


def _window_candidates(requested: int, max_window_days: int) -> list[int]:
    """Return the widening ladder starting at ``requested``, capped and deduped."""
    ladder = [requested]
    for w in WIDENING_WINDOWS:
        if w > requested and w <= max_window_days:
            ladder.append(w)
    if max_window_days > ladder[-1]:
        ladder.append(max_window_days)
    return ladder


def _scene_count(geometry: ee.Geometry, center_date: date, window_days: int) -> int:
    start = ee.Date(center_date.isoformat()).advance(-window_days, "day")
    end = ee.Date(center_date.isoformat()).advance(window_days + 1, "day")
    collection = (
        ee.ImageCollection(S2_COLLECTION)
        .filterBounds(geometry)
        .filterDate(start, end)
    )
    return int(collection.size().getInfo())


def _median_for_window(geometry: ee.Geometry, center_date: date, window_days: int) -> ee.Image:
    start = ee.Date(center_date.isoformat()).advance(-window_days, "day")
    end = ee.Date(center_date.isoformat()).advance(window_days + 1, "day")
    collection = (
        ee.ImageCollection(S2_COLLECTION)
        .filterBounds(geometry)
        .filterDate(start, end)
        .map(mask_scl)
    )
    return collection.median()


def _valid_fraction(
    image: ee.Image,
    geometry: ee.Geometry,
    scale: int = _VALID_CHECK_SCALE,
    max_pixels: int = 10_000_000,
) -> float:
    """Fraction of AOI pixels where the composite actually has clear data."""
    first_band = image.bandNames().slice(0, 1)  # server-side 1-band list
    valid = image.select(first_band).mask().unmask(0).rename("valid")
    stats = valid.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=geometry,
        scale=scale,
        bestEffort=True,
        maxPixels=max_pixels,
    )
    return float(stats.get("valid").getInfo())
