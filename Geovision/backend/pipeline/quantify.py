"""Quantify the change mask: affected area (ha), % of AOI, and severity."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Severity buckets as % of AOI affected.
MILD_MAX = 5.0
SEVERE_MIN = 20.0

#: Fixed per-mode thresholds for the *independent* per-date coverage metric
#: (Phase 18a). Each entry maps mode -> (comparison operator, threshold); the
#: signal is "flagged" when the raw index value crosses the threshold:
#:   ndwi  water coverage:    index >  0.0   (McFeeters NDWI, open water strongly positive)
#:   ndvi  healthy vegetation: index >  0.3   (moderate-to-dense green vegetation)
#:   nbr   burn signature:     index < -0.1   (burn scars are strongly negative)
#: These are deliberately simple, fixed thresholds — a supporting explainability
#: metric, not a second change detector.
COVERAGE_THRESHOLDS: dict[str, tuple[str, float]] = {
    "ndwi": (">", 0.0),
    "ndvi": (">", 0.3),
    "nbr": ("<", -0.1),
}


class SeverityError(RuntimeError):
    """Raised when an AOI has zero analyzable pixels."""


@dataclass
class QuantifyResult:
    affected_ha: float
    aoi_ha: float
    affected_pct: float
    severity: str
    changed_pixels: int
    total_pixels: int


def severity_from_pct(pct: float) -> str:
    """Bucket an affected-% value into mild / moderate / severe."""
    if pct >= SEVERE_MIN:
        return "severe"
    if pct >= MILD_MAX:
        return "moderate"
    return "mild"


def per_date_coverage(
    index: np.ndarray,
    valid: np.ndarray,
    in_aoi: np.ndarray,
    mode: str,
) -> float:
    """Percent of AOI pixels flagged by a signal at one date (Phase 18a).

    Independent of the diff/Otsu mask: the raw coverage of water / healthy
    vegetation / burn signature at that date, computed over pixels that are
    inside the AOI *and* valid that date (so clouds never dilute it).
    """
    op, threshold = COVERAGE_THRESHOLDS.get(mode, (">", 0.0))
    aoi = in_aoi.astype(bool)
    ok = aoi & valid.astype(bool) & ~np.isnan(index)
    n = int(ok.sum())
    if n == 0:
        return 0.0
    values = index[ok]
    hits = (values > threshold) if op == ">" else (values < threshold)
    return round(100.0 * int(hits.sum()) / n, 2)


def quantify(
    mask: np.ndarray,
    in_aoi: np.ndarray,
    scale_m: float = 10.0,
) -> QuantifyResult:
    """Convert a refined mask into area + severity statistics.

    Each pixel of the aligned UTM grid covers ``scale_m**2`` m², so area math
    is exact. ``in_aoi`` (1/0) restricts counts to the true AOI polygon.
    """
    aoi = in_aoi.astype(bool)
    total_pixels = int(aoi.sum())
    if total_pixels == 0:
        raise SeverityError("No analyzable pixels found inside the AOI.")

    changed = (mask & aoi)
    changed_pixels = int(changed.sum())
    pixel_area_m2 = scale_m * scale_m

    affected_ha = changed_pixels * pixel_area_m2 / 10_000.0
    aoi_ha = total_pixels * pixel_area_m2 / 10_000.0
    affected_pct = 100.0 * changed_pixels / total_pixels

    return QuantifyResult(
        affected_ha=round(affected_ha, 1),
        aoi_ha=round(aoi_ha, 1),
        affected_pct=round(affected_pct, 2),
        severity=severity_from_pct(affected_pct),
        changed_pixels=changed_pixels,
        total_pixels=total_pixels,
    )
