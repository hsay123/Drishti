"""Watershed-module API tests (pytest + FastAPI TestClient).

Runs fully offline: photo storage is redirected to a tmp dir and the synthetic
GPS-EXIF JPEG comes from ``conftest.gps_jpeg``, so no GEE / network / real
photos are needed. The existing /analyze pipeline is untouched by these tests
— they only assert the new routes exist alongside it in the OpenAPI schema.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from backend.main import app
from backend.pipeline.photo_ingest import point_in_aoi
from backend.tests.conftest import gps_jpeg, plain_jpeg

AOI_BBOX = "[77.0, 28.5, 77.4, 28.8]"  # contains the fixture's GPS point


class TestUpload:
    def test_happy_path(self, client):
        res = client.post(
            "/photos/upload",
            data={
                "aoi": AOI_BBOX,
                "watershed_id": "ws-demo",
                "notes": "check dam site",
                # Keep this test offline: classifier + satellite fusion are
                # covered separately in test_photo_classifier.py.
                "auto_classify": "false",
            },
            files={"file": ("field.jpg", gps_jpeg(), "image/jpeg")},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["lat"] == pytest.approx(28.6, abs=1e-4)
        assert body["lon"] == pytest.approx(77.2, abs=1e-4)
        assert body["watershed_id"] == "ws-demo"
        assert body["captured_at"].startswith("2024-05-08T10:30")
        assert body["classification"] is None  # auto_classify disabled above

        listed = client.get("/photos", params={"watershed_id": "ws-demo"}).json()
        assert len(listed) == 1 and listed[0]["id"] == body["id"]

        file_res = client.get(f"/photos/{body['id']}/file")
        assert file_res.status_code == 200
        assert file_res.headers["content-type"].startswith("image/")
        assert len(file_res.content) > 100

    def test_missing_gps_rejected_cleanly(self, client):
        res = client.post(
            "/photos/upload",
            data={"aoi": AOI_BBOX, "auto_classify": "false"},
            files={"file": ("no_gps.jpg", plain_jpeg(), "image/jpeg")},
        )
        assert res.status_code == 400
        assert "GPS EXIF" in res.json()["detail"]["error"]

    def test_corrupt_bytes_rejected(self, client):
        res = client.post(
            "/photos/upload",
            data={"aoi": AOI_BBOX, "auto_classify": "false"},
            files={"file": ("junk.jpg", b"this is not an image at all", "image/jpeg")},
        )
        assert res.status_code == 400
        assert res.json()["detail"]["error"]

    def test_outside_aoi_rejected(self, client):
        # Sydney coords — well outside the Delhi bbox AOI.
        far = gps_jpeg(lat_dms=(33, 52, 0), lon_dms=(151, 12, 0))
        res = client.post(
            "/photos/upload",
            data={"aoi": AOI_BBOX, "auto_classify": "false"},
            files={"file": ("sydney.jpg", far, "image/jpeg")},
        )
        assert res.status_code == 422
        assert "outside the selected AOI" in res.json()["detail"]["detail"]

    def test_invalid_aoi_json(self, client):
        res = client.post(
            "/photos/upload",
            data={"aoi": "not-json", "auto_classify": "false"},
            files={"file": ("field.jpg", gps_jpeg(), "image/jpeg")},
        )
        assert res.status_code == 400
        assert "Invalid AOI" in res.json()["detail"]["error"]

    def test_empty_file_rejected(self, client):
        res = client.post(
            "/photos/upload",
            data={"aoi": AOI_BBOX, "auto_classify": "false"},
            files={"file": ("empty.jpg", b"", "image/jpeg")},
        )
        assert res.status_code == 400


class TestExifParsing:
    def test_southern_hemisphere_signs(self):
        """S/W refs must yield negative decimals (extract_photo_metadata is pure)."""
        from PIL.ExifTags import IFD

        from backend.pipeline.photo_ingest import extract_photo_metadata

        img = Image.new("RGB", (16, 16), (1, 2, 3))
        exif = Image.Exif()
        gps = exif.get_ifd(IFD.GPSInfo)
        gps[1] = "S"
        gps[2] = (33, 52, 0)
        gps[3] = "W"
        gps[4] = (151, 12, 0)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif.tobytes())

        meta = extract_photo_metadata(buf.getvalue(), "sydney.jpg", "unit-1")
        assert meta.lat == pytest.approx(-33.866667, abs=1e-4)
        assert meta.lon == pytest.approx(-151.2, abs=1e-4)
        assert meta.captured_at is None  # no capture time written


class TestListing:
    def test_empty_index_returns_empty_list(self, client):
        assert client.get("/photos").json() == []

    def test_unknown_photo_file_404(self, client):
        assert client.get("/photos/deadbeef/file").status_code == 404


class TestPointInAoi:
    BBOX = [77.0, 28.5, 77.4, 28.8]
    POLYGON = {
        "type": "Polygon",
        "coordinates": [
            [[77.0, 28.5], [77.4, 28.5], [77.4, 28.8], [77.0, 28.8], [77.0, 28.5]],
        ],
    }
    WITH_HOLE = {
        "type": "Polygon",
        "coordinates": [
            [[76.9, 28.4], [77.5, 28.4], [77.5, 28.9], [76.9, 28.9], [76.9, 28.4]],
            [[77.1, 28.55], [77.3, 28.55], [77.3, 28.75], [77.1, 28.75], [77.1, 28.55]],
        ],
    }

    def test_bbox_containment(self):
        assert point_in_aoi(28.6, 77.2, self.BBOX)
        assert not point_in_aoi(29.6, 77.2, self.BBOX)

    def test_polygon_containment_and_hole(self):
        inside = {"lat": 28.45, "lon": 77.05}   # in outer ring, out of hole
        hole_pt = {"lat": 28.65, "lon": 77.2}   # inside the hole → outside polygon
        assert point_in_aoi(inside["lat"], inside["lon"], self.WITH_HOLE)
        assert not point_in_aoi(hole_pt["lat"], hole_pt["lon"], self.WITH_HOLE)
        assert point_in_aoi(28.6, 77.2, self.POLYGON)

    def test_feature_wrapper_supported(self):
        feature = {"type": "Feature", "geometry": self.POLYGON}
        assert point_in_aoi(28.6, 77.2, feature)


def test_existing_routes_untouched():
    """The locked /analyze surface must still be present after router include."""
    schema = app.openapi()
    path_items = schema["paths"]
    for route in ("/analyze", "/health", "/watchlist"):
        assert route in path_items
    assert "post" in path_items["/analyze"]
