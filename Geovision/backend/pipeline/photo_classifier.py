"""Field-photo classification via the decided Groq vision model (watershed).

``classify_photo(image_bytes) -> PhotoClassification`` maps a geo-tagged field
photo to one of the watershed categories (water body, healthy/degraded
vegetation, bare degraded land, conservation structures, unclear) and is the
pluggable interface required by the problem statement: the active provider is
selected in ONE place (:data:`PROVIDERS` + ``PHOTO_CLASSIFIER_PROVIDER`` env,
default ``groq_vision``), so swapping providers before the demo touches no
call sites.

The Groq path prompts for strict JSON matching the schema and parses
defensively — any malformed/unknown output, API failure, or missing
``GROQ_API_KEY`` degrades to ``category="unclear"`` with an honest note rather
than crashing an upload. Satellite-index fusion happens separately in
``satellite_sample.py``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Callable

from backend import groq_client
from backend.models.photo import PhotoClassification, StructureType

logger = logging.getLogger("hackpreneur")

#: The single swap point for photo-classification providers (constraint #5).
#: Each entry: name -> callable(bytes) -> PhotoClassification.
PROVIDERS: dict[str, Callable[[bytes], PhotoClassification]] = {}

DEFAULT_PROVIDER = "groq_vision"

ALLOWED_CATEGORIES = {
    "water_body",
    "vegetation_healthy",
    "vegetation_degraded",
    "bare_degraded_land",
    "conservation_structure",
    "unclear",
}

ALLOWED_STRUCTURE_TYPES = {
    "check_dam",
    "farm_pond",
    "contour_trench",
    "plantation",
    "other",
}

#: Strict JSON contract shown to the vision model. Asking for ONLY-JSON keeps
#: parsing deterministic; descriptions anchor rural-India watershed context.
#: ``/no_think`` switches qwen3 reasoning models to direct-answer mode (their
#: <think> blocks otherwise burn the token budget); the parser strips any that
#: slip through anyway (see _strip_think_blocks).
_CLASSIFICATION_PROMPT = """/no_think
Classify this geo-tagged field photograph from a rural Indian watershed-development programme.

Respond with ONLY a JSON object — no markdown fences, no explanation — with exactly these fields:
{"category": "...", "structure_type": ..., "confidence": ...}

"category" must be exactly one of:
- "water_body": open water surface (pond, check-dam reservoir, stream, flooded field)
- "vegetation_healthy": dense green crops, grasses, or tree canopy
- "vegetation_degraded": sparse/stressed/yellowing vegetation, overgrazing, dying plants
- "bare_degraded_land": exposed soil, erosion, gullies, salt crust, little or no plant cover
- "conservation_structure": a built water/soil conservation intervention
- "unclear": cannot tell (too dark, blurry, indoor, people close-ups, ambiguous)

If and only if category is "conservation_structure", set "structure_type" to exactly one of:
"check_dam", "farm_pond", "contour_trench", "plantation", "other".
For every other category set "structure_type" to null.

"confidence" is your certainty as a number between 0 and 1.

JSON object:"""


def classify_photo(image_bytes: bytes, provider: str | None = None) -> PhotoClassification:
    """Classify one photo via the active provider; never raises.

    Every failure mode (unconfigured key, API error after retries, unparseable
    model output) returns ``category="unclear"`` carrying an honest note so
    uploads keep working offline / during outages.
    """
    name = provider or os.environ.get("PHOTO_CLASSIFIER_PROVIDER", DEFAULT_PROVIDER)
    fn = PROVIDERS.get(name)
    if fn is None:
        return PhotoClassification(
            category="unclear",
            confidence=0.0,
            provider=name,
            note=f"Unknown classifier provider {name!r} (known: {sorted(PROVIDERS)}).",
        )
    try:
        return fn(image_bytes)
    except groq_client.GroqNotConfiguredError as exc:
        logger.info("photo classification skipped: %s", exc)
        return _unclear(name, f"Not configured — {exc}")
    except groq_client.GroqError as exc:
        logger.warning("photo classification failed: %s", exc)
        return _unclear(name, f"Provider call failed after retries — {exc}")


def _classify_with_groq(image_bytes: bytes) -> PhotoClassification:
    """Groq free-tier vision path (model ``qwen/qwen3.6-27b``)."""
    raw = groq_client.vision_chat(_CLASSIFICATION_PROMPT, image_bytes, max_tokens=1000)
    parsed = _parse_json_object(_strip_think_blocks(raw))
    if parsed is None:
        return _unclear(
            _provider_tag(),
            f"Unparseable model output (expected JSON): {raw[:120]!r}",
        )
    category = parsed.get("category")
    if category not in ALLOWED_CATEGORIES:
        return _unclear(
            _provider_tag(),
            f"Model returned unknown category {category!r} (raw: {raw[:120]!r})",
        )
    structure_type = parsed.get("structure_type")
    if category != "conservation_structure":
        structure_type = None  # only meaningful for conservation structures
    elif structure_type not in ALLOWED_STRUCTURE_TYPES:
        structure_type = "other"
    confidence = _clamp_confidence(parsed.get("confidence"))
    return PhotoClassification(
        category=category,
        structure_type=structure_type,
        confidence=confidence,
        provider=_provider_tag(),
        note=None,
    )


PROVIDERS["groq_vision"] = _classify_with_groq


def _strip_think_blocks(text: str) -> str:
    """Drop qwen-style ``<think>…</think>`` reasoning before JSON extraction.

    Also handles a *truncated* think block (no closing tag when the token
    budget ran out mid-reasoning): keep only whatever follows the last
    ``</think>`` if present, else everything after ``<think>`` is reasoning
    and there is no answer to salvage.
    """
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return text


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """Defensively extract one JSON object from a model reply.

    Tolerates markdown code fences and surrounding prose by slicing between
    the first '{' and the last '}'. Returns ``None`` instead of raising.
    """
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned[4:] if cleaned.startswith("json") else cleaned
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _clamp_confidence(value: Any) -> float:
    """Coerce the model's confidence to [0, 1]; non-numbers become 0."""
    try:
        return round(min(max(float(value), 0.0), 1.0), 2)
    except (TypeError, ValueError):
        return 0.0


def _provider_tag() -> str:
    return f"groq:{groq_client.model_id()}"


def _unclear(provider: str, note: str) -> PhotoClassification:
    return PhotoClassification(category="unclear", confidence=0.0, provider=provider, note=note)
