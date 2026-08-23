"""DRISHTI-style structured import: CSV rows → photo points.

Parses a table shaped like a plausible DRISHTI field-data export (photo
reference, latitude, longitude, timestamp, plus the SRISHTI-DRISHTI project
hierarchy added to ``PhotoMetadata`` in the alignment task) and maps each row
onto the existing photo schema so it can flow through the same pipeline as an
individually-uploaded photo.

Design notes:
- Pure functions only — no filesystem, network, or Groq access here. The
  router owns storage, AOI checks, satellite fusion, and HTTP errors.
- Per-row tolerance: one malformed row is skipped with a reason; only a
  fundamentally unparseable file (no header / none of the required columns /
  csv syntax error) raises :class:`MalformedImportError`.
- Honesty: rows carry no image bytes, so nothing here classifies. Imported
  points are marked ``not_analyzed`` (see ``not_analyzed_classification``) and
  any pre-existing DRISHTI classification text is preserved verbatim in
  ``notes`` — never presented as our model's verdict.
- Coordinates/timestamps/hierarchy values in any such file are whatever the
  uploader supplied; sample fixtures shipped for tests use clearly fake data.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime

from backend.models.photo import PhotoClassification, PhotoMetadata

#: Largest accepted import (bounds per-row satellite-fusion work downstream).
MAX_IMPORT_ROWS = 250

#: Header spellings accepted for each logical column (matched lowercased).
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "photo_ref": ("photo_ref", "photo_filename", "filename", "photo_reference"),
    "latitude": ("latitude", "lat", "y"),
    "longitude": ("longitude", "lon", "lng", "x"),
    "timestamp": ("timestamp", "captured_at", "datetime", "capture_time"),
    "state": ("state",),
    "district": ("district",),
    "block": ("block",),
    "gram_panchayat": ("gram_panchayat", "gp"),
    "micro_watershed_id": ("micro_watershed_id", "mws_code", "mws_id"),
    "project_year": ("project_year", "year"),
    "notes": ("notes", "remarks"),
    "drishti_classification": ("classification", "drishti_classification"),
}

#: Columns that must resolve for the file to parse at all.
REQUIRED_COLUMNS = ("photo_ref", "latitude", "longitude")

_TIMESTAMP_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S",
    "%Y:%m:%d %H:%M:%S",  # EXIF-style, as DRISHTI exports sometimes carry
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y",
)


class MalformedImportError(ValueError):
    """The CSV file itself is unusable (bad header/syntax) — abort the batch."""


class ImportRow(dict):
    """One validated CSV row mapped onto ``PhotoMetadata`` fields.

    A plain dict subclass keyed exactly like :class:`PhotoMetadata` kwargs,
    plus ``row_number`` (1-based, header excluded) for reporting.
    """


def parse_drishti_csv(text: str) -> list[ImportRow]:
    """Parse CSV text into validated row dicts; raise on unusable files.

    Raises ``MalformedImportError`` when the text has no readable header or is
    missing every required column (photo reference / latitude / longitude), or
    when the row count exceeds ``MAX_IMPORT_ROWS``. Individual bad rows are
    *not* raised here — they land in the result of ``validate_row``.
    """
    try:
        reader = csv.DictReader(io.StringIO(text))
        raw_rows = list(reader)
    except csv.Error as exc:
        raise MalformedImportError(f"CSV is not parseable: {exc}") from exc

    fieldnames = [f.strip() for f in (reader.fieldnames or []) if f and f.strip()]
    if not fieldnames:
        raise MalformedImportError("CSV has no readable header row.")

    columns = _resolve_columns(fieldnames)
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing:
        raise MalformedImportError(
            "CSV is missing required column(s): "
            + ", ".join(f"{c} (accepted: {', '.join(COLUMN_ALIASES[c])})" for c in missing)
            + "."
        )
    if len(raw_rows) > MAX_IMPORT_ROWS:
        raise MalformedImportError(
            f"CSV has {len(raw_rows)} rows — above the {MAX_IMPORT_ROWS}-row import limit."
        )

    rows: list[ImportRow] = []
    for i, raw in enumerate(raw_rows, start=2):  # spreadsheet-style numbering incl. header
        clean = {
            (k or "").strip(): (v.strip() if isinstance(v, str) else v)
            for k, v in raw.items()
        }
        rows.append(_map_row(clean, columns, row_number=i))
    return rows


def validate_row(row: ImportRow) -> tuple[ImportRow | None, str | None]:
    """Return ``(row, None)`` when usable, else ``(None, reason)``.

    Checks presence/range of coordinates; unparseable timestamps degrade to
    ``captured_at=None`` rather than rejecting the row (the position still
    matters, the capture time doesn't).
    """
    ref = (row.get("photo_ref") or "").strip()
    if not ref:
        return None, f"row {row['row_number']}: missing photo reference."

    lat = row.get("_lat")
    lon = row.get("_lon")
    if lat is None or lon is None:
        return None, (
            f"row {row['row_number']}: '{ref}' has missing or non-numeric "
            "latitude/longitude."
        )
    if abs(lat) > 90 or abs(lon) > 180:
        return None, (
            f"row {row['row_number']}: '{ref}' has out-of-range coordinates "
            f"({lat}, {lon})."
        )

    out = ImportRow(row)
    out["filename"] = ref
    out["lat"] = round(lat, 7)
    out["lon"] = round(lon, 7)
    return out, None


def row_to_metadata(row: ImportRow, photo_id: str) -> PhotoMetadata:
    """Map one validated row onto :class:`PhotoMetadata` (id/uploaded_at fresh)."""
    notes_parts = [
        n for n in (row.get("notes"), _drishti_class_note(row.get("drishti_classification"))) if n
    ]
    return PhotoMetadata(
        id=photo_id,
        filename=row["filename"],
        lat=row["lat"],
        lon=row["lon"],
        captured_at=_parse_timestamp(row.get("timestamp")),
        uploaded_at=datetime.now().astimezone(),
        # No image bytes accompany a metadata-only import row.
        width_px=None,
        height_px=None,
        filesize_bytes=None,
        notes="; ".join(notes_parts) if notes_parts else None,
        state=row.get("state") or None,
        district=row.get("district") or None,
        block=row.get("block") or None,
        gram_panchayat=row.get("gram_panchayat") or None,
        micro_watershed_id=row.get("micro_watershed_id") or None,
        project_year=row.get("project_year") or None,
    )


def not_analyzed_classification() -> PhotoClassification:
    """Honest marker for imported metadata-only points (never model output)."""
    return PhotoClassification(
        category="not_analyzed",
        confidence=0.0,
        provider="csv_import",
        note=(
            "Imported from a DRISHTI-style CSV without image data — this "
            "point has not been run through photo classification."
        ),
    )


# --------------------------------------------------------------------------
# internals
# --------------------------------------------------------------------------


def _resolve_columns(fieldnames: list[str]) -> dict[str, str]:
    """Map logical column → actual header spelling (first alias wins)."""
    lowered = {name.lower(): name for name in fieldnames}
    resolved: dict[str, str] = {}
    for logical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                resolved[logical] = lowered[alias]
                break
    return resolved


def _map_row(raw: dict, columns: dict[str, str], row_number: int) -> ImportRow:
    """Build a row dict with logical keys; keep unparsed coords under ``_lat``/``_lon``."""
    def get(logical: str) -> str | None:
        actual = columns.get(logical)
        if actual is None:
            return None
        value = raw.get(actual)
        return value if isinstance(value, str) else None

    row = ImportRow(row_number=row_number)
    for logical in COLUMN_ALIASES:
        row[logical] = get(logical)
    row["_lat"] = _to_float(get("latitude"))
    row["_lon"] = _to_float(get("longitude"))
    return row


def _to_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in _TIMESTAMP_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return parsed.astimezone() if fmt == "%Y-%m-%d" else parsed
    return None  # tolerated upstream: captured_at stays null


def _drishti_class_note(value: str | None) -> str | None:
    """Preserve a pre-existing DRISHTI classification verbatim, clearly labelled."""
    return f"drishti_classification={value}" if value else None


def sample_date_for(captured_at: datetime | None, today: date | None = None) -> date:
    """The date satellite fusion should sample at (same rule as single upload)."""
    return captured_at.date() if captured_at else (today or date.today())


__all__ = [
    "MalformedImportError",
    "MAX_IMPORT_ROWS",
    "ImportRow",
    "parse_drishti_csv",
    "validate_row",
    "row_to_metadata",
    "not_analyzed_classification",
    "sample_date_for",
]
