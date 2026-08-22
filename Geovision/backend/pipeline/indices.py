"""Spectral index computation (NDVI / NDWI / NBR) and a simple brightness feature."""

from __future__ import annotations

from enum import Enum

import ee

SR_SCALE_FACTOR = 0.0001

MODE_NDVI = "ndvi"  # crop / vegetation stress
MODE_NDWI = "ndwi"  # flood / surface water
MODE_NBR = "nbr"  # burn scars

REFLECTANCE_BANDS = ["B2", "B3", "B4", "B8"]


class IndexMode(str, Enum):
    NDVI = MODE_NDVI
    NDWI = MODE_NDWI
    NBR = MODE_NBR

    @property
    def direction(self) -> str:
        """Which side of the change distribution flags damage.

        NDVI drops and NBR drops signal damage; NDWI rises signal new water.
        """
        if self is IndexMode.NDWI:
            return "increase"
        return "decrease"


def _valid_reflectance(img: ee.Image) -> ee.Image:
    """Mask pixels where any used SR band is non-positive (no data / invalid)."""
    valid = img.select(REFLECTANCE_BANDS).reduce(ee.Reducer.min()).gt(0)
    return img.updateMask(valid)


def ndvi(img: ee.Image) -> ee.Image:
    """Normalized Difference Vegetation Index, (B8-B4)/(B8+B4)."""
    img = _valid_reflectance(img)
    nir = img.select("B8").multiply(SR_SCALE_FACTOR)
    red = img.select("B4").multiply(SR_SCALE_FACTOR)
    return nir.subtract(red).divide(nir.add(red)).rename("index")


def ndwi(img: ee.Image) -> ee.Image:
    """McFeeters NDWI (green - NIR)/(green + NIR); open water is strongly positive."""
    img = _valid_reflectance(img)
    green = img.select("B3").multiply(SR_SCALE_FACTOR)
    nir = img.select("B8").multiply(SR_SCALE_FACTOR)
    return green.subtract(nir).divide(green.add(nir)).rename("index")


def nbr(img: ee.Image) -> ee.Image:
    """Normalized Burn Ratio, (B8-B12)/(B8+B12); burn scars become strongly negative."""
    img = _valid_reflectance(img)
    nir = img.select("B8").multiply(SR_SCALE_FACTOR)
    swir = img.select("B12").multiply(SR_SCALE_FACTOR)
    return nir.subtract(swir).divide(nir.add(swir)).rename("index")


def brightness(img: ee.Image) -> ee.Image:
    """Mean visible reflectance (B2, B3, B4) — a coarse albedo feature."""
    img = _valid_reflectance(img)
    vis = img.select(["B2", "B3", "B4"]).multiply(SR_SCALE_FACTOR)
    return vis.reduce(ee.Reducer.mean()).rename("brightness")


_INDEX_FUNCS = {
    IndexMode.NDVI: ndvi,
    IndexMode.NDWI: ndwi,
    IndexMode.NBR: nbr,
}


def compute_index(img: ee.Image, mode: IndexMode | str) -> ee.Image:
    """Compute the index selected by ``mode`` (``IndexMode`` or its string name)."""
    if isinstance(mode, str):
        mode = IndexMode(mode)
    return _INDEX_FUNCS[mode](img)
