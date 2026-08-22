"""FastAPI entrypoint: a thin orchestration layer over the pure-function pipeline."""

from __future__ import annotations

import json
import logging
import time
import traceback

import os

from dotenv import load_dotenv

# Load .env from the project root before any module reads os.environ
# (Groq key, email alerts). No-op when the file is absent.
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

import ee
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend import alerts, cache, messages, watchlist
from backend.gee_client import GeeUnavailableError, check_connectivity, initialize
from backend.models.schemas import AnalyzeRequest, AnalyzeResponse
from backend.pipeline import (
    AOIError,
    AOITooLargeError,
    AOITooSmallError,
    NoImageryError,
    run_analysis,
)
from backend.pipeline.gee_timeout import GeeTimeoutError
from backend.routers.watershed import router as watershed_router

logger = logging.getLogger("hackpreneur")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="HackPreneur Satellite Change Detector", version="0.1.0")
app.include_router(watershed_router)  # watershed module (photos/thematic/timeline)

# The React dev server (Vite) runs on a different port; allow it to call the API.
# FRONTEND_ORIGIN (set on Render) adds the deployed Vercel URL; comma-separate
# multiple origins if needed (e.g. preview + prod Vercel URLs).
_default_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
_extra_origins = [o.strip() for o in os.environ.get("FRONTEND_ORIGIN", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_default_origins + _extra_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

_ERROR_MESSAGES: list[tuple[type, int, str]] = [
    (AOITooLargeError, 400, "Requested AOI is too large to process."),
    (AOITooSmallError, 400, "Requested AOI is too small for a meaningful analysis."),
    (AOIError, 400, "Invalid AOI. Use a GeoJSON geometry or [west, south, east, north] bbox."),
    (NoImageryError, 422, "No usable satellite imagery found in the requested date window."),
    (ValueError, 422, "Analysis could not be completed — the AOI likely has no detectable change."),
    (GeeUnavailableError, 503, "Google Earth Engine is unavailable or not authorized."),
    (GeeTimeoutError, 504, "A Google Earth Engine call exceeded its time limit — GEE is slow or unreachable. Try again, or use a cached preset."),
    (ee.ee_exception.EEException, 429, "Google Earth Engine request failed (check quota / payload size)."),
]

#: In-memory response cache for identical *custom* requests (Phase 19b).
#: Keyed by the exact request parameters (AOI + dates + mode + window + scale)
#: so a repeated request in the same server session returns instantly. Presets
#: are served by the on-disk cache instead. Session-scoped (reset on restart).
_RESPONSE_CACHE: dict[tuple, dict] = {}


def _request_cache_key(request: AnalyzeRequest) -> tuple:
    """Canonical hashable key covering every parameter that changes a result."""
    aoi = request.aoi
    if isinstance(aoi, list):
        aoi_key = tuple(round(v, 6) for v in aoi)
    else:
        aoi_key = json.dumps(aoi, sort_keys=True, default=str)
    return (
        aoi_key,
        request.before_date.isoformat(),
        request.after_date.isoformat(),
        request.mode,
        request.comparison_type,
        request.window_days,
        request.scale,
    )


@app.get("/health")
def health() -> dict:
    """Liveness + GEE connectivity probe for the frontend / infra."""
    try:
        ok = check_connectivity()
    except GeeUnavailableError:
        ok = False
    return {"status": "ok" if ok else "degraded", "gee_connected": ok}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest, background_tasks: BackgroundTasks) -> AnalyzeResponse:
    """Run the full change-detection pipeline for a request."""
    t_start = time.perf_counter()
    logger.info(
        "analyze before=%s after=%s mode=%s scale=%sm window=%dd use_cache=%s",
        request.before_date, request.after_date, request.mode, request.scale,
        request.window_days, request.use_cache,
    )
    preset_id = cache.preset_id_for(request)
    if request.use_cache and preset_id is not None:
        cached = cache.load_cached(preset_id)
        if cached is not None:
            logger.info("serving cached result for preset %s", preset_id)
            cached["cached"] = True
            cached["timing_ms"] = {
                "total": (time.perf_counter() - t_start) * 1000.0,
                "cached": 1.0,
            }
            resp = AnalyzeResponse(**cached)
            enriched = messages.enrich_response(preset_id, resp, timing_ms=resp.timing_ms)
            background_tasks.add_task(alerts.maybe_send_alert, enriched, preset_id)
            return enriched
    if preset_id is None:
        # Exact-parameter repeat of a custom AOI this session → instant,
        # regardless of use_cache (the frontend always sends false for customs).
        key = _request_cache_key(request)
        hit = _RESPONSE_CACHE.get(key)
        if hit is not None:
            logger.info("serving in-memory cached result for identical custom request")
            hit = dict(hit)
            hit["timing_ms"] = {
                "total": (time.perf_counter() - t_start) * 1000.0,
                "cached": 1.0,
            }
            resp = AnalyzeResponse(**hit)
            enriched = messages.enrich_response(None, resp, timing_ms=resp.timing_ms)
            background_tasks.add_task(alerts.maybe_send_alert, enriched, None)
            return enriched
    try:
        result = run_analysis(request)
        enriched = messages.enrich_response(preset_id, result, timing_ms=result.timing_ms)
        background_tasks.add_task(alerts.maybe_send_alert, enriched, preset_id)
        if enriched.timing_ms is not None:
            enriched.timing_ms["total"] = (time.perf_counter() - t_start) * 1000.0
        tm = enriched.timing_ms or {}
        logger.info(
            "analysis done in %.0fms  ingestion=%.0f indices+sample=%.0f "
            "change=%.0f fusion=%.0f quantify=%.0f geocode=%.0f "
            "thumb_b=%.0f thumb_a=%.0f thumb_mask=%.0f",
            tm.get("total", 0.0), tm.get("ingestion", 0.0), tm.get("indices", 0.0),
            tm.get("change_detection", 0.0), tm.get("fusion_classifier", 0.0),
            tm.get("quantify", 0.0), tm.get("geocoding", 0.0),
            tm.get("thumbnail_before", 0.0), tm.get("thumbnail_after", 0.0),
            tm.get("thumbnail_mask", 0.0),
        )
        if preset_id is not None:
            cache.save_cached(preset_id, enriched.model_dump())
        else:
            watchlist.remember(enriched)
            _RESPONSE_CACHE[_request_cache_key(request)] = enriched.model_dump()
        return enriched
    except Exception as exc:
        _raise_http(exc)


@app.get("/watchlist")
def get_watchlist() -> list[dict]:
    """Ranked list of already-analyzed results (3 presets + this session's customs).

    Priority is a relabel of the existing severity bucket (severe → High,
    moderate → Medium, mild → Low), sorted High → Medium → Low then by
    affected_pct descending. Runs no new analyses.
    """
    return watchlist.build_watchlist()


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler so nothing escapes to clients as a raw stack trace."""
    _raise_http(exc)


def _raise_http(exc: Exception) -> None:
    """Map an exception to a clean JSON error, re-raising as HTTPException."""
    for exc_type, status, message in _ERROR_MESSAGES:
        if isinstance(exc, exc_type):
            logger.warning("%s: %s", type(exc).__name__, exc)
            raise HTTPException(status_code=status, detail={"error": message, "detail": str(exc)}) from exc
    logger.error("Unhandled error: %s\n%s", exc, traceback.format_exc())
    raise HTTPException(status_code=500, detail={"error": "Internal server error.", "detail": str(exc)}) from exc


@app.on_event("startup")
def _startup() -> None:
    """Pre-warm the GEE session so the first request is fast and auth errors surface early."""
    try:
        initialize()
        logger.info("GEE initialized; connectivity=%s", check_connectivity())
    except GeeUnavailableError as exc:
        logger.error("GEE unavailable at startup — /analyze will fail until fixed. Detail: %s", exc)