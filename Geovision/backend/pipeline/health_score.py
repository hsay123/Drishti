"""Watershed health score (0-100) per priority watershed.

Transparent, rule-based aggregation over signals the pipeline ALREADY measures
— deliberately NOT a new scoring model. For each /analyze watchlist entry it
combines up to three sub-scores, each nullable:

* ``vegetation`` — the analyze response's own healthy-vegetation coverage
  (``after_coverage_pct`` when the analysis mode is NDVI) or, for flood/burn
  watersheds, the mean NDVI of geo-tagged field photos in the region, mapped
  from [-1, 1] to [0, 100].
* ``water`` — the response's water coverage (``after_coverage_pct`` when the
  mode is NDWI) or the mean NDWI of regional field photos, mapped likewise.
* ``interventions`` — share of field photos in the region with a verified
  conservation intervention (``intervention_verified``), in percent.

The overall score is the weighted mean of the present sub-scores
(vegetation 0.4 / water 0.3 / interventions 0.3). Missing data stays ``None``
— never fabricated as 0 — so a watershed with no field photos and no matching
satellite signal reports a distinct "no data" overall instead of a false
"failing" score.
"""

from __future__ import annotations

from typing import Any

#: Sub-score weights for the overall score (sum == 1.0).
WEIGHTS: dict[str, float] = {"vegetation": 0.4, "water": 0.3, "interventions": 0.3}


def _coverage(entry: dict, mode: str) -> int | None:
    """The analyze response's per-date signal coverage, as a 0-100 int."""
    for key in ("after_coverage_pct", "before_coverage_pct"):
        value = entry.get(key)
        if isinstance(value, (int, float)):
            return round(value)
    return None


def _scaled_index(values: list[float | None]) -> int | None:
    """Mean of normalized indices (already [0, 1]) as a 0-100 int, or None."""
    present = [v for v in values if isinstance(v, (int, float))]
    if not present:
        return None
    return round(sum(present) / len(present) * 100)


def _photos_in_region(photos: list[dict], aoi: Any) -> list[dict]:
    """Field photos whose coordinates fall inside the watchlist entry's AOI."""
    from backend.pipeline.photo_ingest import point_in_aoi

    try:
        return [
            p
            for p in photos
            if isinstance(p.get("lat"), (int, float))
            and isinstance(p.get("lon"), (int, float))
            and point_in_aoi(float(p["lat"]), float(p["lon"]), aoi)
        ]
    except Exception:  # noqa: BLE001 — a malformed AOI must not break scoring
        return []


def _sub_scores(entry: dict, region_photos: list[dict]) -> dict[str, Any]:
    """Per-sub-score value, source label, and the weighting meta."""
    mode = (entry.get("mode") or "").lower()
    aoi = entry.get("aoi_bounds")

    def mean_index(band: str) -> int | None:
        values: list[float | None] = []
        for p in region_photos:
            sat = p.get("satellite") if isinstance(p.get("satellite"), dict) else {}
            values.append(sat.get(band))
        return _scaled_index(values)

    sat_veg = _coverage(entry, mode) if mode == "ndvi" else None
    sat_water = _coverage(entry, mode) if mode == "ndwi" else None
    photo_veg = None if aoi is None else mean_index("ndvi")
    photo_water = None if aoi is None else mean_index("ndwi")
    interventions = None
    if region_photos:
        verified = sum(1 for p in region_photos if bool(p.get("intervention_verified")))
        interventions = round(verified / len(region_photos) * 100)

    vegetation = sat_veg if sat_veg is not None else photo_veg
    water = sat_water if sat_water is not None else photo_water

    return {
        "vegetation": vegetation,
        "water": water,
        "interventions": interventions,
        "vegetation_source": (
            "satellite:ndvi coverage" if sat_veg is not None
            else "field:mean ndvi" if photo_veg is not None else None
        ),
        "water_source": (
            "satellite:ndwi coverage" if sat_water is not None
            else "field:mean ndwi" if photo_water is not None else None
        ),
        "interventions_source": ("field:verified share" if region_photos else None),
    }


def _overall(sub: dict) -> int | None:
    """Weighted mean of non-null sub-scores; ``None`` when none are present."""
    weighted_sum = 0.0
    weight_sum = 0.0
    for key, weight in WEIGHTS.items():
        value = sub[key]
        if isinstance(value, (int, float)):
            weighted_sum += value * weight
            weight_sum += weight
    if weight_sum == 0:
        return None
    return round(weighted_sum / weight_sum)


def compute_health_scores(watchlist: list[dict], photos: list[dict]) -> list[dict]:
    """Health score for every priority watershed (``None`` overall = no data)."""
    results: list[dict] = []
    for entry in watchlist:
        region_photos = (
            _photos_in_region(photos, entry.get("aoi_bounds"))
            if entry.get("aoi_bounds")
            else []
        )
        sub = _sub_scores(entry, region_photos)
        results.append(
            {
                "preset_id": entry.get("preset_id"),
                "location_name": entry.get("location_name"),
                "mode": entry.get("mode"),
                "priority": entry.get("priority"),
                "affected_pct": entry.get("affected_pct"),
                "aoi_bounds": entry.get("aoi_bounds"),
                "photo_count": len(region_photos),
                "verified_intervention_count": sum(
                    1 for p in region_photos if bool(p.get("intervention_verified"))
                ),
                "overall": _overall(sub),
                "sub_scores": sub,
            }
        )
    return results