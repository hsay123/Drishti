"""Image output helpers: true-color previews and change-mask overlays.

GEE ``getThumbURL`` URLs require GEE credentials, so browser-side ``<img>`` tags
cannot load them. Instead the backend downloads the rendered thumbnails over an
authenticated session and returns them as base64 PNG data URLs — self-contained
responses the React frontend can drop straight into ``src`` attributes.
"""

from __future__ import annotations

import base64
import io
import time
from concurrent.futures import ThreadPoolExecutor

import ee
import numpy as np
from PIL import Image

from backend.gee_client import initialize
from backend.pipeline.indices import SR_SCALE_FACTOR

#: Change-mask overlay colors per analysis mode (RGB).
MASK_COLORS = {
    "ndvi": (220, 38, 38),   # red    → crop stress
    "ndwi": (37, 99, 235),   # blue   → new water
    "nbr": (234, 88, 12),    # orange → burn
}

MASK_ALPHA = 170

TRUE_COLOR_MAX = 0.3

#: Default preview resolution (px). 512 px is plenty for a UI preview and
#: downloads ~2.2x faster than the old 768 px default.
THUMB_DIMENSIONS = 512


def _authenticated_session():
    """A requests session with auto-refreshing GEE credentials."""
    from google.auth.transport.requests import AuthorizedSession

    initialize()
    return AuthorizedSession(ee.data.get_persistent_credentials())


def _true_color(img: ee.Image) -> ee.Image:
    """Reflectance-scaled true color (B4, B3, B2) in [0, 0.3]."""
    return img.select(["B4", "B3", "B2"]).multiply(SR_SCALE_FACTOR)


def _data_url(raw: bytes, mime: str = "image/png") -> str:
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def rgb_thumbnail_data_url(
    img: ee.Image,
    geometry: ee.Geometry,
    dimensions: int = THUMB_DIMENSIONS,
    session=None,
) -> str:
    """Download a true-color PNG of the region and return it as a data URL.

    Renders via a GEE thumbnail (fast, server-side), fetches the PNG bytes with
    the authenticated session, and embeds them base64 so the response is fully
    self-contained. ``session`` may be shared across concurrent calls to avoid
    re-creating an AuthorizedSession per thumbnail. Raises ``RuntimeError`` if
    GEE cannot render/fetch it.
    """
    rgb = _true_color(img)
    thumb = rgb.getThumbId(
        {
            "region": geometry.bounds(),
            "dimensions": dimensions,
            "format": "png",
            "min": 0,
            "max": TRUE_COLOR_MAX,
        }
    )
    url = ee.data.makeThumbUrl(thumb)
    session = session or _authenticated_session()
    response = session.get(url, timeout=120)
    if response.status_code != 200:
        raise RuntimeError(
            f"Thumbnail fetch failed with HTTP {response.status_code} "
            f"({response.content[:200]!r})."
        )
    return _data_url(response.content)


def render_thumbnails(
    before_comp: ee.Image,
    after_comp: ee.Image,
    mask: np.ndarray,
    mode: str,
    geometry: ee.Geometry,
    dimensions: int = THUMB_DIMENSIONS,
    timings: dict | None = None,
) -> tuple[str, str, str]:
    """Render before/after true-color previews + the mask overlay in parallel.

    The two true-color downloads are independent GEE requests, so they run on a
    shared authenticated session with ``ThreadPoolExecutor`` instead of serially
    (Phase 19b: before+after thumbnails were ~30% of live-run time). When a
    ``timings`` dict is supplied, per-thumbnail durations are recorded under
    ``thumbnail_before`` / ``thumbnail_after`` / ``thumbnail_mask``.
    """

    def _recorded(name: str, fn) -> str:
        if timings is None:
            return fn()
        t0 = time.perf_counter()
        try:
            return fn()
        finally:
            timings[name] = (time.perf_counter() - t0) * 1000.0

    session = _authenticated_session()
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_before = pool.submit(
            _recorded, "thumbnail_before",
            lambda: rgb_thumbnail_data_url(before_comp, geometry, dimensions, session),
        )
        future_after = pool.submit(
            _recorded, "thumbnail_after",
            lambda: rgb_thumbnail_data_url(after_comp, geometry, dimensions, session),
        )
        before_url = future_before.result()
        after_url = future_after.result()
    mask_url = _recorded(
        "thumbnail_mask", lambda: mask_thumbnail(mask, mode)
    )
    return before_url, after_url, mask_url


def _data_url_from_pil(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def mask_thumbnail(
    mask: np.ndarray,
    mode: str,
    alpha: int = MASK_ALPHA,
) -> str:
    """Semi-transparent RGBA overlay of the change mask as a PNG data URL.

    Phase 19c trialed drawing white region contours on top of the tint but was
    reverted (see analyze.py) — the mask overlay renders as a raw pixel tint.
    """
    color = MASK_COLORS.get(mode, MASK_COLORS["ndvi"])
    rgba = np.zeros((*mask.shape, 4), dtype=np.uint8)
    changed = mask.astype(bool)
    rgba[changed, 0] = color[0]
    rgba[changed, 1] = color[1]
    rgba[changed, 2] = color[2]
    rgba[changed, 3] = alpha
    return _data_url_from_pil(Image.fromarray(rgba, mode="RGBA"))
