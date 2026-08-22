"""Watershed-development API router (photos, thematic layers, timeline).

Deliberately a single APIRouter registered with one ``app.include_router``
line in ``main.py`` — the smallest possible diff to the locked orchestration
file. Domain errors raised by the pipeline modules are caught *here* and
mapped to clean HTTP JSON (same ``{"error", "detail"}`` body shape as
``main.py:_ERROR_MESSAGES``), so ``_ERROR_MESSAGES`` itself stays untouched.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend import photo_store
from backend.models.photo import PhotoPoint
from backend.pipeline.photo_classifier import classify_photo
from backend.pipeline.narrative import build_narrative
from backend.pipeline.photo_ingest import (
    CorruptImageError,
    InvalidAoiError,
    MissingGPSExifError,
    PhotoError,
    PhotoOutsideAoiError,
    ensure_in_aoi,
    extract_photo_metadata,
)
from backend.pipeline.satellite_sample import sample_indices_at_point

logger = logging.getLogger("hackpreneur")

router = APIRouter(tags=["watershed"])

_STATUS_BY_ERROR: list[tuple[type[Exception], int, str]] = [
    (CorruptImageError, 400, "Uploaded file is not a usable image."),
    (MissingGPSExifError, 400, "Photo has no GPS EXIF — geo-tagged photos required."),
    (InvalidAoiError, 400, "Invalid AOI. Use a bbox or GeoJSON Polygon/MultiPolygon."),
    (PhotoOutsideAoiError, 422, "Photo is outside the selected AOI."),
    (PhotoError, 422, "Photo could not be ingested."),
]


def _raise_http(exc: Exception) -> None:
    """Map a photo-domain exception to the clean error-body convention."""
    for exc_type, status, message in _STATUS_BY_ERROR:
        if isinstance(exc, exc_type):
            logger.warning("%s: %s", type(exc).__name__, exc)
            raise HTTPException(
                status_code=status, detail={"error": message, "detail": str(exc)}
            ) from exc
    raise HTTPException(
        status_code=500, detail={"error": "Internal server error.", "detail": str(exc)}
    ) from exc


def _parse_aoi(raw: str) -> dict[str, Any] | list[float]:
    """Parse the multipart ``aoi`` field: a bbox or GeoJSON geometry as JSON."""
    try:
        aoi = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidAoiError(f"aoi is not valid JSON: {raw[:120]!r}") from exc
    if not isinstance(aoi, (dict, list)):
        raise InvalidAoiError("aoi must be a JSON bbox array or GeoJSON object.")
    return aoi


@router.post("/photos/upload", response_model=PhotoPoint)
async def upload_photo(
    file: UploadFile = File(...),
    aoi: str = Form(...),
    watershed_id: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    auto_classify: bool = Form(default=True),
) -> PhotoPoint:
    """Ingest one geo-tagged field photo and index it for the watershed map.

    The photo must carry GPS EXIF inside the given AOI; it is stored on disk
    under ``data/photos/`` and its metadata appended to the JSON index. When
    ``auto_classify`` is set (default), the Groq vision model labels the photo
    (degrading to ``unclear`` when unconfigured) and the point is fused with
    SRISHTI-DRISHTI-equivalent satellite index values at its coordinates —
    neither step can fail the upload.
    """
    try:
        image_bytes = await file.read()
        metadata = extract_photo_metadata(
            image_bytes,
            filename=file.filename or "photo.jpg",
            photo_id=uuid.uuid4().hex[:12],
            watershed_id=watershed_id,
            notes=notes,
        )
        parsed_aoi = _parse_aoi(aoi)
        ensure_in_aoi(metadata.lat, metadata.lon, parsed_aoi, metadata.filename)

        suffix = _suffix_for(file.content_type, metadata.filename)
        photo_store.save_photo_file(metadata.id, image_bytes, suffix)
        entry = _enriched_point(metadata, image_bytes) if auto_classify else PhotoPoint(
            **metadata.model_dump()
        )
        photo_store.upsert(entry.model_dump())
        logger.info(
            "photo uploaded %s at (%.5f, %.5f) watershed=%s category=%s",
            metadata.id, metadata.lat, metadata.lon, watershed_id,
            entry.classification.category if entry.classification else "skipped",
        )
        return entry
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — one mapping point, like main.py
        _raise_http(exc)


def _enriched_point(metadata, image_bytes: bytes) -> PhotoPoint:
    """Attach classification + satellite fusion to a fresh photo point.

    Deliberately best-effort: an unavailable classifier or satellite source
    degrades that field to ``None``/``unclear`` instead of failing the upload.
    """
    entry = PhotoPoint(**metadata.model_dump())
    classification = classify_photo(image_bytes)
    entry.classification = classification
    entry.intervention_verified = (
        classification is not None
        and classification.category == "conservation_structure"
    )

    sample_date = metadata.captured_at.date() if metadata.captured_at else date.today()
    entry.satellite = sample_indices_at_point(
        metadata.lat, metadata.lon, sample_date
    )
    return entry


@router.get("/photos/narrative")
async def photos_narrative(watershed_id: str | None = None) -> dict:
    """Written field report over the current photo set (Groq; template fallback).

    Aggregates verified statistics from the stored photo index and asks the
    narrative model to summarise them — the prompt carries ONLY measured
    numbers, and any provider failure degrades to a deterministic template
    with ``generated=false`` so the endpoint always answers 200.
    """
    photos = photo_store.list_photos(watershed_id)
    payload = build_narrative(photos)
    payload["photo_count"] = len(photos)
    return payload


def _suffix_for(content_type: str | None, filename: str) -> str:
    """Pick a storage suffix from the declared content type, then filename."""
    by_type = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/heic": ".heic",
        "image/heif": ".heif",
    }
    if content_type in by_type:
        return by_type[content_type]
    lower = filename.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"):
        if lower.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return ".jpg"


@router.get("/photos")
def get_photos(watershed_id: str | None = None) -> list[dict]:
    """Indexed field photos, newest first; filter by ``watershed_id``."""
    return photo_store.list_photos(watershed_id)


@router.get("/photos/{photo_id}/file")
def get_photo_file(photo_id: str) -> FileResponse:
    """The stored photo bytes (for <img src> markers on the map)."""
    path_iter = photo_store.PHOTOS_DIR.glob(f"{photo_id}.*")
    for candidate in sorted(path_iter):
        if candidate.suffix in {".tmp", ".json"}:
            continue
        media_type = {
            ".jpg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".heic": "image/heic",
            ".heif": "image/heif",
        }.get(candidate.suffix, "application/octet-stream")
        return FileResponse(candidate, media_type=media_type)
    raise HTTPException(status_code=404, detail={"error": f"Photo '{photo_id}' not found."})
