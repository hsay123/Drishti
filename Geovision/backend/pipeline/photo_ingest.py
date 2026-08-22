"""Geo-tagged field-photo ingestion: EXIF parsing + AOI containment checks.

Pure functions only — no filesystem or network access. ``extract_photo_metadata``
reads GPS coordinates and the capture timestamp from an image's EXIF metadata
(Pillow parses the GPS IFD natively; no extra dependency) and returns a
``PhotoMetadata``. Missing/corrupt EXIF raises an explicit typed error so the
API layer can return a clean message instead of crashing.

Watershed-module data source note: photos are fused against SRISHTI-DRISHTI-
equivalent 30m satellite data (SRISHTI-DRISHTI API integration pending public
access); see ``photo_classifier.py`` / ``thematic_layers.py``.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime
from typing import Any

from PIL import Image, UnidentifiedImageError

from backend.models.photo import PhotoMetadata

logger = logging.getLogger("hackpreneur")

#: Max accepted upload size (bytes) — guards against accidental huge uploads.
MAX_PHOTO_BYTES = 15 * 1024 * 1024

#: EXIF GPS sub-IFD marker and the tag ids inside it (numeric form; Pillow
#: exposes the GPS IFD raw rather than via named constants).
GPS_IFD_ID = 0x8825
EXIF_IFD_ID = 0x8769
TAG_GPS_LAT_REF = 1
TAG_GPS_LAT = 2
TAG_GPS_LON_REF = 3
TAG_GPS_LON = 4
TAG_GPS_ALT = 6

#: EXIF capture-time tags: DateTimeOriginal lives in the Exif sub-IFD,
#: DateTime in the base IFD.
TAG_DATETIME_ORIGINAL = 36867
TAG_DATETIME = 306

_EXIF_DT_FORMAT = "%Y:%m:%d %H:%M:%S"


class PhotoError(RuntimeError):
    """Base class for photo-ingestion failures (mapped to clean HTTP errors)."""


class CorruptImageError(PhotoError):
    """The uploaded bytes are not a decodable image."""


class MissingGPSExifError(PhotoError):
    """The image has no usable GPS coordinates in its EXIF."""


class InvalidAoiError(PhotoError):
    """The containment-check AOI is not a supported bbox/GeoJSON shape."""


class PhotoOutsideAoiError(PhotoError):
    """The photo's GPS point lies outside the requested AOI."""


def extract_photo_metadata(
    image_bytes: bytes,
    filename: str,
    photo_id: str,
    watershed_id: str | None = None,
    notes: str | None = None,
) -> PhotoMetadata:
    """Parse one uploaded image into a :class:`PhotoMetadata`.

    Raises ``CorruptImageError`` for undecodable/oversized bytes and
    ``MissingGPSExifError`` when no GPS latitude/longitude is present. All
    other EXIF fields are optional and degrade to ``None``.
    """
    if len(image_bytes) == 0:
        raise CorruptImageError("Uploaded file is empty.")
    if len(image_bytes) > MAX_PHOTO_BYTES:
        raise CorruptImageError(
            f"Uploaded file is {len(image_bytes) / 1e6:.1f} MB — above the "
            f"{MAX_PHOTO_BYTES / 1e6:.0f} MB limit."
        )

    img, exif = _decode(image_bytes)
    gps = _gps_ifd(exif)
    lat = _dms_to_decimal(gps.get(TAG_GPS_LAT), gps.get(TAG_GPS_LAT_REF))
    lon = _dms_to_decimal(gps.get(TAG_GPS_LON), gps.get(TAG_GPS_LON_REF))
    if lat is None or lon is None:
        raise MissingGPSExifError(
            f"'{filename}' has no usable GPS EXIF. Geo-tagged field photos are "
            "required — enable location in the camera app and re-shoot."
        )
    if abs(lat) > 90 or abs(lon) > 180:
        raise MissingGPSExifError(
            f"'{filename}' has out-of-range GPS coordinates ({lat}, {lon})."
        )

    return PhotoMetadata(
        id=photo_id,
        filename=filename,
        lat=round(lat, 7),
        lon=round(lon, 7),
        altitude_m=_rational_float(gps.get(TAG_GPS_ALT)),
        captured_at=_parse_datetime(exif),
        uploaded_at=datetime.now().astimezone(),
        width_px=img.size[0],
        height_px=img.size[1],
        filesize_bytes=len(image_bytes),
        notes=notes,
        watershed_id=watershed_id,
    )


