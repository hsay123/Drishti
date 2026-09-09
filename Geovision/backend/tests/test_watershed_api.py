"""Watershed-module API tests (pytest + FastAPI TestClient).

Runs fully offline: photo storage is redirected to a tmp dir and the synthetic
GPS-EXIF JPEG comes from ``conftest.gps_jpeg``, so no GEE / network / real
photos are needed. The existing /analyze pipeline is untouched by these tests
— they only assert the new routes exist alongside it in the OpenAPI schema.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from backend.main import app
from backend.pipeline.drishti_import import (
    MalformedImportError,
    parse_drishti_csv,
    row_to_metadata,
    validate_row,
)
from backend.pipeline.photo_ingest import point_in_aoi
from backend.tests.conftest import gps_jpeg, plain_jpeg

AOI_BBOX = "[77.0, 28.5, 77.4, 28.8]"  # contains the fixture's GPS point

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def csv_file(text: str, name: str = "import.csv") -> dict:
    """Multipart file tuple for the /photos/import endpoint."""
    return {"file": (name, io.BytesIO(text.encode("utf-8")), "text/csv")}


def load_sample_csv() -> str:
    """The shipped DRISHTI-style sample fixture (clearly fake demo data)."""
    return (FIXTURES_DIR / "drishti_sample_import.csv").read_text(encoding="utf-8")


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
        # DRISHTI-hierarchy fields stay null when not supplied.
        assert body["state"] is None
        assert body["micro_watershed_id"] is None
        assert body["project_year"] is None

        listed = client.get("/photos", params={"watershed_id": "ws-demo"}).json()
        assert len(listed) == 1 and listed[0]["id"] == body["id"]

        file_res = client.get(f"/photos/{body['id']}/file")
        assert file_res.status_code == 200
        assert file_res.headers["content-type"].startswith("image/")
        assert len(file_res.content) > 100

    def test_upload_with_drishti_context_fields(self, client):
        """Optional SRISHTI-DRISHTI hierarchy fields round-trip when supplied."""
        res = client.post(
            "/photos/upload",
            data={
                "aoi": AOI_BBOX,
                "auto_classify": "false",
                "state": "Bihar",
                "district": "Kishanganj",
                "block": "Pothia",
                "gram_panchayat": "Sample GP",
                "micro_watershed_id": "2C1A3L1",
                "project_year": "2014-15",
            },
            files={"file": ("field.jpg", gps_jpeg(), "image/jpeg")},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["state"] == "Bihar"
        assert body["district"] == "Kishanganj"
        assert body["block"] == "Pothia"
        assert body["gram_panchayat"] == "Sample GP"
        assert body["micro_watershed_id"] == "2C1A3L1"
        assert body["project_year"] == "2014-15"

        listed = client.get("/photos").json()
        assert listed[0]["district"] == "Kishanganj"

    def test_blank_drishti_fields_stay_null(self, client):
        """Empty-string context values must not be stored as truthy data."""
        res = client.post(
            "/photos/upload",
            data={
                "aoi": AOI_BBOX,
                "auto_classify": "false",
                "state": "",
                "micro_watershed_id": "",
            },
            files={"file": ("field.jpg", gps_jpeg(), "image/jpeg")},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["state"] is None
        assert body["micro_watershed_id"] is None

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


class TestDrishtiImport:
    """POST /photos/import — DRISHTI-style CSV bulk import (offline).

    Satellite fusion is stubbed to ``None`` so tests never touch GEE even on
    machines with cached credentials (same hermeticity as conftest's storage
    redirect).
    """

    @pytest.fixture(autouse=True)
    def _no_satellite(self, monkeypatch):
        from backend.routers import watershed

        monkeypatch.setattr(watershed, "sample_indices_at_point", lambda *a, **k: None)

    def test_sample_fixture_imports_happy_path(self, client):
        res = client.post("/photos/import", files=csv_file(load_sample_csv()))
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["total_rows"] == 4
        assert body["imported_count"] == 4
        assert body["skipped_count"] == 0

        # Points land on the map exactly like individually-uploaded photos.
        listed = client.get("/photos").json()
        assert len(listed) == 4
        first = next(p for p in listed if p["filename"] == "sample_check_dam_01.jpg")
        assert first["lat"] == pytest.approx(28.6120)
        assert first["classification"]["category"] == "not_analyzed"
        assert first["classification"]["provider"] == "csv_import"
        assert first["district"] == "Demo District"
        assert first["micro_watershed_id"] == "SAMPLE-MWS-001"
        assert first["project_year"] == "2014-15"
        # Pre-existing DRISHTI classification text is preserved in notes only.
        assert "drishti_classification=check_dam" in first["notes"]
        assert "Sample row" in first["notes"]

    def test_missing_required_columns_rejected(self, client):
        bad = "photo_ref,state\nrow1.jpg,Bihar\n"
        res = client.post("/photos/import", files=csv_file(bad))
        assert res.status_code == 400
        assert "required column" in res.json()["detail"]["error"]

    def test_one_bad_row_among_good_ones_skips_individually(self, client):
        csv_text = (
            "photo_ref,latitude,longitude\n"
            "good1.jpg,28.60,77.20\n"
            "bad_coord.jpg,999.0,77.30\n"
            "not_numeric.jpg,twenty-eight,77.10\n"
            "good2.jpg,28.70,77.25\n"
        )
        res = client.post("/photos/import", files=csv_file(csv_text))
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["total_rows"] == 4
        assert body["imported_count"] == 2
        assert {p["filename"] for p in body["imported"]} == {"good1.jpg", "good2.jpg"}
        assert body["skipped_count"] == 2
        reasons = " | ".join(s["reason"] for s in body["skipped"])
        assert "out-of-range coordinates" in reasons
        assert "non-numeric latitude/longitude" in reasons
        assert all("row_number" in s for s in body["skipped"])

    def test_outside_aoi_rows_skipped_when_aoi_given(self, client):
        csv_text = (
            "photo_ref,latitude,longitude\n"
            "inside.jpg,28.60,77.20\n"
            "sydney.jpg,-33.86,151.20\n"
        )
        res = client.post(
            "/photos/import", data={"aoi": AOI_BBOX}, files=csv_file(csv_text)
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["imported_count"] == 1
        assert body["skipped_count"] == 1
        assert "outside the selected AOI" in body["skipped"][0]["reason"]

    def test_empty_file_rejected(self, client):
        res = client.post("/photos/import", files=csv_file(""))
        assert res.status_code == 400

    def test_non_utf8_rejected(self, client):
        res = client.post(
            "/photos/import",
            files={"file": ("bin.csv", io.BytesIO(b"\xff\xfe\x00binary"), "text/csv")},
        )
        assert res.status_code == 400
        assert "UTF-8" in res.json()["detail"]["error"]

    def test_row_limit_enforced(self, client):
        header = "photo_ref,latitude,longitude\n"
        rows = "".join(f"r{i}.jpg,28.6,77.2\n" for i in range(251))
        res = client.post(
            "/photos/import", files=csv_file(header + rows)
        )
        assert res.status_code == 400
        assert "250-row import limit" in res.json()["detail"]["error"]

    def test_existing_single_upload_unaffected(self, client):
        """Constraint: /photos/upload must behave exactly as before."""
        res = client.post(
            "/photos/upload",
            data={"aoi": AOI_BBOX, "auto_classify": "false"},
            files={"file": ("field.jpg", gps_jpeg(), "image/jpeg")},
        )
        assert res.status_code == 200, res.text


class TestDrishtiImportParsing:
    """Pure-function coverage for pipeline/drishti_import.py (no HTTP)."""

    def test_alias_headers_resolve(self):
        rows = parse_drishti_csv("Filename,Lat,Lon,MWS_Code\nx.jpg,28.6,77.2,A1\n")
        assert rows[0]["photo_ref"] == "x.jpg"
        assert rows[0]["micro_watershed_id"] == "A1"

    def test_bad_timestamp_degrades_to_none(self):
        rows = parse_drishti_csv("photo_ref,latitude,longitude,timestamp\nx.jpg,28.6,77.2,whenever\n")
        validated, reason = validate_row(rows[0])
        assert reason is None
        meta = row_to_metadata(validated, photo_id="t1")
        assert meta.captured_at is None  # tolerated — position still matters

    def test_good_timestamp_parsed(self):
        rows = parse_drishti_csv(
            "photo_ref,latitude,longitude,timestamp\nx.jpg,28.6,77.2,2024-05-08T10:30:00\n"
        )
        validated, reason = validate_row(rows[0])
        meta = row_to_metadata(validated, photo_id="t2")
        assert meta.captured_at is not None and meta.captured_at.year == 2024

    def test_unparseable_garbage_raises_typed_error(self):
        with pytest.raises(MalformedImportError):
            parse_drishti_csv('photo_ref,latitude\n"x.jpg\nbroken')


def test_existing_routes_untouched():
    """The locked /analyze surface must still be present after router include."""
    schema = app.openapi()
    path_items = schema["paths"]
    for route in ("/analyze", "/health", "/watchlist", "/photos/import"):
        assert route in path_items
    assert "post" in path_items["/analyze"]


# ---------------------------------------------------------------------------
# Watershed health score (Phase 24 — judge-feedback "Watershed Health" chart)
# ---------------------------------------------------------------------------

NDVI_ENTRY = {
    "preset_id": "po-valley-drought-2022",
    "location_name": "Po Valley, Italy",
    "mode": "ndvi",
    "priority": "High",
    "affected_pct": 24.31,
    "aoi_bounds": [10.75, 44.7, 11.25, 45.1],
    "after_coverage_pct": 40.0,
    "before_coverage_pct": 60.0,
}

NDWI_ENTRY = {
    "preset_id": "kishanganj-flood-2017",
    "location_name": "Kishanganj District, Bihar, India",
    "mode": "ndwi",
    "priority": "Medium",
    "affected_pct": 13.01,
    "aoi_bounds": [87.75, 25.75, 88.15, 26.15],
    "after_coverage_pct": 2.4,
    "before_coverage_pct": 1.6,
}


def sample_photo(lat: float, lon: float, **overrides) -> dict:
    base = {
        "lat": lat,
        "lon": lon,
        "intervention_verified": False,
        "satellite": {"ndvi": 0.2, "ndwi": -0.2},
    }
    base.update(overrides)
    return base


class TestHealthScore:
    """Pure-function coverage for backend/pipeline/health_score.py (no HTTP)."""

    def test_empty(self):
        from backend.pipeline.health_score import compute_health_scores

        assert compute_health_scores([], []) == []

    def test_ndvi_entry_uses_satellite_coverage(self):
        from backend.pipeline.health_score import compute_health_scores

        [r] = compute_health_scores([NDVI_ENTRY], [])
        assert r["preset_id"] == "po-valley-drought-2022"
        assert r["sub_scores"]["vegetation"] == 40  # after_coverage_pct, ndvi mode
        assert r["sub_scores"]["water"] is None
        assert r["sub_scores"]["interventions"] is None
        assert r["overall"] == 40  # single present sub-score drives the score
        assert r["sub_scores"]["vegetation_source"] == "satellite:ndvi coverage"
        assert r["photo_count"] == 0

    def test_ndwi_entry_buckets_field_photos(self):
        """Photos outside the AOI are excluded; mixed signals combine."""
        from backend.pipeline.health_score import compute_health_scores

        photos = [
            sample_photo(25.9, 87.9, satellite={"ndvi": 0.2, "ndwi": 0.5}),
            sample_photo(26.0, 88.0, satellite={"ndvi": 0.4, "ndwi": 0.2},
                         intervention_verified=True),
            # Far outside the Kishanganj bbox — must not count:
            sample_photo(28.6, 77.2, satellite={"ndvi": 1.0, "ndwi": 1.0},
                         intervention_verified=True),
        ]
        [r] = compute_health_scores([NDWI_ENTRY], photos)
        assert r["photo_count"] == 2
        assert r["verified_intervention_count"] == 1
        # ndwi mode -> satellite water coverage wins over photo mean.
        assert r["sub_scores"]["water"] == 2  # after_coverage_pct 2.4
        assert r["sub_scores"]["water_source"] == "satellite:ndwi coverage"
        # No ndvi-mode coverage -> fall back to field mean NDVI (0.3 -> 30).
        assert r["sub_scores"]["vegetation"] == 30
        assert r["sub_scores"]["vegetation_source"] == "field:mean ndvi"
        # 1 of 2 regional photos is a verified intervention.
        assert r["sub_scores"]["interventions"] == 50
        # overall = 30*0.4 + 2*0.3 + 50*0.3 = 27.6 -> 28.
        assert r["overall"] == 28

    def test_no_satellite_data_degrades_to_null(self):
        from backend.pipeline.health_score import compute_health_scores

        entry = dict(NDWI_ENTRY)
        entry["after_coverage_pct"] = None
        entry["before_coverage_pct"] = None
        photos = [sample_photo(25.9, 87.9, satellite=None)]
        [r] = compute_health_scores([entry], photos)
        assert r["sub_scores"]["vegetation"] is None
        assert r["sub_scores"]["water"] is None
        # Photos exist but none verified -> 0 is a real (not "no data") value.
        assert r["sub_scores"]["interventions"] == 0
        assert r["overall"] == 0

    def test_no_data_reports_null_overall_not_zero(self):
        from backend.pipeline.health_score import compute_health_scores

        entry = dict(NDVI_ENTRY)
        entry["mode"] = "nbr"
        entry["after_coverage_pct"] = None
        entry["before_coverage_pct"] = None
        [r] = compute_health_scores([entry], [])
        assert r["photo_count"] == 0
        assert r["sub_scores"]["vegetation"] is None
        assert r["sub_scores"]["water"] is None
        assert r["sub_scores"]["interventions"] is None
        assert r["overall"] is None  # the UI's distinct "no data" state

    def test_malformed_aoi_never_breaks_scoring(self):
        from backend.pipeline.health_score import compute_health_scores

        entry = dict(NDWI_ENTRY)
        entry["aoi_bounds"] = "not-a-aoi"
        photos = [sample_photo(25.9, 87.9)]
        # Degrades to no photos rather than raising.
        [r] = compute_health_scores([entry], photos)
        assert r["photo_count"] == 0
        assert r["overall"] is not None


class TestWatershedHealthEndpoint:
    """GET /watersheds/health — fully offline (presets come from cache files)."""

    def test_health_scores_shape(self, client):
        res = client.get("/watersheds/health")
        assert res.status_code == 200, res.text
        body = res.json()
        assert "watersheds" in body and isinstance(body["watersheds"], list)
        assert body["photo_count"] == 0  # tmp photo store, none uploaded

        # The three on-disk presets are always present -> deterministic offline.
        assert len(body["watersheds"]) == 3
        names = {w["location_name"] for w in body["watersheds"]}
        assert "Kishanganj District, Bihar, India" in names
        assert "Po Valley, Emilia-Romagna, Italy" in names
        assert "Gospers Mountain, NSW, Australia" in names

        for w in body["watersheds"]:
            assert {
                "preset_id", "location_name", "mode", "priority",
                "affected_pct", "aoi_bounds", "photo_count",
                "verified_intervention_count", "overall", "sub_scores",
            } <= set(w)
            assert w["aoi_bounds"] is not None

        kish = next(w for w in body["watersheds"] if w["mode"] == "ndwi")
        assert kish["sub_scores"]["water"] == 2  # ndwi coverage 2.4 -> 2
        assert kish["sub_scores"]["interventions"] is None  # no field photos yet
        assert kish["photo_count"] == 0

        nsw = next(w for w in body["watersheds"] if w["mode"] == "nbr")
        # nbr mode has no matching satellite coverage and no photos -> no data.
        assert nsw["overall"] is None
        assert nsw["sub_scores"]["vegetation"] is None

    def test_uploads_feed_health_buckets(self, client):
        """A photo inside a preset AOI appears in that watershed's score."""
        # Kishanganj bbox [87.75, 25.75, 88.15, 26.15] — reuse the bbox AOI.
        res = client.post(
            "/photos/upload",
            data={
                "aoi": "[87.75, 25.75, 88.15, 26.15]",
                "auto_classify": "false",
            },
            files={"file": ("bihar.jpg", gps_jpeg(lat_dms=(25, 54, 0), lon_dms=(88, 0, 0)), "image/jpeg")},
        )
        assert res.status_code == 200, res.text

        body = client.get("/watersheds/health").json()
        assert body["photo_count"] == 1
        kish = next(w for w in body["watersheds"] if w["mode"] == "ndwi")
        assert kish["photo_count"] == 1  # bucketed into this watershed's AOI
        assert kish["sub_scores"]["interventions"] == 0  # photo present, not verified
