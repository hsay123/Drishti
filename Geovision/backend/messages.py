"""Natural-language alert messages + caveats for the /analyze response.

The headline (`alert_message`) is one clean sentence per mode/severity; the
nuance (Phase 10 classifier note, Phase 13 same-season warning) stays in a
separate `caveats` list so the headline is never cluttered.
"""

from __future__ import annotations

import logging
import time

from backend.geocoder import location_for
from backend.models.schemas import AnalyzeResponse

logger = logging.getLogger("hackpreneur")

#: Human fallback when no place name is available (geocoding failed/offline).
FALLBACK_PLACE = "the selected area"

#: Rule-based decision-support list (Phase 18b) keyed on mode -> severity.
#: Deliberately a simple lookup — no scoring model, wording tuned to stay
#: calm/non-alarmist for mild cases.
RECOMMENDED_ACTIONS: dict[str, dict[str, list[str]]] = {
    "ndwi": {  # flood
        "severe": [
            "Alert nearby villages / local authorities",
            "Assess the need for evacuation support",
            "Monitor river level closely over the next 48h",
        ],
        "moderate": [
            "Notify local disaster-management contact",
            "Monitor for further water-level rise",
        ],
        "mild": [
            "Log for routine monitoring",
            "No immediate action needed",
        ],
    },
    "ndvi": {  # crop stress
        "severe": [
            "Field inspection recommended",
            "Check irrigation / pest status",
            "Consider a yield-loss assessment",
        ],
        "moderate": [
            "Schedule a field check within the week",
        ],
        "mild": [
            "Continue routine monitoring",
        ],
    },
    "nbr": {  # burn scar
        "severe": [
            "Assess containment status with local fire authority",
            "Evaluate need for evacuation in surrounding areas",
        ],
        "moderate": [
            "Monitor for spread",
            "Notify local forest / fire department",
        ],
        "mild": [
            "Log for monitoring",
        ],
    },
}


def recommended_actions_for(mode: str, severity: str) -> list[str]:
    """Look up the rule-based action list for a mode/severity pair."""
    return RECOMMENDED_ACTIONS.get(mode, {}).get(severity, [])


def _place(location_name: str | None) -> str:
    return location_name if location_name else FALLBACK_PLACE


def _pct(affected_pct: float) -> str:
    return f"{affected_pct:.2f}"


def _km2(affected_ha: float) -> str:
    return f"{affected_ha / 100.0:.1f}"


def build_alert_message(
    mode: str,
    severity: str,
    location_name: str | None,
    affected_pct: float,
    affected_ha: float,
) -> str:
    """One clean headline sentence; calm and non-alarming when mild."""
    place = _place(location_name)
    if mode == "ndwi":  # flood
        if severity == "mild":
            return (
                f"No major flood signal near {place} — {_pct(affected_pct)}% of "
                "the monitored area shows new water coverage, below the alert "
                "threshold."
            )
        return (
            f"Flood signal detected near {place} — {_pct(affected_pct)}% of the "
            f"monitored area shows new water coverage ({severity})."
        )
    if mode == "ndvi":  # crop stress
        if severity == "mild":
            return (
                f"No major crop-stress signal near {place} — {_pct(affected_pct)}% "
                "of the monitored area affected, below the alert threshold."
            )
        return (
            f"Crop stress detected near {place} — {_pct(affected_pct)}% of the "
            f"monitored area affected ({severity})."
        )
    # nbr — burn scar
    if severity == "mild":
        return (
            f"No major fire/burn signal near {place} — approximately "
            f"{_km2(affected_ha)} km² affected, below the alert threshold."
        )
    return (
        f"Fire/burn damage detected near {place} — approximately "
        f"{_km2(affected_ha)} km² affected ({severity})."
    )


def build_caveats(caveat: str | None, classifier_note: str | None) -> list[str]:
    """Secondary nuance kept separate from the headline alert message."""
    out: list[str] = []
    if caveat:
        out.append(caveat)
    if classifier_note:
        out.append(classifier_note)
    return out


def build_why_explanation(
    mode: str,
    before_coverage_pct: float,
    after_coverage_pct: float,
) -> str | None:
    """One-line "why" under the alert headline (Phase 18a).

    States how the raw per-date signal coverage moved, not the diff/severity.
    The verb is picked from the actual direction so the sentence is always
    honest even on an edge case where coverage moved the other way. Returns
    None when no movement was measured (e.g. a stale cached response that
    predates the coverage fields), so the UI never shows a meaningless
    "0.0% to 0.0%" line.
    """
    if before_coverage_pct == 0.0 and after_coverage_pct == 0.0:
        return None
    if mode == "ndwi":  # flood — water coverage
        verb = "increased from" if after_coverage_pct >= before_coverage_pct else "decreased from"
        return (
            f"Water-covered area {verb} {before_coverage_pct:.1f}% to "
            f"{after_coverage_pct:.1f}% of the monitored region."
        )
    if mode == "ndvi":  # crop stress — healthy vegetation coverage
        verb = "dropped from" if after_coverage_pct <= before_coverage_pct else "rose from"
        return (
            f"Healthy-vegetation coverage {verb} {before_coverage_pct:.1f}% to "
            f"{after_coverage_pct:.1f}% of the monitored region."
        )
    if mode == "nbr":  # burn scar — burn-signature coverage
        verb = "increased from" if after_coverage_pct >= before_coverage_pct else "decreased from"
        return (
            f"Burn-signature coverage {verb} {before_coverage_pct:.1f}% to "
            f"{after_coverage_pct:.1f}% of the monitored region."
        )
    return None


def enrich_response(
    preset_id: str | None,
    response: AnalyzeResponse,
    timing_ms: dict[str, float] | None = None,
) -> AnalyzeResponse:
    """Add location + alert fields to a response object (live or cached).

    Runs after the analysis so a geocoding failure can never block or fail a
    result. Never raises. When ``timing_ms`` is supplied (Phase 19a), the
    geocoding duration is recorded under ``geocoding``.
    """
    t0 = time.perf_counter()
    location_name, location_precision = location_for(response.aoi_bounds, preset_id)
    if timing_ms is not None:
        timing_ms["geocoding"] = (time.perf_counter() - t0) * 1000.0
    logger.info(
        "resolved location %r (%s) for preset=%s",
        location_name,
        location_precision,
        preset_id,
    )
    return response.model_copy(
        update={
            "location_name": location_name,
            "location_precision": location_precision,
            "alert_message": build_alert_message(
                response.mode,
                response.severity,
                location_name,
                response.affected_pct,
                response.affected_ha,
            ),
            "caveats": build_caveats(response.caveat, response.classifier_note),
            "why_explanation": build_why_explanation(
                response.mode,
                response.before_coverage_pct,
                response.after_coverage_pct,
            ),
            "recommended_actions": recommended_actions_for(
                response.mode, response.severity
            ),
        }
    )
