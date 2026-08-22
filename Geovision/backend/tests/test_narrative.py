"""Narrative-generator tests (watershed module, offline).

``groq_client.text_chat`` is stubbed so no network call happens; the no-key
path short-circuits before any request. Aggregation runs on plain dicts in
the same shape ``photo_store.list_photos`` returns.
"""

from __future__ import annotations

import pytest

from backend import groq_client
from backend.pipeline.narrative import aggregate_photos, build_narrative


@pytest.fixture(autouse=True)
def _offline_groq(monkeypatch):
    """Guarantee no real Groq traffic unless a test stubs text_chat itself."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-real")
    groq_client.reset_client()
    yield
    groq_client.reset_client()


def _photo(**over) -> dict:
    base = {
        "id": "p1",
        "captured_at": "2024-05-08 10:30:00",
        "uploaded_at": "2026-08-22 12:00:00",
        "classification": {"category": "water_body"},
        "intervention_verified": False,
        "satellite": {"ndvi": 0.48, "ndwi": -0.46},
    }
    base.update(over)
    return base


class TestAggregatePhotos:
    def test_counts_interventions_means_and_dates(self):
        photos = [
            _photo(classification={"category": "water_body"}),
            _photo(
                id="p2",
                classification={"category": "conservation_structure"},
                intervention_verified=True,
                satellite={"ndvi": 0.60, "ndwi": None},
            ),
            _photo(id="p3", classification=None, satellite=None),
        ]
        stats = aggregate_photos(photos)

        assert stats["total"] == 3
        assert stats["interventions"] == 1
        assert stats["category_counts"] == (
            "conservation_structure: 1, unclassified: 1, water_body: 1"
        )
        assert stats["_mean_ndvi"] == pytest.approx((0.48 + 0.60) / 2)
        assert stats["_mean_ndwi"] == pytest.approx(-0.46)
        assert stats["ndvi_n"] == " (n=2)"
        assert stats["ndwi_n"] == " (n=1)"
        assert stats["date_range"] == "2024-05-08 to 2024-05-08"

    def test_empty_photo_set_is_model_safe(self):
        stats = aggregate_photos([])
        assert stats["total"] == 0
        assert stats["category_counts"] == "none"
        assert stats["ndvi"] == "unavailable"
        assert stats["date_range"] == "no capture dates available"

        # The exact dict must format into the prompt without KeyErrors.
        from backend.pipeline.narrative import _NARRATIVE_PROMPT

        rendered = _NARRATIVE_PROMPT.format(**stats)
        assert "Photos analysed: 0" in rendered

    def test_upload_date_used_when_capture_missing(self):
        stats = aggregate_photos([_photo(captured_at=None, uploaded_at="2026-01-02 09:00:00")])
        assert stats["date_range"] == "2026-01-02 to 2026-01-02"


class TestBuildNarrative:
    def test_generated_via_stubbed_provider(self, monkeypatch):
        captured = {}

        def fake_text_chat(prompt, *, system=None, max_tokens=512, timeout_s=30.0):
            captured["prompt"] = prompt
            return "<think>reasoning noise</think>Three check dams verified."

        monkeypatch.setattr(groq_client, "text_chat", fake_text_chat)
        result = build_narrative([_photo(intervention_verified=True)])

        assert result["generated"] is True
        assert result["narrative"] == "Three check dams verified."  # think stripped
        assert result["note"] is None
        assert result["provider"].startswith("groq:")
        # Prompt carries measured facts only.
        assert "Verified conservation structures: 1" in captured["prompt"]
        assert "Mean satellite NDVI" in captured["prompt"]

    def test_missing_key_falls_back_to_template(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        groq_client.reset_client()
        result = build_narrative([_photo()])

        assert result["generated"] is False
        assert result["provider"] == "template"
        assert "GROQ_API_KEY" in result["note"]
        # Template still reports the measured facts.
        assert "1 geo-tagged field photo(s)" in result["narrative"]
        assert "water_body" in result["narrative"]

    def test_provider_error_falls_back_to_template(self, monkeypatch):
        def boom(*a, **k):
            raise groq_client.GroqError("429 after retries")

        monkeypatch.setattr(groq_client, "text_chat", boom)
        result = build_narrative([_photo()])
        assert result["generated"] is False
        assert "failed after retries" in result["note"]
        assert "field photo(s) on record" in result["narrative"]

    def test_empty_reply_falls_back_to_template(self, monkeypatch):
        monkeypatch.setattr(groq_client, "text_chat", lambda *a, **k: "")
        result = build_narrative([])
        assert result["generated"] is False
        assert "0 geo-tagged field photo(s)" in result["narrative"]

    def test_unexpected_provider_crash_propagates(self, monkeypatch):
        def explodes(*a, **k):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(groq_client, "text_chat", explodes)
        with pytest.raises(RuntimeError):
            build_narrative([])  # contract: GroqError handled, others propagate


class TestEndpoint:
    def test_photos_narrative_endpoint_200_with_mocked_provider(self, client, monkeypatch):
        monkeypatch.setattr(groq_client, "text_chat", lambda *a, **k: "All quiet upstream.")
        res = client.get("/photos/narrative")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["narrative"] == "All quiet upstream."
        assert body["generated"] is True
        assert body["photo_count"] == 0

    def test_photos_narrative_endpoint_offline_template(self, client, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        groq_client.reset_client()
        res = client.get("/photos/narrative")
        assert res.status_code == 200
        body = res.json()
        assert body["generated"] is False
        assert body["provider"] == "template"
