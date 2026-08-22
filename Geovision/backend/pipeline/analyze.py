"""End-to-end analysis orchestration shared by the API and test scripts."""

from __future__ import annotations

import math

import ee

from backend.gee_client import initialize
from backend.models.schemas import AnalyzeRequest, AnalyzeResponse
from backend.pipeline import change_detection, features, fusion_classifier, quantify, visualization
from backend.pipeline.indices import IndexMode
from backend.pipeline.ingestion import NoImageryError, median_composite
from backend.pipeline.timing import Timings
from backend.pipeline.gee_timeout import call_with_timeout
from backend.cache import preset_id_for

import logging

logger = logging.getLogger("hackpreneur")

#: Hard per-phase wall-clock ceilings for GEE round-trips (Phase 19d). GEE's
#: client has no read timeout, so a stalled compute must fail with a clean 504
#: instead of hanging a /analyze request forever. Generous headroom over the
#: observed ~41 s live budget; far below "never".
INGESTION_TIMEOUT_S = 90.0
SAMPLE_TIMEOUT_S = 180.0
THUMBNAIL_TIMEOUT_S = 180.0

#: Hard cap on sampled pixels at the request scale (guard against runaway AOIs).
MAX_SAMPLE_PIXELS = 40_000_000

#: Minimum sampled pixels for a statistically meaningful Otsu + fusion analysis.
#: Too-small AOIs (e.g. a few pixels) produce nonsense change fractions, so they
#: are rejected up front with a clear message instead of a misleading result.
MIN_AOI_PIXELS = 10_000

#: Same-season NDVI comparisons longer than this (in days) are vulnerable to the
#: harvest/senescence confound (the Po Valley Phase 6 bug).
SAME_SEASON_CAVEAT_DAYS = 45


class AOIError(RuntimeError):
    """Raised for invalid / unusable AOI definitions."""


class AOITooLargeError(AOIError):
    """Raised when the AOI would exceed the pixel sampling budget."""


class AOITooSmallError(AOIError):
    """Raised when the AOI would yield too few pixels for a meaningful result."""


def geometry_from_aoi(aoi: dict | list) -> ee.Geometry:
    """Parse a GeoJSON geometry dict or a ``[w, s, e, n]`` bbox into ee.Geometry."""
    if isinstance(aoi, dict):
        if aoi.get("type") == "Feature":
            aoi = aoi.get("geometry", aoi)
        return ee.Geometry(aoi)
    if isinstance(aoi, list) and len(aoi) == 4:
        return ee.Geometry.Rectangle(
            coords=aoi, proj="EPSG:4326", geodesic=False
        )
    raise AOIError(
        "aoi must be a GeoJSON geometry dict or a [west, south, east, north] bbox."
    )


def _guard_aoi_size(geometry: ee.Geometry, scale: int) -> None:
    """Reject AOIs outside the usable sampling budget at the requested scale."""
    area_m2 = float(geometry.area(maxError=1).getInfo())
    pixels = area_m2 / (scale * scale)
    if pixels < MIN_AOI_PIXELS:
        raise AOITooSmallError(
            f"AOI of ~{area_m2 / 1e6:.4f} km² yields only ~{pixels:.0f} pixels at "
            f"{scale}m scale — below the {MIN_AOI_PIXELS:,} pixel minimum for a "
            "statistically meaningful change analysis. Enlarge the AOI or reduce "
            "the scale."
        )
    if pixels > MAX_SAMPLE_PIXELS:
        raise AOITooLargeError(
            f"AOI of {area_m2 / 1e6:.0f} km² needs ~{pixels / 1e6:.1f}M pixels at "
            f"{scale}m scale — above the {MAX_SAMPLE_PIXELS / 1e6:.0f}M sampling "
            "budget. Shrink the AOI or increase the scale."
        )


def _aoi_bounds(geometry: ee.Geometry) -> list[float]:
    """Lon/lat bounding box ``[west, south, east, north]`` of the AOI."""
    coords = geometry.bounds(maxError=1).getInfo()["coordinates"][0]
    xs = [pt[0] for pt in coords]
    ys = [pt[1] for pt in coords]
    return [min(xs), min(ys), max(xs), max(ys)]


