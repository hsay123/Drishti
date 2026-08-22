"""File-based persistence for geo-tagged field photos (watershed module).

Same spirit as ``cache.py``: no database — photos live on disk under
``data/photos/`` and their metadata in a single JSON index file
(``data/photos/index.json``), written atomically (tmp file + replace) so a
crash mid-write can never corrupt the index. Degrades gracefully: the data
directory is created on first use, and every read tolerates a missing or
corrupt index by returning empty results instead of raising.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("hackpreneur")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PHOTOS_DIR = DATA_DIR / "photos"
INDEX_PATH = PHOTOS_DIR / "index.json"

#: Accepted upload content types (anything else is rejected before decode).
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


def save_photo_file(photo_id: str, image_bytes: bytes, suffix: str = ".jpg") -> Path:
    """Write the image bytes to ``data/photos/<id><suffix>`` atomically."""
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    path = photo_path(photo_id, suffix)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(image_bytes)
    tmp.replace(path)
    return path


def photo_path(photo_id: str, suffix: str = ".jpg") -> Path:
    return PHOTOS_DIR / f"{photo_id}{suffix}"


def load_index() -> dict[str, dict]:
    """The full metadata index keyed by photo id; ``{}`` when absent/corrupt."""
    try:
        raw = json.loads(INDEX_PATH.read_text())
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_index(index: dict[str, dict]) -> None:
    """Atomically persist the whole metadata index."""
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(index, default=str))
    tmp.replace(INDEX_PATH)


def upsert(entry: dict) -> None:
    """Insert or update one photo's metadata entry (keyed by its id)."""
    index = load_index()
    index[entry["id"]] = entry
    save_index(index)
    logger.info("photo %s indexed (%s total)", entry["id"], len(index))


def get(photo_id: str) -> dict | None:
    """One photo's metadata entry, or ``None``."""
    return load_index().get(photo_id)


def list_photos(watershed_id: str | None = None) -> list[dict]:
    """All indexed photos, newest upload first; optionally per watershed."""
    entries = list(load_index().values())
    if watershed_id is not None:
        entries = [e for e in entries if e.get("watershed_id") == watershed_id]
    return sorted(entries, key=lambda e: str(e.get("uploaded_at") or ""), reverse=True)


def read_image_bytes(photo_id: str) -> bytes | None:
    """The stored image bytes for a photo id, or ``None`` when missing."""
    if not PHOTOS_DIR.exists():
        return None
    for candidate in sorted(PHOTOS_DIR.glob(f"{photo_id}.*")):
        if candidate.suffix in {".tmp", ".json"}:
            continue
        return candidate.read_bytes()
    return None
