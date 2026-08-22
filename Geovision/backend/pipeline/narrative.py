"""Watershed field-report narratives via the shared Groq client (watershed).

``build_narrative(photos)`` turns the current photo set into a short written
field report. The model receives ONLY structured facts extracted by
``aggregate_photos`` (counts per category, intervention count, date span,
mean fused indices) and is instructed to never invent numbers — the output is
a readable summary of data the pipeline already measured.

Degradation contract mirrors ``photo_classifier``: no key / API failure /
unparseable reply falls back to a deterministic template summary with
``generated=False`` and an honest note. Never raises.
"""

from __future__ import annotations

import logging
import re

from backend import groq_client

logger = logging.getLogger("hackpreneur")

_NARRATIVE_SYSTEM = (
    "You write concise field reports for a rural watershed-development "
    "monitoring programme. You receive structured statistics extracted from "
    "geo-tagged field photographs and their fused satellite indices. Rules: "
    "(1) use ONLY the numbers provided — inventing or rounding figures is "
    "forbidden; (2) 3-5 sentences, plain factual tone; (3) if the dataset is "
    "small or sparse, say so explicitly instead of overclaiming; (4) end with "
    "one practical next step for a field officer."
)

_NARRATIVE_PROMPT = """Write a watershed monitoring field report from these verified statistics:

- Photos analysed: {total}
- Date range: {date_range}
- Category counts: {category_counts}
- Verified conservation structures: {interventions}
- Mean satellite NDVI at photo points: {ndvi}{ndvi_n}
- Mean satellite NDWI at photo points: {ndwi}{ndwi_n}

Field report:"""


def build_narrative(photos: list[dict]) -> dict:
    """Narrative summary for a photo set; degrades to a template, never raises."""
    stats = aggregate_photos(photos)
    try:
        raw = groq_client.text_chat(
            _NARRATIVE_PROMPT.format(**stats),
            system=_NARRATIVE_SYSTEM,
            max_tokens=600,
            timeout_s=30.0,
        )
        text = _clean(raw)
        if not text:
            raise groq_client.GroqError("empty narrative")
        return {
            "narrative": text,
            "provider": f"groq:{groq_client.model_id()}",
            "generated": True,
            "note": None,
        }
    except groq_client.GroqNotConfiguredError as exc:
        logger.info("narrative generation skipped: %s", exc)
        return _fallback(stats, f"Not configured — {exc}")
    except groq_client.GroqError as exc:
        logger.warning("narrative generation failed: %s", exc)
        return _fallback(stats, f"Provider call failed after retries — {exc}")


def aggregate_photos(photos: list[dict]) -> dict:
    """Structured, model-safe facts about a photo set (all values pre-formatted)."""
    total = len(photos)
    counts: dict[str, int] = {}
    ndvis, ndwis = [], []
    dates: list[str] = []
    interventions = 0
    for p in photos:
        cat = (p.get("classification") or {}).get("category") or "unclassified"
        counts[cat] = counts.get(cat, 0) + 1
        if p.get("intervention_verified"):
            interventions += 1
        sat = p.get("satellite") or {}
        if isinstance(sat.get("ndvi"), (int, float)):
            ndvis.append(sat["ndvi"])
        if isinstance(sat.get("ndwi"), (int, float)):
            ndwis.append(sat["ndwi"])
        d = p.get("captured_at") or p.get("uploaded_at")
        if d:
            dates.append(str(d)[:10])

    mean_ndvi = sum(ndvis) / len(ndvis) if ndvis else None
    mean_ndwi = sum(ndwis) / len(ndwis) if ndwis else None
    date_range = f"{min(dates)} to {max(dates)}" if dates else "no capture dates available"

    return {
        "total": total,
        "date_range": date_range,
        "category_counts": (
            ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "none"
        ),
        "interventions": interventions,
        "ndvi": f"{mean_ndvi:.3f}" if mean_ndvi is not None else "unavailable",
        "ndvi_n": f" (n={len(ndvis)})" if ndvis else "",
        "ndwi": f"{mean_ndwi:.3f}" if mean_ndwi is not None else "unavailable",
        "ndwi_n": f" (n={len(ndwis)})" if ndwis else "",
        "_mean_ndvi": mean_ndvi,
        "_mean_ndwi": mean_ndwi,
        "_counts": counts,
    }


def _fallback(stats: dict, note: str) -> dict:
    """Deterministic plain-facts summary when the provider is unavailable."""
    structure_line = (
        f"{stats['interventions']} photo(s) verify conservation structures"
        if stats["interventions"]
        else "No conservation structures verified yet"
    )
    idx_line = ""
    if stats["_mean_ndvi"] is not None:
        idx_line = f" Mean satellite NDVI at photo points is {stats['_mean_ndvi']:.2f}"
        if stats["_mean_ndwi"] is not None:
            idx_line += f" and mean NDWI is {stats['_mean_ndwi']:.2f}"
        idx_line += "."
    narrative = (
        f"{stats['total']} geo-tagged field photo(s) on record "
        f"({stats['category_counts']}). {structure_line}.{idx_line} "
        "Upload more photos inside the monitored AOI to strengthen coverage."
    )
    return {
        "narrative": narrative,
        "provider": "template",
        "generated": False,
        "note": note,
    }


def _clean(text: str) -> str | None:
    stripped = text.strip()
    if not stripped:
        return None
    # Reasoning models may still leak <think> blocks despite effort=none.
    cleaned = re.sub(r"<think>.*?</think>", "", stripped, flags=re.DOTALL).strip()
    return cleaned or None


__all__ = ["build_narrative", "aggregate_photos"]