def seasonal_caveat(request: AnalyzeRequest) -> str | None:
    """Return a caveat when crop-stress mode risks the harvest confound.

    Comparing NDVI within one growing season over a long gap conflates real crop
    stress with normal harvest/senescence (the Po Valley bug fixed in Phase 6).
    We warn rather than block so the user still sees the analysis, but the risk
    is stated explicitly and year-over-year comparison is recommended.
    """
    if request.mode != "ndvi":
        return None
    if request.comparison_type != "same_season":
        return None
    gap_days = (request.after_date - request.before_date).days
    if gap_days <= SAME_SEASON_CAVEAT_DAYS:
        return None
    return (
        f"Same-season comparison over {gap_days} days may conflate crop stress "
        "with normal harvest/senescence. Year-over-year comparison (same "
        "calendar window, consecutive years) is recommended for more reliable "
        "crop-stress detection."
    )


def confidence_grade(
    before_frac: float,
    after_frac: float,
    scenes_before: int,
    scenes_after: int,
    preset_id: str | None,
) -> tuple[str, str]:
    """Self-reported confidence (high/medium/low) for a run's result.

    Scored from the clear-sky fraction of both composites, the number of scenes
    per window, and whether the request is one of the explicitly validated
    presets. This is an honest *proxy*, not validated accuracy: custom regions
    are never scored higher than "medium" unless coverage is strong, and the
    note always states when a region is not independently validated.
    """
    min_frac = min(before_frac, after_frac)
    min_scenes = min(scenes_before, scenes_after)

    if preset_id is not None:
        if min_frac >= 0.5:
            return "high", (
                f"Validated preset region ({preset_id}) with adequate "
                "clear-sky coverage."
            )
        if min_frac >= 0.3:
            return "medium", (
                f"Validated preset region ({preset_id}), but clear-sky "
                "coverage was limited this run."
            )
        return "low", (
            f"Validated preset region ({preset_id}), but clear-sky coverage "
            "was poor this run."
        )

    if min_frac >= 0.7 and min_scenes >= 2:
        return "high", (
            "Strong clear-sky coverage from multiple scenes; this custom region "
            "is not independently validated."
        )
    if min_frac >= 0.4:
        return "medium", (
            "Adequate clear-sky coverage; this custom region is not "
            "independently validated."
        )
    return "low", (
        "Limited clear-sky coverage; this custom region is not independently "
        "validated."
    )