def point_in_aoi(lat: float, lon: float, aoi: dict[str, Any] | list[float]) -> bool:
    """Return True when ``(lat, lon)`` falls inside ``aoi``.

    Supports the same two AOI shapes as ``POST /analyze``: a
    ``[west, south, east, north]`` bounding box or a GeoJSON Polygon /
    MultiPolygon geometry (ray casting per ring; holes are respected).
    Raises ``InvalidAoiError`` for anything else.
    """
    if isinstance(aoi, list):
        return _point_in_bbox(lat, lon, aoi)
    if isinstance(aoi, dict):
        geom = aoi.get("geometry", aoi) if aoi.get("type") == "Feature" else aoi
        gtype = geom.get("type")
        if gtype == "Polygon":
            return _point_in_polygon(lat, lon, geom.get("coordinates"))
        if gtype == "MultiPolygon":
            polys = geom.get("coordinates") or []
            return any(_point_in_polygon(lat, lon, poly) for poly in polys)
    raise InvalidAoiError(
        "aoi must be a [west, south, east, north] bbox or a GeoJSON "
        "Polygon/MultiPolygon geometry."
    )


def ensure_in_aoi(
    lat: float, lon: float, aoi: dict[str, Any] | list[float], filename: str
) -> None:
    """Raise a typed error when the photo point lies outside the AOI."""
    if not point_in_aoi(lat, lon, aoi):
        raise PhotoOutsideAoiError(
            f"'{filename}' ({lat:.5f}, {lon:.5f}) lies outside the selected AOI "
            "— field photos must be geo-tagged inside the monitored area."
        )


# --------------------------------------------------------------------------
# internals
# --------------------------------------------------------------------------


def _decode(image_bytes: bytes) -> tuple[Image.Image, Any]:
    """Decode the image once; returns the PIL image + its EXIF object."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        return img, img.getexif()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise CorruptImageError(f"Could not decode uploaded image: {exc}") from exc


def _gps_ifd(exif: Any) -> dict[int, Any]:
    """Extract the raw GPS sub-IFD (empty dict when absent or malformed)."""
    try:
        return dict(exif.get_ifd(GPS_IFD_ID))
    except (AttributeError, KeyError, TypeError):
        return {}


def _dms_to_decimal(dms: Any, ref: Any) -> float | None:
    """Convert EXIF degrees/minutes/seconds rationals + hemisphere to signed dd."""
    values = _rational_floats(dms)
    if values is None or len(values) != 3:
        return None
    deg, minutes, seconds = values
    decimal = deg + minutes / 60.0 + seconds / 3600.0
    ref_str = str(ref).strip().upper()
    if ref_str in {"S", "W"}:
        decimal = -decimal
    elif ref_str not in {"N", "E", ""}:
        return None
    return decimal


def _rational_floats(value: Any) -> list[float] | None:
    """EXIF rationals (tuples of IFDRational, or a bare number) as floats."""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return [float(value)]
        return [float(v) for v in tuple(value)]
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _rational_float(value: Any) -> float | None:
    """Single EXIF rational (e.g. altitude) as float; ``None`` when unusable."""
    values = _rational_floats(value)
    return values[0] if values else None


def _parse_datetime(exif: Any) -> datetime | None:
    """DateTimeOriginal (capture time) falling back to the file DateTime tag."""
    for getter in (
        lambda: exif.get_ifd(EXIF_IFD_ID).get(TAG_DATETIME_ORIGINAL),
        lambda: exif.get(TAG_DATETIME),
    ):
        try:
            raw = getter()
        except (AttributeError, KeyError, TypeError):
            raw = None
        if isinstance(raw, str) and raw.strip():
            try:
                return datetime.strptime(raw.strip(), _EXIF_DT_FORMAT)
            except ValueError:
                continue
    return None


def _point_in_bbox(lat: float, lon: float, aoi: list[float]) -> bool:
    """Inclusive containment in a ``[west, south, east, north]`` box."""
    if len(aoi) != 4 or not all(isinstance(v, (int, float)) for v in aoi):
        raise InvalidAoiError("bbox aoi must be [west, south, east, north].")
    w, s, e, n = aoi
    if w >= e or s >= n:
        raise InvalidAoiError(f"Malformed bbox {aoi}: expected west<east, south<north.")
    return s <= lat <= n and w <= lon <= e


def _point_in_polygon(lat: float, lon: float, rings: Any) -> bool:
    """Even-odd ray casting over a GeoJSON Polygon's rings (holes respected)."""
    if not rings or not isinstance(rings, list):
        raise InvalidAoiError("GeoJSON Polygon is missing its coordinate rings.")
    exterior = rings[0]
    if not _ring_contains(exterior, lat, lon):
        return False
    for hole in rings[1:]:
        if _ring_contains(hole, lat, lon):
            return False
    return True


def _ring_contains(ring: Any, lat: float, lon: float) -> bool:
    """Classic even-odd ray-cast test of point-in-ring ([lon, lat] vertices)."""
    if not ring or len(ring) < 4:
        raise InvalidAoiError("GeoJSON polygon ring needs at least 4 vertices.")
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = _vertex(ring[i])
        xj, yj = _vertex(ring[j])
        crosses = (yi > lat) != (yj > lat)
        if crosses and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _vertex(pt: Any) -> tuple[float, float]:
    try:
        x, y = pt[0], pt[1]
        return float(x), float(y)
    except (TypeError, ValueError, IndexError) as exc:
        raise InvalidAoiError(f"Malformed GeoJSON vertex: {pt!r}") from exc
