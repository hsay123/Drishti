"""Pydantic schemas for geo-tagged field photographs (watershed module).

A ``PhotoMetadata`` is what EXIF parsing yields for one upload; a
``PhotoPoint`` is the enriched object served back to clients after optional
classification (``PhotoClassification``) and fusion with satellite index
values sampled at the photo's coordinates.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

#: Top-level photo categories produced by ``photo_classifier.classify_photo``.
PhotoCategory = Literal[
    "water_body",
    "vegetation_healthy",
    "vegetation_degraded",
    "bare_degraded_land",
    "conservation_structure",
    "unclear",
]

#: Sub-type recorded when the category is ``conservation_structure``.
StructureType = Literal[
    "check_dam",
    "farm_pond",
    "contour_trench",
    "plantation",
    "other",
]


class PhotoMetadata(BaseModel):
    """EXIF-derived facts about one uploaded photograph."""

    id: str
    filename: str
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    altitude_m: float | None = None
    captured_at: datetime | None = None
    uploaded_at: datetime | None = None
    width_px: int | None = None
    height_px: int | None = None
    filesize_bytes: int | None = None
    notes: str | None = None
    watershed_id: str | None = None


class SatelliteSample(BaseModel):
    """Satellite index values fused to a photo point (SRISHTI-DRISHTI-equivalent 30m data)."""

    ndvi: float | None = None
    ndwi: float | None = None
    date: str | None = None  # ISO date of the composite the sample came from
    source: str = "sentinel2_sr_20m"


class PhotoClassification(BaseModel):
    """Result of classifying one photo (provider-agnostic schema)."""

    category: PhotoCategory
    structure_type: StructureType | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    provider: str = "heuristic_v1"
    note: str | None = None


class PhotoPoint(PhotoMetadata):
    """A photo plus every derived layer the UI needs to render its marker."""

    classification: PhotoClassification | None = None
    satellite: SatelliteSample | None = None
    intervention_verified: bool = False
