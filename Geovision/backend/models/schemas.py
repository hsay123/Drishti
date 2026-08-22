"""Pydantic request / response schemas for the analysis API."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Mode = Literal["ndvi", "ndwi", "nbr"]

ComparisonType = Literal["same_season", "year_over_year"]


class AnalyzeRequest(BaseModel):
    """Body of ``POST /analyze``.

    ``aoi`` is either a GeoJSON geometry dict (Polygon/MultiPolygon) or a
    ``[west, south, east, north]`` bounding box.
    """

    aoi: dict[str, Any] | list[float]
    before_date: date
    after_date: date
    mode: Mode = "ndvi"
    comparison_type: ComparisonType = "same_season"
    window_days: int = Field(default=6, ge=3, le=10)
    #: Sampling grid in meters. Default 20 m for the live custom-AOI path (4x
    #: fewer pixels than 10 m → ~4x faster computePixels; Phase 19b). Presets
    #: still sample at the fixed 10 m grid that produced their cached numbers.
    scale: int = Field(default=20, ge=10, le=100)
    use_cache: bool = True

    @field_validator("after_date")
    @classmethod
    def _after_after_before(cls, v: date, info) -> date:
        before = info.data.get("before_date")
        if before is not None and v <= before:
            raise ValueError("after_date must be strictly after before_date")
        return v


class AnalyzeResponse(BaseModel):
    """Result of ``POST /analyze``."""

    mode: str
    before_date: date
    after_date: date
    comparison_type: str = "same_season"
    window_days: int

    affected_ha: float
    aoi_ha: float
    affected_pct: float
    severity: str

    before_coverage_pct: float = 0.0
    after_coverage_pct: float = 0.0
    why_explanation: str | None = None
    recommended_actions: list[str] = Field(default_factory=list)

    caveat: str | None = None

    location_name: str | None = None
    location_precision: str = "coordinates"

    alert_message: str = ""
    caveats: list[str] = []

    otsu_threshold: float
    classifier_labeled_pixels: int
    classifier_bootstrap_fit_score: float
    classifier_note: str
    changed_pixels: int
    total_pixels: int

    confidence: str = "medium"
    confidence_note: str = ""

    scenes_before: int
    scenes_after: int
    aoi_bounds: list[float]

    before_thumbnail_url: str
    after_thumbnail_url: str
    mask_thumbnail_url: str

    cached: bool = False

    #: Dev-only per-stage timing breakdown (ms), Phase 19a. Absent/None for
    #: cached reads and any response that predates the field.
    timing_ms: dict[str, float] | None = None
