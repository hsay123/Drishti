"""Cloud / snow masking from the Sentinel-2 SCL band and JRC permanent water masking."""

from __future__ import annotations

import ee

#: SCL classes treated as unusable surface: no-data, saturated/defective,
#: cloud shadow, medium cloud, high cloud, cirrus, snow/ice.
BAD_SCL_CLASSES = {0, 1, 3, 8, 9, 10, 11}

JRC_SURFACE_WATER = "JRC/GSW1_4/GlobalSurfaceWater"

#: JRC "occurrence" band: 0-100% of the time a pixel held water (1984-present).
#: Permanent water = 100% occurrence.
PERMANENT_WATER_OCCURRENCE = 100


def scl_cloud_mask(img: ee.Image) -> ee.Image:
    """Return a 1/0 mask image where 1 = clear surface pixel.

    Cloud shadow (3), medium/high cloud (8, 9), cirrus (10), snow/ice (11),
    plus no-data (0) and saturated/defective (1) are all flagged as 0.
    """
    scl = img.select("SCL")
    valid = ee.Image.constant(1)
    for cls in BAD_SCL_CLASSES:
        valid = valid.And(scl.neq(cls))
    return valid.rename("clear")


def mask_scl(img: ee.Image) -> ee.Image:
    """Zero out cloud/shadow/snow pixels in an S2 image via its SCL band."""
    return img.updateMask(scl_cloud_mask(img))


def permanent_water_mask(geometry: ee.Geometry) -> ee.Image:
    """Return a 1/0 image (named ``water_mask``) flagging permanent water.

    Reads the JRC Global Surface Water dataset and keeps only pixels with 100%
    water occurrence (permanent water), so existing rivers/lakes are excluded
    from flood change detection.
    """
    occurrence = ee.Image(JRC_SURFACE_WATER).select("occurrence")
    mask = occurrence.eq(PERMANENT_WATER_OCCURRENCE).rename("water_mask")
    return mask.clip(geometry)
