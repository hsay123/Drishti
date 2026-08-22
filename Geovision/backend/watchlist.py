"""Priority watchlist: ranks already-analyzed results (Phase 18c).

No new live analyses are run here — the watchlist ranks results the app has
already produced: the three cached validated presets (read from the on-disk
cache) plus any custom-AOI results run during this server session (a simple
in-memory list, reset on process restart).

Priority is a direct relabel of the existing severity bucket
(severe → High, moderate → Medium, mild → Low) — deliberately NOT a new
independent scoring model. Sorting is High → Medium → Low, then by
``affected_pct`` descending within a priority band.
"""

from __future__ import annotations

from backend import cache, messages
from backend.models.schemas import AnalyzeResponse

#: Priority = relabeled severity (honest, no new model).
PRIORITY_BY_SEVERITY = {"severe": "High", "moderate": "Medium", "mild": "Low"}
_PRIORITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}

#: Custom-AOI results produced during this session (full enriched response dicts).
_session_custom: list[dict] = []


def remember(enriched: AnalyzeResponse) -> None:
    """Record a completed custom-AOI result for the session watchlist.

    Deduplicates by (aoi_bounds, before, after, mode) so re-running the same
    custom AOI updates its entry instead of stacking duplicates.
    """
    entry = enriched.model_dump()
    key = (
        tuple(entry.get("aoi_bounds") or ()),
        str(entry.get("before_date")),
        str(entry.get("after_date")),
        entry.get("mode"),
    )
    for i, existing in enumerate(_session_custom):
        existing_key = (
            tuple(existing.get("aoi_bounds") or ()),
            str(existing.get("before_date")),
            str(existing.get("after_date")),
            existing.get("mode"),
        )
        if existing_key == key:
            _session_custom[i] = entry
            return
    _session_custom.append(entry)


def build_watchlist() -> list[dict]:
    """Ranked list of already-analyzed results (presets + session customs)."""
    entries: list[dict] = []
    for preset_id in cache.PRESETS:
        payload = cache.load_cached(preset_id)
        if not payload:
            continue
        try:
            resp = AnalyzeResponse(**payload)
        except Exception:  # noqa: BLE001 — a stale cache entry must not break the list
            continue
        enriched = messages.enrich_response(preset_id, resp)
        entry = enriched.model_dump()
        entry["preset_id"] = preset_id
        entry["cached"] = True  # served from the on-disk preset cache
        entries.append(entry)
    for entry in _session_custom:
        e = dict(entry)
        e["preset_id"] = None
        entries.append(e)

    for entry in entries:
        entry["priority"] = PRIORITY_BY_SEVERITY.get(
            entry.get("severity", "mild"), "Low"
        )

    entries.sort(
        key=lambda e: (
            _PRIORITY_ORDER.get(e["priority"], 9),
            -(e.get("affected_pct") or 0.0),
        )
    )
    return entries
