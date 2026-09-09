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
from backend.pipeline.drishti_import import (
    MalformedImportError,
    not_analyzed_classification,
    parse_drishti_csv,
    row_to_metadata,
    sample_date_for,
    validate_row,
)
from backend.pipeline.health_score import compute_health_scores
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


#: DRISHTI-hierarchy fields accepted as optional multipart form entries on
#: /photos/upload. Nullable in the schema — a casual upload fills none of them.
DRISHTI_CONTEXT_FIELDS = (
    "state",
    "district",
    "block",
    "gram_panchayat",
    "micro_watershed_id",
    "project_year",
)


def _apply_drishti_fields(metadata: PhotoPoint, form: dict[str, str | None]) -> PhotoPoint:
    """Attach any non-empty optional DRISHTI-hierarchy fields to fresh metadata."""
    updates = {k: v for k in DRISHTI_CONTEXT_FIELDS if (v := form.get(k))}
    return metadata.model_copy(update=updates) if updates else metadata


@router.post("/photos/upload", response_model=PhotoPoint)
async def upload_photo(
    file: UploadFile = File(...),
    aoi: str = Form(...),
    watershed_id: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    auto_classify: bool = Form(default=True),
    state: str | None = Form(default=None),
    district: str | None = Form(default=None),
    block: str | None = Form(default=None),
    gram_panchayat: str | None = Form(default=None),
    micro_watershed_id: str | None = Form(default=None),
    project_year: str | None = Form(default=None),
) -> PhotoPoint:
    """Ingest one geo-tagged field photo and index it for the watershed map.

    The photo must carry GPS EXIF inside the given AOI; it is stored on disk
    under ``data/photos/`` and its metadata appended to the JSON index. When
    ``auto_classify`` is set (default), the Groq vision model labels the photo
    (degrading to ``unclear`` when unconfigured) and the point is fused with
    SRISHTI-DRISHTI-equivalent satellite index values at its coordinates —
    neither step can fail the upload.

    The optional state/district/block/… form fields mirror DRISHTI's project
    hierarchy; supplying none of them is fully supported.
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
        if auto_classify:
            entry = _enriched_point(metadata, image_bytes)
        else:
            entry = PhotoPoint(**metadata.model_dump())
        entry = _apply_drishti_fields(
            entry,
            {
                "state": state,
                "district": district,
                "block": block,
                "gram_panchayat": gram_panchayat,
                "micro_watershed_id": micro_watershed_id,
                "project_year": project_year,
            },
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


@router.post("/photos/import")
async def import_photos(
    file: UploadFile = File(...),
    aoi: str | None = Form(default=None),
) -> dict:
    """Bulk import of a DRISHTI-style structured CSV export.

    Each row is mapped onto the same schema as an individually-uploaded photo
    (``pipeline/drishti_import.py``) and indexed for the watershed map, with
    the same best-effort satellite fusion. Rows carry no image bytes, so they
    are marked ``classification="not_analyzed"`` — never presented as model
    output — and any pre-existing DRISHTI classification text is preserved in
    ``notes``. Bad rows are skipped individually with a reason; only a
    fundamentally unparseable file fails (400). When ``aoi`` is supplied,
    rows outside it are skipped rather than rejecting the batch.

    Coordinates and attributes are whatever the uploaded file contains; the
    sample fixture shipped with the repo is clearly-labelled demo data.
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")  # tolerate Excel's BOM
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "Import file must be UTF-8 text (CSV).", "detail": str(exc)},
        ) from exc

    try:
        rows = parse_drishti_csv(text)
        parsed_aoi = _parse_aoi(aoi) if aoi else None
    except MalformedImportError as exc:
        logger.warning("import rejected: %s", exc)
        raise HTTPException(
            status_code=400, detail={"error": str(exc), "detail": "Malformed CSV import."}
        ) from exc
    except InvalidAoiError as exc:
        _raise_http(exc)

    imported: list[PhotoPoint] = []
    skipped: list[dict] = []
    total_rows = len(rows)
    for row in rows:
        validated, reason = validate_row(row)
        if validated is None:
            skipped.append({"row_number": row["row_number"], "reason": reason})
            continue
        if parsed_aoi is not None:
            try:
                ensure_in_aoi(validated["lat"], validated["lon"], parsed_aoi, validated["filename"])
            except PhotoOutsideAoiError as exc:
                skipped.append(
                    {
                        "row_number": validated["row_number"],
                        "photo_ref": validated["filename"],
                        "reason": str(exc),
                    }
                )
                continue

        metadata = row_to_metadata(validated, photo_id=uuid.uuid4().hex[:12])
        entry = PhotoPoint(**metadata.model_dump())
        entry.classification = not_analyzed_classification()
        entry.satellite = sample_indices_at_point(
            metadata.lat, metadata.lon, sample_date_for(metadata.captured_at)
        )
        photo_store.upsert(entry.model_dump())
        imported.append(entry)

    logger.info(
        "DRISHTI-style import: %d imported, %d skipped (%s)",
        len(imported), len(skipped), file.filename or "csv",
    )
    return {
        "source": file.filename or "uploaded CSV",
        "data_note": (
            "Rows imported verbatim from the uploaded DRISHTI-style CSV "
            "(metadata only; not analyzed)."
        ),
        "total_rows": total_rows,
        "imported_count": len(imported),
        "skipped_count": len(skipped),
        "imported": [p.model_dump() for p in imported],
        "skipped": skipped,
    }


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


@router.get("/watersheds/health")
def watersheds_health() -> dict:
    """0-100 health score per priority watershed (see ``pipeline/health_score.py``).

    Aggregates satellite signal coverage from each already-analyzed /analyze
    result plus geo-tagged field photos that fall inside that watershed's AOI.
    Runs no new analyses and makes no network calls. Sub-scores that cannot be
    computed are ``None``; an ``overall`` of ``None`` means "no data" (the UI
    must not render it as a failing 0).
    """
    from backend import watchlist

    entries = watchlist.build_watchlist()
    photos = photo_store.list_photos()
    return {
        "watersheds": compute_health_scores(entries, photos),
        "photo_count": len(photos),
    }


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
