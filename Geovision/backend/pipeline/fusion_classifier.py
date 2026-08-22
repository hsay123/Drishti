"""Random Forest "fusion" classifier that refines the change mask.

Labels are auto-bootstrapped from *independent* signals so the model does more
than re-learn the threshold it is meant to improve:

* Confident changed  = strong index change AND clear in both periods
                       AND not permanent water.
* Confident unchanged = near-zero index change AND clear AND not water.

The cloud flags and water mask come from SCL / JRC — separate sources from the
index difference — so they can break the circularity of thresholding the very
same distribution the model refines.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import RandomForestClassifier

from backend.pipeline.change_detection import DIRECTION_DECREASE

FEATURE_COLUMNS = [
    "before_index",
    "after_index",
    "change",
    "before_clear_frac",
    "after_clear_frac",
    "before_brightness",
    "after_brightness",
    "water_mask",
]

#: Minimum clear-fraction (per period) required to trust a bootstrap label.
MIN_CLEAR_FRACTION = 0.5
#: "Near zero" unchanged bound as a fraction of |otsu threshold|.
UNCHANGED_REL = 0.35
MAX_TRAIN_SAMPLES = 100_000

#: Honest framing for the fusion classifier's self-fit score (surfaced in the UI
#: tooltip and the /analyze response). It is scored on its own bootstrap labels,
#: NOT held-out ground truth, so it must not be read as model accuracy.
BOOTSTRAP_SCORE_NOTE = (
    "Fusion/refinement layer scored against its own auto-generated bootstrap "
    "labels, not independent ground truth — used to suppress residual "
    "cloud/shadow noise on top of the physics-based index signal, not as a "
    "standalone accuracy metric."
)


@dataclass
class FusionResult:
    mask: np.ndarray  # boolean 2-D refined change mask (same shape as input bands)
    n_labeled: int
    n_changed_label: int
    train_score: float
    n_trees: int


def _build_bootstrap_labels(
    X: np.ndarray,
    otsu_t: float,
    direction: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (labeled_mask, y) where y=1 changed, y=0 unchanged, from rules."""
    before_clear = X[:, 3]
    after_clear = X[:, 4]
    water = X[:, 7]
    change = X[:, 2]

    clear = (before_clear >= MIN_CLEAR_FRACTION) & (after_clear >= MIN_CLEAR_FRACTION)
    not_water = water < 0.5

    threshold_abs = abs(otsu_t)
    if direction == DIRECTION_DECREASE:
        strong_change = change <= otsu_t
    else:
        strong_change = change >= otsu_t

    near_zero = np.abs(change) <= UNCHANGED_REL * threshold_abs

    changed = strong_change & clear & not_water
    unchanged = near_zero & clear & not_water
    labeled = changed | unchanged

    y = np.zeros(X.shape[0], dtype=np.int8)
    y[changed] = 1
    return labeled, y


def _impute_medians(X: np.ndarray) -> np.ndarray:
    """Replace non-finite feature values with per-column medians."""
    X = X.copy()
    for col in range(X.shape[1]):
        finite = np.isfinite(X[:, col])
        if finite.any():
            median = np.median(X[finite, col])
        else:
            median = 0.0
        X[:, col] = np.where(np.isfinite(X[:, col]), X[:, col], median)
    return X


def fusion_predict(
    features: dict[str, np.ndarray],
    otsu_t: float,
    direction: str,
    n_estimators: int = 150,
    random_state: int = 42,
) -> FusionResult:
    """Train an RF on bootstrapped labels and predict a refined 2-D mask.

    ``features`` maps band names to aligned 2-D float arrays (see
    ``features.sample_feature_stack``). Only pixels inside the AOI and valid in
    both periods are scored; everything else is excluded from the mask.
    """
    rows, cols = features["before_index"].shape
    n_feat = len(FEATURE_COLUMNS)

    valid = (
        features["before_valid"].astype(bool)
        & features["after_valid"].astype(bool)
        & features["in_aoi"].astype(bool)
    )

    flat = {name: features[name].reshape(-1) for name in FEATURE_COLUMNS}
    flat["valid"] = valid.reshape(-1)

    mask_flat = np.zeros(rows * cols, dtype=bool)

    X = np.stack([flat[name] for name in FEATURE_COLUMNS], axis=1)
    X = _impute_medians(X)

    labeled, y = _build_bootstrap_labels(X, otsu_t, direction)
    labeled = labeled & flat["valid"]
    y = y & flat["valid"]

    train_idx = np.where(labeled)[0]
    if train_idx.size < 50:
        raise ValueError(
            f"Only {train_idx.size} confidently labeled pixels — cannot train "
            "the fusion classifier. The AOI likely has no detectable change."
        )

    rng = np.random.RandomState(random_state)
    if train_idx.size > MAX_TRAIN_SAMPLES:
        train_idx = rng.choice(train_idx, size=MAX_TRAIN_SAMPLES, replace=False)

    X_train = X[train_idx]
    y_train = y[train_idx]

    clf = RandomForestClassifier(
        n_estimators=n_estimators,
        max_features="sqrt",
        n_jobs=-1,
        random_state=random_state,
    )
    clf.fit(X_train, y_train)
    train_score = float(clf.score(X_train, y_train))

    predict_idx = np.where(flat["valid"])[0]
    y_pred = clf.predict(X[predict_idx])
    mask_flat[predict_idx] = y_pred.astype(bool)

    return FusionResult(
        mask=mask_flat.reshape(rows, cols),
        n_labeled=int(train_idx.size),
        n_changed_label=int(np.sum(y[train_idx] == 1)),
        train_score=train_score,
        n_trees=n_estimators,
    )
