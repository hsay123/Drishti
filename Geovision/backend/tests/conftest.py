"""Shared fixtures for the watershed-module API tests.

Runs fully offline: photo storage is redirected to a throwaway directory and
the synthetic GPS-EXIF JPEGs are generated in-process, so no GEE / Groq /
network access is needed.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend import photo_store
from backend.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with photo storage pointed at a throwaway directory."""
    monkeypatch.setattr(photo_store, "PHOTOS_DIR", tmp_path / "photos")
    monkeypatch.setattr(photo_store, "INDEX_PATH", tmp_path / "photos" / "index.json")
    with TestClient(app) as c:
        yield c


def gps_jpeg(lat_dms=(28, 36, 0), lon_dms=(77, 12, 0)) -> bytes:
    """A tiny in-memory JPEG carrying GPS EXIF (~28.6N, 77.2E) + capture time."""
    from PIL.ExifTags import IFD

    img = Image.new("RGB", (64, 48), (96, 128, 64))
    exif = Image.Exif()
    gps = exif.get_ifd(IFD.GPSInfo)
    gps[1] = "N"
    gps[2] = lat_dms
    gps[3] = "E"
    gps[4] = lon_dms
    gps[6] = 216  # altitude in metres (single rational)
    exif.get_ifd(IFD.Exif)[36867] = "2024:05:08 10:30:00"
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def plain_jpeg() -> bytes:
    """A JPEG with no EXIF at all (missing-GPS failure case)."""
    img = Image.new("RGB", (32, 32), (10, 10, 10))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()
