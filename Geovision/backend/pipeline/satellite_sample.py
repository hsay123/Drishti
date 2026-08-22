"""Fuse geo-tagged photo points with satellite index values (the core of the
problem statement).

``sample_indices_at_point(lat, lon, date)`` returns the NDVI/NDWI values at
one field-photo location for a given date, sampled from the same Sentinel-2
cloud-masked median composites used by the /analyze pipeline (existing
``ingestion`` + ``indices`` stages, unchanged). This is the photo↔satellite
linkage: a ground-truth photograph anchored to its nearest pixel.

Data-source honesty: SRISHTI-DRISHTI has no public API at build time, so this
samples SRISHTI-DRISHTI-**equivalent** 30 m-class open data via Google Earth
Engine and labels itself accordingly in ``SatelliteSample.source``
(``SRISHTI-DRISHTI API integration pending public access``).

Pluggable seams (constraint #5): the composite builder, index computer, and
point reducer are module-level callables injectable via parameters — swap the
data source in one place. GEE round-trips are bounded by the same hard-timeout
wrapper as the rest of the pipeline.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Callable

import ee

from backend.models.photo import SatelliteSample
from backend.pipeline.gee_timeout import call_with_timeout
from backend.pipeline.indices import IndexMode, compute_index
from backend.pipeline.ingestion import median_composite

logger = logging.getLogger("hackpreneur")

#: Hard wall-clock ceiling for the whole point-sampling round trip.
SAMPLE_TIMEOUT_S = 90.0

#: Half-width of the sampling box around the photo point (metres) — small on
#: purpose: we want "conditions at the photographed spot", not the neighbourhood.
POINT_WINDOW_M = 45.0

#: Sampling grid for the reduceRegion mean (m) — matches the live custom-AOI default.
POINT_SCALE_M = 20

#: Honest source label embedded in every sample.
SOURCE_LABEL = (
    "sentinel2_sr_20m (SRISHTI-DRISHTI-equivalent 30m data; "
    "SRISHTI-DRISHTI API integration pending public access)"
)


def _composite_builder(geometry: ee.Geometry, center_date: date, window_days: int):
    """Default composite builder — the existing adaptive-window ingestion stage."""
    return median_composite(geometry, center_date, window_days)


def _index_computer(img: ee.Image, mode: IndexMode) -> ee.Image:
    """Default index computer — the existing indices stage."""
    return compute_index(img, mode)


def _mean_reducer(index_img: ee.Image, geometry: ee.Geometry, scale_m: int) -> dict:
    """Default point reducer — mean index value over the small sampling box."""
    stats = index_img.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=geometry,
        scale=scale_m,
        bestEffort=True,
        maxPixels=10_000_000,
    )
    return stats.getInfo()


def sample_indices_at_point(
    lat: float,
    lon: float,
    center_date: date,
    *,
    window_days: int = 6,
    scale_m: int = POINT_SCALE_M,
    timeout_s: float = SAMPLE_TIMEOUT_S,
    composite_fn=None,
    index_fn=None,
    reducer_fn=None,
    geometry_fn=None,
) -> SatelliteSample | None:
    """NDVI/NDWI at ``(lat, lon)`` around ``center_date``, or ``None``.

    Returns ``None`` (never raises) when no imagery exists for the window or
    GEE fails/times out — photo uploads must not break because satellites are
    unavailable. The injected ``*_fn`` seams exist so tests can stub the data
    source without GEE credentials.
    """
    try:
        def run() -> SatelliteSample:
            build = composite_fn or _composite_builder
            compute = index_fn or _index_computer
            reduce = reducer_fn or _mean_reducer
            make_geometry = geometry_fn or _point_window_geometry

            geometry = make_geometry(lat, lon)
            composite, _scenes, _win, _frac = build(geometry, center_date, window_days)
            values: dict[str, float | None] = {}
            for mode, key in ((IndexMode.NDVI, "ndvi"), (IndexMode.NDWI, "ndwi")):
                # indices.compute_index renames its output band to "index".
                stats = reduce(compute(composite, mode), geometry, scale_m)
                values[key] = _finite(stats.get("index"))
            return SatelliteSample(
                ndvi=values["ndvi"],
                ndwi=values["ndwi"],
                date=center_date.isoformat(),
                source=SOURCE_LABEL,
            )

        return call_with_timeout(run, timeout_s, "photo-point satellite sampling")
    except Exception as exc:  # noqa: BLE001 — degrade gracefully by design
        logger.warning(
            "satellite sampling unavailable for (%.5f, %.5f) @ %s: %s",
            lat, lon, center_date, exc,
        )
        return None


def _point_window_geometry(lat: float, lon: float) -> ee.Geometry:
    """A small square around the photo point (~±POINT_WINDOW_M per side)."""
    return (
        ee.Geometry.Point([lon, lat])
        .buffer(POINT_WINDOW_M)
        .bounds(maxError=1)
    )


def _finite(value) -> float | None:
    """Coerce a reducer result to a finite float; anything else → None."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN check
