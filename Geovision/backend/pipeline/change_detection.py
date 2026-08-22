"""Change detection: difference image, adaptive Otsu threshold, binary mask."""

from __future__ import annotations

import numpy as np
from skimage.filters import threshold_otsu

MIN_OTSU_SAMPLES = 100

DIRECTION_DECREASE = "decrease"
DIRECTION_INCREASE = "increase"


def otsu_threshold(change: np.ndarray, direction: str) -> float:
    """Adaptive per-request threshold over the change distribution.

    Applies Otsu's method to the sign-of-interest half of the distribution
    (the damage tail), so the threshold always splits "noise around zero" from
    "real change" in the physically meaningful direction rather than using a
    hard-coded value.

    Raises ``ValueError`` when the sub-distribution is too small to threshold.
    """
    if direction == DIRECTION_DECREASE:
        values = change[change < 0]
    elif direction == DIRECTION_INCREASE:
        values = change[change > 0]
    else:
        raise ValueError(f"Unknown direction: {direction!r}")

    values = values[np.isfinite(values)]
    if values.size < MIN_OTSU_SAMPLES:
        raise ValueError(
            f"Not enough {direction} change samples ({values.size}) to apply Otsu "
            "thresholding — the AOI is likely unchanged."
        )

    threshold = float(threshold_otsu(values))
    # The threshold is on the sign-of-interest side; keep its sign.
    return -abs(threshold) if direction == DIRECTION_DECREASE else abs(threshold)


def apply_threshold(change: np.ndarray, threshold: float, direction: str) -> np.ndarray:
    """Return a boolean array True where change is beyond ``threshold``."""
    change = np.nan_to_num(change, nan=0.0)
    if direction == DIRECTION_DECREASE:
        return change <= threshold
    return change >= threshold
