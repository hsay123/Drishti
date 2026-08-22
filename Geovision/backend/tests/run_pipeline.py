"""Standalone pipeline test on the 2017 Kishanganj (Bihar) flood AOI/date pair.

Runs the full chain: ingestion → indices → change detection → fusion
classifier → quantification → thumbnails, printing diagnostics at each stage.
"""

from __future__ import annotations

from backend.models.schemas import AnalyzeRequest
from backend.pipeline import run_analysis

KISHANGANJ_BBOX = [87.75, 25.75, 88.15, 26.15]


def main() -> None:
    request = AnalyzeRequest(
        aoi=KISHANGANJ_BBOX,
        before_date="2017-06-01",
        after_date="2017-08-20",
        mode="ndwi",
        window_days=6,
    )
    print("Running full pipeline for 2017 Kishanganj flood (Bihar) ...")
    result = run_analysis(request)

    print("\n=== RESULTS ===")
    print(f"mode                 : {result.mode}")
    print(f"before/after         : {result.before_date} / {result.after_date} (±{result.window_days}d)")
    print(f"scenes in windows    : before={result.scenes_before}, after={result.scenes_after}")
    print(f"Otsu threshold       : {result.otsu_threshold}")
    print(f"classifier labeled px: {result.classifier_labeled_pixels} (bootstrap fit {result.classifier_bootstrap_fit_score})")
    print(f"changed / total px   : {result.changed_pixels} / {result.total_pixels}")
    print(f"AOI area             : {result.aoi_ha} ha")
    print(f"affected area        : {result.affected_ha} ha ({result.affected_pct}% of AOI)")
    print(f"severity             : {result.severity}")
    print(f"thumbnail payloads   : before={len(result.before_thumbnail_url) // 1024} KB, "
          f"after={len(result.after_thumbnail_url) // 1024} KB, "
          f"mask={len(result.mask_thumbnail_url) // 1024} KB (base64 PNG data URLs)")
    assert result.severity in {"mild", "moderate", "severe"}
    assert 0 <= result.affected_pct <= 100
    print("\nPIPELINE OK — zero errors.")


if __name__ == "__main__":
    main()
