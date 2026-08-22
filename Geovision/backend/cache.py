"""On-disk result cache for the three validated demo presets.

Serving a preset from cache makes the demo near-instant and immune to live GEE /
venue-network hiccups. A cached response is the full ``POST /analyze`` JSON
(including base64 thumbnails) saved to ``backend/cache/<preset_id>.json``.

Cache writes happen automatically after any successful *live* run that exactly
matches a known preset, so the cache regenerates itself whenever someone clicks
"Run live" or the pipeline changes and a fresh run is issued.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from backend.models.schemas import AnalyzeRequest

logger = logging.getLogger("hackpreneur")

CACHE_DIR = Path(__file__).resolve().parent / "cache"

#: The three validated presets. Must mirror the frontend
#: ``frontend/src/components/AOIPicker.jsx`` PRESETS exactly (incl. scale/window).
PRESETS: dict[str, dict] = {
    "kishanganj-flood-2017": {
        "aoi": [87.75, 25.75, 88.15, 26.15],
        "before_date": "2017-01-15",
        "after_date": "2017-11-01",
        "mode": "ndwi",
        "window_days": 6,
        "scale": 20,
    },
    "po-valley-drought-2022": {
        "aoi": [10.9, 44.85, 11.15, 45.1],
        "before_date": "2021-08-15",
        "after_date": "2022-08-15",
        "mode": "ndvi",
        "window_days": 6,
        "scale": 20,
    },
    "nsw-bushfires-2019": {
        "aoi": [150.55, -33.25, 150.85, -33.05],
        "before_date": "2019-09-15",
        "after_date": "2020-02-15",
        "mode": "nbr",
        "window_days": 6,
        "scale": 20,
    },
}

_bbox_tol = 1e-9


def preset_id_for(request: AnalyzeRequest) -> str | None:
    """Return the preset id whose spec exactly matches ``request``, else ``None``."""
    if not isinstance(request.aoi, list) or len(request.aoi) != 4:
        return None
    for preset_id, spec in PRESETS.items():
        if (
            _bbox_equals(request.aoi, spec["aoi"])
            and request.before_date == date.fromisoformat(spec["before_date"])
            and request.after_date == date.fromisoformat(spec["after_date"])
            and request.mode == spec["mode"]
            and request.window_days == spec["window_days"]
            and request.scale == spec["scale"]
        ):
            return preset_id
    return None


def _bbox_equals(a: list[float], b: list[float]) -> bool:
    return len(a) == len(b) and all(abs(x - y) <= _bbox_tol for x, y in zip(a, b))


def _path(preset_id: str) -> Path:
    return CACHE_DIR / f"{preset_id}.json"


def load_cached(preset_id: str) -> dict | None:
    """Return the cached response payload for ``preset_id``, or ``None``."""
    try:
        return json.loads(_path(preset_id).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def save_cached(preset_id: str, payload: dict) -> None:
    """Atomically persist ``payload`` to the preset's cache file."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _path(preset_id).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, default=str))
    tmp.replace(_path(preset_id))
    logger.info("cached result written for preset %s", preset_id)
