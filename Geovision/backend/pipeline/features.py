"""Build a per-pixel feature stack and sample it into aligned NumPy arrays.

All bands (indices, cloud-frequency, brightness, water mask, AOI mask,
validity flags) are reprojected onto a single UTM grid at the requested scale
so that every feature is perfectly pixel-aligned and area calibration is exact
(each pixel == scale**2 m²).

Sampling is done in tiles because a single ``computePixels`` request is capped
at 48 MB: the AOI grid is tiled on the projection grid so tiles stitch back
together exactly.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import ee
import numpy as np

from backend.pipeline import indices, masking
from backend.pipeline.indices import IndexMode

DEFAULT_SCALE = 10  # meters; Sentinel-2 native resolution

#: Approx. pixel budget per computePixels request (keeps payload < 48 MB).
#: 10 bands x float32 = 40 bytes/pixel -> 800k px ≈ 32 MB, a safe margin.
MAX_TILE_PIXELS = 800_000
#: Max concurrent tile fetches.
MAX_TILE_WORKERS = 4


def utm_crs(geometry: ee.Geometry) -> str:
    """Best UTM CRS (EPSG:326xx north / 327xx south) for the AOI centroid."""
    lon, lat = geometry.centroid(1).coordinates().getInfo()
    zone = int((lon + 180) // 6) + 1
    if zone == 61:  # lon == 180 edge case
        zone = 60
    if lat >= 0:
        return f"EPSG:{32600 + zone}"
    return f"EPSG:{32700 + zone}"


def _rasterize_aoi(geometry: ee.Geometry) -> ee.Image:
    """1/0 image, 1 inside the AOI polygon, 0 elsewhere."""
    return ee.Image.constant(1).clip(geometry).unmask(0).rename("in_aoi")


def build_feature_stack(
    before_comp: ee.Image,
    after_comp: ee.Image,
    geometry: ee.Geometry,
    mode: IndexMode,
    before_date: date,
    after_date: date,
    before_window_days: int = 6,
    after_window_days: int = 6,
    scale: int = DEFAULT_SCALE,
) -> ee.Image:
    """Stack every analysis feature into a single pixel-aligned image.

    Bands:
      before_index, after_index, change              - index before/after and diff
      before_clear_frac, after_clear_frac        - SCL-derived cloud frequency
      before_brightness, after_brightness        - mean visible reflectance
      water_mask                                 - JRC permanent water (1/0)
      in_aoi                                     - AOI rasterization (1/0)
      before_valid, after_valid                  - usable-pixel flags (1/0)

    ``scale`` controls the UTM reprojection grid (meters). Presets sample at
    the 10 m native grid; custom AOIs may sample coarser (e.g. 20-30 m) to cut
    computePixels cost with negligible statistical impact.
    """
    before_index = indices.compute_index(before_comp, mode).rename("before_index")
    after_index = indices.compute_index(after_comp, mode).rename("after_index")
    change = after_index.subtract(before_index).rename("change")

    before_bright = indices.brightness(before_comp).rename("before_brightness")
    after_bright = indices.brightness(after_comp).rename("after_brightness")

    before_valid = before_index.mask().unmask(0).rename("before_valid")
    after_valid = after_index.mask().unmask(0).rename("after_valid")

    before_clear = _clear_fraction(geometry, before_date, before_window_days).rename("before_clear_frac")
    after_clear = _clear_fraction(geometry, after_date, after_window_days).rename("after_clear_frac")

    water = masking.permanent_water_mask(geometry)
    aoi = _rasterize_aoi(geometry)

    stack = ee.Image.cat(
        [
            before_index, after_index, change,
            before_clear, after_clear,
            before_bright, after_bright,
            water, aoi,
            before_valid, after_valid,
        ]
    )
    return stack.toFloat().reproject(crs=utm_crs(geometry), scale=scale)


def _clear_fraction(geometry: ee.Geometry, center: date, window_days: int) -> ee.Image:
    """Fraction of scenes in the window where the pixel is clear (SCL-based)."""
    start = ee.Date(center.isoformat()).advance(-window_days, "day")
    end = ee.Date(center.isoformat()).advance(window_days + 1, "day")
    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(geometry)
        .filterDate(start, end)
        .map(lambda img: masking.scl_cloud_mask(img).toFloat())
    )
    return collection.mean()


def sample_feature_stack(
    stack: ee.Image,
    geometry: ee.Geometry,
    scale: int = DEFAULT_SCALE,
    max_tile_pixels: int = MAX_TILE_PIXELS,
) -> dict[str, np.ndarray]:
    """Download the feature stack as aligned 2-D float32 NumPy arrays.

    Clips the stack to the AOI grid, derives the exact pixel grid from the
    image projection, then fetches it in tiles that are stitched back together.
    """
    clipped = stack.clipToBoundsAndScale(geometry=geometry, scale=scale)

    first_band = clipped.bandNames().get(0)
    proj = clipped.select([first_band]).projection().getInfo()
    crs = proj["crs"]
    transform = proj["transform"]
    x_origin, y_origin = transform[2], transform[5]

    bounds = geometry.bounds().transform(proj=crs, maxError=1).getInfo()["coordinates"][0]
    xs = [pt[0] for pt in bounds]
    ys = [pt[1] for pt in bounds]
    minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)

    col0 = math.floor((minx - x_origin) / scale)
    col1 = math.ceil((maxx - x_origin) / scale)
    row0 = math.floor((y_origin - maxy) / scale)
    row1 = math.ceil((y_origin - miny) / scale)
    rows, cols = row1 - row0, col1 - col0

    if rows <= 0 or cols <= 0:
        raise RuntimeError("Empty AOI grid after reprojection.")

    tile = math.isqrt(max_tile_pixels)
    band_names = clipped.bandNames().getInfo()

    out = {
        name: np.full((rows, cols), np.nan, dtype=np.float32)
        for name in band_names
    }

    def fetch_tile(r0: int, c0: int) -> tuple[int, int, int, int, np.ndarray]:
        th = min(tile, row1 - r0)
        tw = min(tile, col1 - c0)
        rect = ee.Geometry.Rectangle(
            [
                x_origin + c0 * scale,
                y_origin - (r0 + th) * scale,
                x_origin + (c0 + tw) * scale,
                y_origin - r0 * scale,
            ],
            proj=crs,
            geodesic=False,
        )
        tile_img = clipped.clipToBoundsAndScale(geometry=rect, scale=scale)
        data = ee.data.computePixels(
            {
                "expression": tile_img,
                "fileFormat": "NUMPY_NDARRAY",
                "bandIds": band_names,
            }
        )
        return r0, c0, th, tw, np.asarray(data)

    tile_starts = [
        (r0, c0)
        for r0 in range(row0, row1, tile)
        for c0 in range(col0, col1, tile)
    ]

    with ThreadPoolExecutor(max_workers=MAX_TILE_WORKERS) as pool:
        for r0, c0, th, tw, structured in pool.map(
            lambda rc: fetch_tile(*rc), tile_starts
        ):
            dr = r0 - row0
            dc = c0 - col0
            for name in band_names:
                out[name][dr : dr + th, dc : dc + tw] = structured[name]

    return out
