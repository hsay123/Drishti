"""Photo classifier + satellite-fusion tests (watershed module, offline).

The Groq client is never contacted: classification tests stub
``groq_client.vision_chat`` and the no-key path short-circuits before any
network call. Fusion tests inject fake composite/index/reducer callables into
``sample_indices_at_point``'s documented seams, so GEE is never touched.
"""

from __future__ import annotations

import pytest

from backend import groq_client, photo_store
from backend.models.photo import PhotoClassification
from backend.pipeline import photo_classifier
from backend.pipeline.satellite_sample import sample_indices_at_point

from backend.tests.conftest import gps_jpeg

AOI_BBOX = "[77.0, 28.5, 77.4, 28.8]"

_VALID_REPLY = (
    '{"category": "conservation_structure", "structure_type": "check_dam", '
    '"confidence": 0.87}'
)


@pytest.fixture(autouse=True)
def _offline_groq(monkeypatch):
    """Guarantee no real Groq network traffic in any test here."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-real")
    groq_client.reset_client()
    yield
    groq_client.reset_client()


def _mock_vision(monkeypatch, reply: str) -> None:
    monkeypatch.setattr(groq_client, "vision_chat", lambda *a, **k: reply)


class TestClassifyPhoto:
    def test_valid_json_parsed(self, monkeypatch):
        _mock_vision(monkeypatch, _VALID_REPLY)
        result = photo_classifier.classify_photo(b"fake-image-bytes")
        assert result.category == "conservation_structure"
        assert result.structure_type == "check_dam"
        assert result.confidence == pytest.approx(0.87)
        assert "qwen" in result.provider
        assert result.note is None

    def test_markdown_fenced_json_still_parses(self, monkeypatch):
        _mock_vision(
            monkeypatch,
            'Here you go:\n```json\n{"category": "water_body", "structure_type": null, "confidence": 0.9}\n```',
        )
        assert photo_classifier.classify_photo(b"x").category == "water_body"

    def test_malformed_output_falls_back_to_unclear(self, monkeypatch):
        _mock_vision(monkeypatch, "I think this is a lovely check dam!")
        result = photo_classifier.classify_photo(b"x")
        assert result.category == "unclear"
        assert "Unparseable" in result.note

    def test_unknown_category_falls_back(self, monkeypatch):
        _mock_vision(monkeypatch, '{"category": "alien_landing_site", "confidence": 0.99}')
        result = photo_classifier.classify_photo(b"x")
        assert result.category == "unclear"
        assert "unknown category" in result.note

    def test_confidence_clamped(self, monkeypatch):
        _mock_vision(monkeypatch, '{"category": "unclear", "confidence": 7}')
        assert photo_classifier.classify_photo(b"x").confidence == 1.0
        _mock_vision(monkeypatch, '{"category": "unclear", "confidence": -3}')
        assert photo_classifier.classify_photo(b"x").confidence == 0.0
        _mock_vision(monkeypatch, '{"category": "unclear"}')
        assert photo_classifier.classify_photo(b"x").confidence == 0.0

    def test_structure_type_only_for_conservation(self, monkeypatch):
        _mock_vision(
            monkeypatch,
            '{"category": "vegetation_healthy", "structure_type": "check_dam", "confidence": 0.5}',
        )
        assert photo_classifier.classify_photo(b"x").structure_type is None
        _mock_vision(
            monkeypatch,
            '{"category": "conservation_structure", "structure_type": "mega_dam", "confidence": 0.5}',
        )
        assert photo_classifier.classify_photo(b"x").structure_type == "other"

    def test_missing_api_key_degrades_to_unclear(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        groq_client.reset_client()
        result = photo_classifier.classify_photo(b"x")
        assert result.category == "unclear"
        assert "GROQ_API_KEY" in result.note

    def test_provider_error_degrades_to_unclear(self, monkeypatch):
        def boom(*a, **k):
            raise groq_client.GroqError("rate limited after retries")

        monkeypatch.setattr(groq_client, "vision_chat", boom)
        result = photo_classifier.classify_photo(b"x")
        assert result.category == "unclear"
        assert "failed after retries" in result.note

    def test_unknown_provider_name_falls_back(self):
        result = photo_classifier.classify_photo(b"x", provider="does_not_exist")
        assert result.category == "unclear"
        assert "Unknown classifier provider" in result.note


class TestSatelliteFusion:
    @staticmethod
    def _geom_stub(lat: float, lon: float) -> str:
        return "GEOM"  # keeps ee.Geometry out of offline tests

    def test_samples_both_indices_at_point(self):
        calls: list[str] = []

        def composite_fn(geometry, center_date, window_days):
            return ("COMPOSITE", 2, 6, 1.0)

        def index_fn(img, mode):
            calls.append(mode.value)
            return img  # passthrough sentinel

        seq = {"n": 0}

        def reducer_fn(index_img, geometry, scale_m):
            seq["n"] += 1
            return {"index": 0.42 if seq["n"] == 1 else -0.12}  # ndvi then ndwi

        sample = sample_indices_at_point(
            28.6, 77.2, __import__("datetime").date(2024, 5, 8),
            composite_fn=composite_fn, index_fn=index_fn, reducer_fn=reducer_fn,
            geometry_fn=self._geom_stub,
        )
        assert sample is not None
        assert sample.ndvi == pytest.approx(0.42)
        assert sample.ndwi == pytest.approx(-0.12)
        assert sample.date == "2024-05-08"
        assert calls == ["ndvi", "ndwi"]
        assert "SRISHTI-DRISHTI" in sample.source  # honest substitute labeling

    def test_non_numeric_reducer_values_become_none(self):
        sample = sample_indices_at_point(
            28.6, 77.2, __import__("datetime").date(2024, 5, 8),
            composite_fn=lambda g, d, w: ("C", 1, 6, 1.0),
            index_fn=lambda img, mode: img,
            reducer_fn=lambda img, g, s: {"index": None},
            geometry_fn=self._geom_stub,
        )
        assert sample.ndvi is None and sample.ndwi is None

    def test_no_imagery_degrades_to_none(self):
        from backend.pipeline.ingestion import NoImageryError

        def composite_fn(geometry, center_date, window_days):
            raise NoImageryError("no scenes")

        sample = sample_indices_at_point(
            28.6, 77.2, __import__("datetime").date(2024, 5, 8),
            composite_fn=composite_fn,
            geometry_fn=self._geom_stub,
        )
        assert sample is None


class TestEnrichedUpload:
    def test_upload_auto_classifies_and_fuses(self, client, monkeypatch):
        _mock_vision(monkeypatch, '{"category": "vegetation_degraded", "confidence": 0.66}')
        monkeypatch.setattr(
            "backend.routers.watershed.sample_indices_at_point",
            lambda lat, lon, d, **k: {
                "ndvi": 0.18, "ndwi": -0.05, "date": "2024-05-08",
                "source": "stub",
            },
        )

        res = client.post(
            "/photos/upload",
            data={"aoi": AOI_BBOX},
            files={"file": ("field.jpg", gps_jpeg(), "image/jpeg")},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["classification"]["category"] == "vegetation_degraded"
        assert body["intervention_verified"] is False
        assert body["satellite"]["ndvi"] == 0.18

        stored = photo_store.list_photos()
        assert stored[0]["classification"]["category"] == "vegetation_degraded"
        assert stored[0]["satellite"]["ndvi"] == 0.18

    def test_intervention_flag_set_for_structures(self, client, monkeypatch):
        _mock_vision(monkeypatch, _VALID_REPLY)
        monkeypatch.setattr(
            "backend.routers.watershed.sample_indices_at_point",
            lambda lat, lon, d, **k: None,
        )
        res = client.post(
            "/photos/upload",
            data={"aoi": AOI_BBOX},
            files={"file": ("dam.jpg", gps_jpeg(), "image/jpeg")},
        )
        body = res.json()
        assert body["intervention_verified"] is True
        assert body["satellite"] is None  # fusion failure must not break upload

    def test_auto_classify_false_skips_enrichment(self, client, monkeypatch):
        called = {"classifier": False}

        def spy(*a, **k):
            called["classifier"] = True
            return PhotoClassification(category="water_body")

        monkeypatch.setattr(photo_classifier, "classify_photo", spy)
        res = client.post(
            "/photos/upload",
            data={"aoi": AOI_BBOX, "auto_classify": "false"},
            files={"file": ("field.jpg", gps_jpeg(), "image/jpeg")},
        )
        assert res.status_code == 200
        assert called["classifier"] is False
        assert res.json()["classification"] is None