def run_analysis(request: AnalyzeRequest) -> AnalyzeResponse:
    """Execute the full pipeline for a request and build the response object."""
    timings = Timings()
    initialize()

    # Presets sample at the fixed 10 m grid that produced their validated cache
    # numbers (any scale change would alter them → forbidden). Custom AOIs honor
    # the requested scale (default 20 m) — 4x fewer pixels than 10 m, with
    # negligible statistical impact, and `quantify` gets the matching scale_m so
    # area math stays exact.
    preset_id = preset_id_for(request)
    sampling_scale = features.DEFAULT_SCALE if preset_id is not None else request.scale
    logger.info("sampling scale=%dm preset=%s", sampling_scale, preset_id)

    with timings.timeit("aoi_guard"):
        geometry = geometry_from_aoi(request.aoi)
        _guard_aoi_size(geometry, request.scale)

    before_attempts: list[int] = []
    after_attempts: list[int] = []
    with timings.timeit("ingestion"):
        before_comp, scenes_before, before_window, before_frac = call_with_timeout(
            lambda: median_composite(
                geometry, request.before_date, request.window_days,
                attempts_log=before_attempts,
            ),
            INGESTION_TIMEOUT_S,
            "before-composite (median_composite)",
        )
        after_comp, scenes_after, after_window, after_frac = call_with_timeout(
            lambda: median_composite(
                geometry, request.after_date, request.window_days,
                attempts_log=after_attempts,
            ),
            INGESTION_TIMEOUT_S,
            "after-composite (median_composite)",
        )
    logger.info(
        "composites  before window=%dd valid=%.2f scenes=%d attempts=%s | after window=%dd valid=%.2f scenes=%d attempts=%s",
        before_window, before_frac, scenes_before, before_attempts,
        after_window, after_frac, scenes_after, after_attempts,
    )

    mode = IndexMode(request.mode)
    with timings.timeit("indices"):
        stack = features.build_feature_stack(
            before_comp,
            after_comp,
            geometry,
            mode,
            request.before_date,
            request.after_date,
            before_window,
            after_window,
            scale=sampling_scale,
        )
        bands = call_with_timeout(
            lambda: features.sample_feature_stack(stack, geometry, scale=sampling_scale),
            SAMPLE_TIMEOUT_S,
            "feature-stack sampling (computePixels)",
        )

    change = bands["change"]
    direction = mode.direction

    with timings.timeit("change_detection"):
        otsu_t = change_detection.otsu_threshold(change, direction)
        raw_mask = change_detection.apply_threshold(change, otsu_t, direction)

    with timings.timeit("fusion_classifier"):
        fusion = fusion_classifier.fusion_predict(bands, otsu_t, direction)

    # Phase 19c was trialed (morphological opening, then connected-component
    # speckle filtering) but REVERTED: both moved the flagged area more than the
    # ~0.5 pp acceptance gate on the NSW burn preset (3x3 opening −4.26pp,
    # ≤4px island filter −0.70pp), and would desync live runs from the cached
    # headline numbers. The raw fusion mask is used as-is.
    cleaned_mask = fusion.mask

    with timings.timeit("quantify"):
        stats = quantify.quantify(
            cleaned_mask,
            bands["in_aoi"],
            scale_m=sampling_scale,
        )
        before_coverage_pct = quantify.per_date_coverage(
            bands["before_index"], bands["before_valid"], bands["in_aoi"], request.mode
        )
        after_coverage_pct = quantify.per_date_coverage(
            bands["after_index"], bands["after_valid"], bands["in_aoi"], request.mode
        )

    before_thumb, after_thumb, mask_thumb = call_with_timeout(
        lambda: visualization.render_thumbnails(
            before_comp, after_comp, cleaned_mask, request.mode, geometry, timings=timings
        ),
        THUMBNAIL_TIMEOUT_S,
        "thumbnail rendering",
    )

    raw_pct = 100.0 * raw_mask[bands["in_aoi"].astype(bool)].sum() / max(
        1, int(bands["in_aoi"].astype(bool).sum())
    )

    confidence, confidence_note = confidence_grade(
        before_frac, after_frac, scenes_before, scenes_after, preset_id
    )

    # SCL cloud/snow masking is applied inside the ingestion composites and the
    # JRC permanent-water mask is folded into the sampled stack — neither has its
    # own GEE round-trip, so there is no separately measurable masking stage.
    timings["masking"] = 0.0
    # `geocoding` and `total` are filled in by main.py / messages.enrich_response
    # after run_analysis returns.
    timings["geocoding"] = 0.0
    timings["total"] = timings.total_ms()

    return AnalyzeResponse(
        mode=request.mode,
        before_date=request.before_date,
        after_date=request.after_date,
        comparison_type=request.comparison_type,
        window_days=request.window_days,
        aoi_bounds=_aoi_bounds(geometry),
        caveat=seasonal_caveat(request),
        confidence=confidence,
        confidence_note=confidence_note,
        affected_ha=stats.affected_ha,
        aoi_ha=stats.aoi_ha,
        affected_pct=stats.affected_pct,
        severity=stats.severity,
        before_coverage_pct=before_coverage_pct,
        after_coverage_pct=after_coverage_pct,
        otsu_threshold=round(otsu_t, 5),
        classifier_labeled_pixels=fusion.n_labeled,
        classifier_bootstrap_fit_score=round(fusion.train_score, 4),
        classifier_note=fusion_classifier.BOOTSTRAP_SCORE_NOTE,
        changed_pixels=stats.changed_pixels,
        total_pixels=stats.total_pixels,
        scenes_before=scenes_before,
        scenes_after=scenes_after,
        before_thumbnail_url=before_thumb,
        after_thumbnail_url=after_thumb,
        mask_thumbnail_url=mask_thumb,
        timing_ms=timings,
    )
