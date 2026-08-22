# CLAUDE.md

> **2026-08-08 (pre-demo) Phase 19e: Timeout tuning for elevated GEE latency.** Diagnosed a
> "timeout error" on a previously-working custom AOI (`[68.35,28.05,68.45,28.15]`, ndvi,
> same-season `2024-05-08 → 2025-05-21`) — **NOT a code bug or quota rejection**. Log evidence:
> composites completed in ~11 s (valid 1.00 both periods), GEE returned transient **503s** on
> `computePixels` (client retried, recovered), then **thumbnail rendering exceeded the old 90 s
> backend ceiling → 504** at 01:42:43; the frontend's 90 s cap would have aborted ~34 s earlier.
> `/health` flapped degraded only because its 8 s probe is marginal under latency — a direct GEE
> probe passed (4.8 s), and zero `EEException`/429/quota lines exist in the log. **Scenario 2a
> (slow-but-succeeding)**: GEE was latency-throttled, not rejecting. Fix (timeout/UX tuning only —
> no locked pipeline logic touched): frontend `ANALYZE_TIMEOUT_MS` 90 s → **180 s**; backend
> `SAMPLE_TIMEOUT_S` 120 s → **180 s** and `THUMBNAIL_TIMEOUT_S` 90 s → **180 s** (ingestion 90s
> untouched — composites are fast). The loading overlay now shows a **live elapsed counter**
> ("Still working… Ns elapsed — large/slow requests can take up to ~3 minutes") instead of a
> static spinner, and the Cancel button is unchanged. Verified: `npm run build` clean (2.79 s);
> all 3 cached presets still read instantly with correct numbers (13.01 moderate / 24.31 severe /
> 53.5 severe); `/health` → ok. Honest framing: with latency this high tonight, live custom runs
> may still exceed even 180 s under load — **lean on the 3 cached presets for the demo**; a real
> quota/429 rejection would surface as `EEException` (429), which we have never observed.

> Demo fallback video recorded 2026-08-07: `media/demo-capture.mp4` (30 s, 1440×900, h264) — full
> end-to-end flow: Bihar (Kishanganj) flood preset (instant cached alert), before/after + mask toggle,
> model-note tooltip, Po Valley crop + NSW burn presets, then a map-click custom AOI live run
> (resolved to "New South Wales, Australia"). Play via `xdg-open media/demo-capture.mp4`.

## Project Overview
HackPreneur Satellite Change Detector is a full-stack MVP that performs rapid, near-real-time satellite change detection for disaster and crop-stress scenarios. A user selects an AOI and before/after date ranges, Sentinel-2 imagery is pulled via Google Earth Engine, a mode-selectable spectral index (NDVI for crop stress, NDWI for flood, NBR for burn) is computed and differenced, the change signal is cleaned with SCL-based cloud masking and (for flood) JRC permanent-water subtraction, an adaptive Otsu threshold converts the change distribution into a binary mask, and a small Random Forest "fusion" classifier refines the mask using auto-bootstrapped labels from multiple independent signals. The result is quantified (hectares + % of AOI) with a severity rating and served to a modern React frontend with a Leaflet before/after toggle and change-mask overlay.

## Architecture
```
AOI (GeoJSON/bbox) + before/after dates + mode
        │
        ▼
[ingestion]  S2 median composites over adaptive windows (SCL cloud-masked;
        │  starts ±6d, widens to ±10/±14d when clear-sky coverage is poor)
        │  before_img, after_img
        ▼
[indices]   NDVI / NDWI / NBR (mode-selectable)
        │  before_index, after_index
        ▼
[masking]   SCL cloud/shadow/snow mask + JRC permanent water mask (flood mode)
        ▼
[change_detection]  after-before diff → Otsu adaptive threshold → binary mask
        │  change_mask (unrefined)
        ▼
[fusion_classifier]  RF trained on auto-bootstrapped confident labels from
                     independent signals (index change + cloud flag + water + brightness)
        │  refined_mask
        ▼
[quantify]  hectares, % of AOI, severity (mild/moderate/severe), alert object
        ▼
[FastAPI POST /analyze]  →  JSON + thumbnails (before/after/mask)
        ▼
[React frontend]  AOI picker, date ranges, mode selector, Leaflet map
                 with before/after toggle + change-mask overlay, alert card
```
Each stage is a pure function in `backend/pipeline/`; `main.py` orchestrates them.
The feature stack (indices + cloud-frequency + brightness + water + AOI + validity)
is reprojected onto a UTM grid at 10 m and sampled in tiles via `computePixels`, so
all per-pixel features are pixel-aligned and area math is exact.

## Tech Stack
- Python 3.13, FastAPI, uvicorn
- Google Earth Engine Python API (earthengine-api 1.7.38), geemap 0.38.3
- numpy, pandas, Pillow
- scikit-learn (RandomForestClassifier), scikit-image (Otsu threshold via skimage.filters.threshold_otsu)
- pydantic (schemas)
- React + Vite, Leaflet (react-leaflet), design-taste-frontend skill for styling

## Environment / Setup
- Project lives in `~/Documents/YCCEHACK` (relocated from an earlier `~/hackpreneur-satellite` draft).
- GEE Cloud project ID: `project-326ab593-31e9-43ed-8cd` (noncommercial/community tier, registered).
- GEE auth: done via `earthengine authenticate`; token saved locally. Verified working
  (`check_connectivity()` returns True).
- venv symlinked at `./venv` → `~/hackpreneur-satellite/venv` (Python 3.13, all deps installed).
- Run backend: `source venv/bin/activate && uvicorn backend.main:app --reload --port 8000` (from project root; `backend` is an importable package).
- Run frontend: `cd frontend && npm install && npm run dev`.

## API Contract
Base URL: `http://localhost:8000` (uvicorn). Backend is CORS-open to the Vite dev
origin (`localhost:5173`). All thumbnails are returned as **base64 PNG data URLs** —
GEE thumbnail URLs require GEE auth, so the backend fetches them server-side via an
`AuthorizedSession` and embeds them; the browser needs no GEE credentials.

### `GET /health`
```json
{"status": "ok" | "degraded", "gee_connected": true | false}
```

### `POST /analyze`
Request:
```json
{
  "aoi": [68.90, 26.85, 68.95, 26.90],        // GeoJSON geometry dict OR [w,s,e,n] bbox
  "before_date": "2022-05-15",                 // YYYY-MM-DD
  "after_date":  "2022-09-01",                 // must be strictly after before_date
  "mode": "ndvi" | "ndwi" | "nbr",             // crop | flood | burn
  "comparison_type": "same_season" | "year_over_year",  // optional; crop mode defaults
  "window_days": 6,                            // optional, 3-10, ± median-composite window
  "scale": 20                                  // optional, 10-100 m sampling grid; default 20 (Phase 19b)
}
```
Response (status 200):
```json
{
  "mode": "ndwi", "before_date": "2022-05-15", "after_date": "2022-09-01",
  "window_days": 6,
  "affected_ha": 45.4, "aoi_ha": 11055.6, "affected_pct": 0.41,
  "severity": "mild" | "moderate" | "severe",
  "before_coverage_pct": 3.1, "after_coverage_pct": 15.9,   // Phase 18a — per-date signal coverage, fixed threshold
  "why_explanation": "Water-covered area increased from 3.1% to 15.9% of the monitored region.",  // Phase 18a
  "recommended_actions": ["Notify local disaster management contact", "Monitor for further water-level rise"],  // Phase 18b
  "comparison_type": "same_season",
  "caveat": null | "string — set only when crop-stress mode risks the harvest confound",
  "confidence": "high" | "medium" | "low",
  "confidence_note": "string — why the confidence grade was assigned; honest, proxy-based",
  "otsu_threshold": 0.346, "classifier_labeled_pixels": 100000,
  "classifier_bootstrap_fit_score": 1.0,
  "classifier_note": "Fusion/refinement layer scored against its own auto-generated bootstrap labels, not independent ground truth — used to suppress residual cloud/shadow noise on top of the physics-based index signal, not as a standalone accuracy metric.",
  "changed_pixels": 1134, "total_pixels": 276390,
  "scenes_before": 2, "scenes_after": 3, "aoi_bounds": [68.9, 26.85, 68.95, 26.9],
  "location_name": "Kishanganj District, Bihar, India",   // Phase 15; null on geocode failure
  "location_precision": "city",                              // coordinates | settlement | district | city | ...
  "alert_message": "Flood signal detected near Kishanganj District, Bihar, India — 13.01% of the monitored area shows new water coverage (moderate).",  // Phase 16
  "caveats": ["Fusion/refinement layer scored against its own auto-generated bootstrap labels, ..."],  // Phase 16; includes classifier_note always, seasonal caveat when set
  "cached": false,
  "before_thumbnail_url": "data:image/png;base64,...",
  "after_thumbnail_url":  "data:image/png;base64,...",
  "mask_thumbnail_url":   "data:image/png;base64,...",
  "timing_ms": {"ingestion": 5698, "indices": 17630, "change_detection": 3, "fusion_classifier": 1112, "quantify": 2, "geocoding": 107, "thumbnail_before": 4608, "thumbnail_after": 23599, "thumbnail_mask": 13, "total": 49326}   // Phase 19a — dev-only per-stage wall-clock breakdown (ms)
}
```
`location_name` / `location_precision` / `alert_message` / `caveats` (Phases 15/16): populated on
every `/analyze` response (cached reads included) by `backend/messages.py:enrich_response`.
Location naming comes from `backend/geocoder.py` (`location_for(aoi_bounds, preset_id)`):
the three validated presets return hardcoded region names (never geocoded, no network), custom
AOIs get a Nominatim reverse-geocode (centroid, 1 req/s throttle, 5 s timeout, in-memory cache
keyed by centroid rounded to 2 dp; `NOMINATIM_URL` env var overrides the endpoint for outage
simulation). On any geocode failure the response degrades gracefully: `location_name = null`,
`location_precision = null`, and the alert falls back to "the selected area". `alert_message`
is a per-mode/per-severity natural-language headline (see `backend/messages.py`). `caveats` is
an ordered list of human-readable warning strings: the Phase 13 seasonal `caveat` (when set) is
copied in first, and the `classifier_note` is always appended (the standalone `caveat` field is
preserved for Phase 13 backward-compat).
`before_coverage_pct` / `after_coverage_pct` / `why_explanation` (Phase 18a): an explainability
triplet on every `/analyze` response. The coverage percentages are the *independent* per-date
raw signal coverage (NOT the diff/severity): % of AOI pixels above a fixed per-mode threshold
in the before composite and separately in the after composite, over pixels inside the AOI that
are valid at that date (so clouds never dilute it). Fixed thresholds live in
`backend/pipeline/quantify.py:COVERAGE_THRESHOLDS`: NDWI water `> 0.0`, NDVI healthy vegetation
`> 0.3`, NBR burn signature `< -0.1`. Computed from the already-sampled feature-stack bands
(`before_index`/`after_index`/`before_valid`/`after_valid`/`in_aoi`) in `analyze.py`; the prose
line is generated by `backend/messages.py:build_why_explanation` (verb chosen from the actual
coverage direction so the sentence is always honest). `why_explanation` is a supporting line
under the `alert_message` headline, not a replacement for it.
`recommended_actions` (Phase 18b): rule-based decision-support list, keyed on mode + severity,
populated on every response (see the rule table below).

| Mode | Severity | recommended_actions |
|---|---|---|
| ndwi (flood) | severe | "Alert nearby villages / local authorities", "Assess the need for evacuation support", "Monitor river level closely over the next 48h" |
| ndwi (flood) | moderate | "Notify local disaster-management contact", "Monitor for further water-level rise" |
| ndwi (flood) | mild | "Log for routine monitoring", "No immediate action needed" |
| ndvi (crop stress) | severe | "Field inspection recommended", "Check irrigation / pest status", "Consider a yield-loss assessment" |
| ndvi (crop stress) | moderate | "Schedule a field check within the week" |
| ndvi (crop stress) | mild | "Continue routine monitoring" |
| nbr (burn) | severe | "Assess containment status with local fire authority", "Evaluate need for evacuation in surrounding areas" |
| nbr (burn) | moderate | "Monitor for spread", "Notify local forest / fire department" |
| nbr (burn) | mild | "Log for monitoring" |

Lookup lives in `backend/messages.py:RECOMMENDED_ACTIONS` (a plain dict) +
`recommended_actions_for(mode, severity)`; no scoring model. Frontend renders it as a
check-icon checklist (`.action-list` / `.action-item`) directly under the "Why?" line.
`comparison_type` (Phase 13): `"same_season"` (default) or `"year_over_year"`. The frontend
defaults crop-stress (`ndvi`) mode to `year_over_year` (single analysis date; before is
auto-derived one calendar year prior) to avoid the harvest confound, and lets the user opt
into `same_season` with an inline caveat. When `mode == "ndvi"`, `comparison_type ==
"same_season"`, and the before/after gap exceeds 45 days within one calendar year, the
analysis still runs but `caveat` is set: "Same-season comparison over N days may conflate
crop stress with normal harvest/senescence. Year-over-year comparison ... is recommended".
This generalizes the Po Valley fix (Phase 6) so the same mistake is caught for any AOI, not
just presets. A successful live same-season Po Valley run (2022-04-01 → 2022-08-15) returned
the caveat as verified (25.95% "severe" — largely harvest-driven, now flagged).
The classifier score field is `classifier_bootstrap_fit_score` (was `classifier_train_score`
before Phase 10): the RF is scored on its own auto-bootstrapped labels, so the value ~1.0 is
expected and must NOT be presented as held-out model accuracy. `classifier_note` carries the
honest framing; the frontend surfaces it behind a small "Model note" info tooltip on the
result card. `cached` is `true` when the result was served from the on-disk preset cache
(Phase 8).
`confidence` / `confidence_note` (Phase 14): a self-reported high/medium/low grade scored by
`confidence_grade()` in `backend/pipeline/analyze.py` from the per-period clear-sky valid
fractions (reported by the adaptive-window ingestion), the minimum scene count across the
two windows, and whether the request is one of the three validated presets. Validated presets
with adequate coverage get "high"; custom regions are capped lower ("high" only with
strong multi-scene coverage, "medium" for adequate coverage, "low" for limited coverage) and
the note always states when a region is not independently validated. This is a proxy (based
on coverage + validation status), NOT measured accuracy — the frontend surfaces `confidence_note`
in the result card's note panel.
Errors: clean JSON, never stack traces.
- 400 `AOITooLargeError` / `AOITooSmallError` / `AOIError` — AOI too big (>40M px budget),
  too small (<10k px → statistically meaningless), or malformed.
- 422 `NoImageryError` / `ValueError` — no scenes in window, or no detectable change.
- 429 `EEException` — GEE quota / payload errors.
- 503 `GeeUnavailableError` — auth or network failure.
- 504 `GeeTimeoutError` — a GEE round-trip exceeded its hard wall-clock deadline (Phase 19d); GEE slow/unreachable.
- 500 — unexpected; logged server-side.
Severity buckets: mild `<5%` of AOI, moderate `5–20%`, severe `>20%`.

### `GET /watchlist` (Phase 18c)
Returns a **ranked list of already-analyzed results** — it never runs new analyses.
Ranking: the three cached validated presets plus any custom-AOI results run during the
current server session (in-memory list in `backend/watchlist.py`, reset on restart; deduped
by aoi_bounds+dates+mode). Each entry is a **full `/analyze` response object** (so the
frontend can reuse existing result rendering by clicking it) plus two extra fields:
`priority` and `preset_id` (null for custom AOIs).
```json
[
  { "...full AnalyzeResponse...", "priority": "High", "preset_id": "nsw-bushfires-2019" },
  { "...full AnalyzeResponse...", "priority": "Medium", "preset_id": null }
]
```
**Priority-derivation rule:** `priority` is a *direct relabel* of the existing severity
bucket — severe → High, moderate → Medium, mild → Low (`backend/watchlist.py:
PRIORITY_BY_SEVERITY`). It is deliberately NOT a new independent score. Sorting: priority
High → Medium → Low, then `affected_pct` descending within a band. Degrades gracefully with
just the 3 presets if no custom AOI has been run this session (no empty/broken panel).
`watchlist.remember(enriched)` is called in `main.py` for every successful *custom* live run
(presets are covered by the cache files); preset entries are re-enriched at read time
(location/alert/why/actions fresh) and flagged `cached: true`.

## Demo Cache (Phase 8)
The three validated presets are served from an on-disk cache by default so the demo is
near-instant and immune to live GEE / venue-network hiccups.
- **Cache files:** `backend/cache/<preset_id>.json` — full `/analyze` JSON responses
  (including base64 thumbnails), one per preset:
  `kishanganj-flood-2017.json`, `po-valley-drought-2022.json`, `nsw-bushfires-2019.json`.
- **Request flag:** `POST /analyze` accepts `use_cache: bool` (default `true`). When
  `true` and the request exactly matches a known preset (bbox + dates + mode + scale +
  window), the cached JSON is returned immediately (measured ~7–11 ms) with `"cached":
  true` in the body. Custom/map-clicked AOIs never match a preset, so the flag is
  effectively ignored for them (always a live run). The frontend sends `use_cache:
  true` for presets and `false` for custom AOIs.
- **"Run live" path:** any live run of a preset re-saves the cache file automatically
  (`backend/cache.py:save_cached`), so the cache regenerates itself whenever someone
  clicks "Run live from GEE" or the pipeline changes and a fresh run is issued. To force
  a regen by hand: delete the file(s) and re-run, or `curl` with `"use_cache":false`.
- **Honesty:** cached responses are visually identical to live ones except a subtle
  "cached preset result" pill + a "Run live from GEE" button on the result card
  (`AlertCard.jsx`); the body also carries `cached: true`.
- **Caveat:** because a cache file stores the exact response schema at write time, a
  schema change (e.g. Phase 10's field rename) invalidates old cache files — regenerate
  them (the backend's auto-save does this on the next live preset run). The current cache
  files were patched in place through Phase 14 (comparison_type, caveat, confidence,
  confidence_note) so cached reads carry the same fields a fresh live run would emit.

## Known Limitations
- Sentinel-2 revisit is ~5 days (never "real-time"); composites start at a ±6-day median
  window with SCL cloud masking and only widen (to ±10/±14d) when the clear-sky fraction
  is poor, so fast-moving flood peaks can still be partially missed and highly-persistent
  cloud cover may never produce enough clear pixels.
- 10 m native resolution / configurable sampling scale: custom AOIs default to a 20 m
  sampling grid (Phase 19b, 4× fewer pixels for faster live runs); presets always sample at
  10 m (their cache was validated at that grid). Fine feature detail (narrow roads, small
  plots) is not resolved, and sub-resolution partial flooding is undercounted.
- The fusion classifier's bootstrap labels are auto-derived rules, not human ground truth;
  a 100% train score is expected (RF memorizes), but generalization is unverified on new sites.
- NDVI "crop stress" before/after within a single season is confounded by phenology (harvest
  causes NDVI collapse); year-over-year comparisons (same season) isolate drought better.
- JRC permanent-water subtraction removes only pixels that were water 100% of the time
  (1984–present), so recently-wet lakes/irrigation reservoirs can leak into the flood mask.
- GEE `computePixels` is capped at 48 MB/request; large AOIs are tiled (800k px/tile), which
  makes very large AOIs slow (minutes) and hard-capped at 40M pixels.
- The `S2_SR`/`S2_SR_HARMONIZED` collection has a **persistent monsoon-season gap over North
  India** — L1C scenes exist for Jun–Oct but were never processed to surface reflectance, so
  the SR collection returns zero scenes for exactly the window when floods peak (Phase 17
  found this for the 2017 Bihar event; verified across multiple years and regions, not one
  bbox). Monsoon-period flood analysis on this region therefore needs a shifted post-flood
  after-date, or a TOA (L1C) pipeline variant.
- First /analyze request is slow (~30–120 s) because thumbnails are rendered and downloaded
  server-side; subsequent runs share cached GEE session state.

## Edge Case Testing (Phase 9 — observed behavior)
Tested against the running backend on 2026-08-07. All three adversarial cases fail
gracefully with clean JSON (no hangs, no 500s, no stack traces):
1. **Persistently cloudy AOI** (Amazon wet season bbox `[-60.0,-3.1,-59.9,-3.0]`, ndvi,
   2023-01-01 → 2023-02-15, scale 20): HTTP 422 in ~37 s with
   `{"error":"Analysis could not be completed — the AOI likely has no detectable change.",
   "detail":"Only 0 confidently labeled pixels — cannot train the fusion classifier..."}`.
   The Otsu stage had enough finite change samples to pass, but the fusion layer found
   0 confidently labeled pixels → clean ValueError → 422. Not a hang, not a 500.
   **Phase 14 update:** this exact request now returns HTTP 200 (mild, 4.7%) because the
   adaptive windowing (Phase 14a) widened the `after` window 6d → 14d (valid fraction
   0.96, 12 scenes), producing enough clear pixels to run the full pipeline. The 422 path
   above still fires when even a 14d window can't recover clear pixels.
2. **`before_date` after `after_date`**: rejected at pydantic request-validation time,
   HTTP 422 in ~1 ms:
   `{"detail":[{"type":"value_error","loc":["body","after_date"],"msg":"Value error,
   after_date must be strictly after before_date",...}]}`. Enforced in
   `backend/models/schemas.py` `AnalyzeRequest._after_after_before`.
3. **Very small AOI** (~50 m × 50 m box `[68.4,28.1,68.4005,28.1005]`, scale 20):
   **before fix** returned a misleading HTTP 422 "the AOI is likely unchanged" (the
   Otsu ValueError path, 30 change samples). **Fix:** added `MIN_AOI_PIXELS = 10_000`
   guard (`AOITooSmallError`) in `backend/pipeline/analyze.py:_guard_aoi_size`; now
   HTTP 400 in <1 s with a clear message: "AOI of ~0.0027 km² yields only ~7 pixels at
   20m scale — below the 10,000 pixel minimum...". Boundary re-test: a 2,727-px AOI is
   also rejected; AOIs ≥10k px pass the guard. No divide-by-zero possible in quantify
   (guards `total_pixels == 0` internally).

## Robustness Smoke Tests (Phase 14 — observed behavior)
Run on 2026-08-07 against the live backend (custom AOIs → always live, `use_cache:false`).
These are smoke tests of pipeline behavior on *unvalidated* region types — they confirm the
pipeline runs and self-reports honestly, NOT that the numbers are ground-truth accurate:
1. **Cloudy tropical (Amazon wet season)** `[-60.0,-3.1,-59.9,-3.0]`, ndvi, 2023-01-01 →
   2023-02-15, scale 20: HTTP 200 in ~64 s, **mild 4.7%**, confidence medium. The `after`
   composite adaptively widened 6d → 14d (valid 0.96, 12 scenes) to recover clear pixels —
   the same request that hard-failed in Phase 9 now completes.
2. **Arid desert (Sahara, Algeria)** `[5.0,30.0,5.1,30.1]`, ndvi, 2022-01-01 → 2022-07-01,
   scale 20: HTTP 200 in ~71 s, **moderate 7.57%** (bare-sand NDVI noise crossing the Otsu
   threshold — directionally plausible, not a false negative), confidence high (valid 1.00,
   6 scenes both periods).
3. **High-latitude (Finland)** `[24.0,62.0,24.2,62.2]`, ndvi, 2021-06-15 → 2022-06-15,
   scale 20: HTTP 200 in ~93 s, **moderate 17.19%**, confidence high (valid 1.00 / 0.79,
   24 scenes both periods). No crashes from snow/permafrost/atmosphere edge cases.
Also re-confirmed (Phase 14e): the tiny-AOI guard still fires **before** any adaptive
windowing — `[68.4,28.1,68.4005,28.1005]` at scale 20 → HTTP 400 in 0.57 s, so no runaway
retry ladder for statistically meaningless AOIs.

## Validation
Benchmarked against the **August 2017 Bihar floods** — the **Kishanganj flood preset**
(see `sample_events/bihar_floods_2017.md` for the full event write-up). Reported context:
unprecedented monsoon rainfall in Nepal's Himalayan catchment overflowed the Kankai and
Mahananda rivers; **all 7 blocks (100%) of Kishanganj district were severely affected**,
the Kankai flooded for the first time in ~50 years, and Bihar-wide the event (part of the
2017 South Asia floods) hit 19 districts with ~514 deaths and ~17.1M people affected
(Wikipedia/ReliefWeb/Al Jazeera) — cited as regional context, not a target area to match.
**Pipeline output** (mode `ndwi`, before 2017-01-15 → after 2017-11-01, ±6d composites,
20 m scale, 10 m analysis grid):
- **Kishanganj district** bbox `[87.75,25.75,88.15,26.15]`: **23,430 ha = 13.01% new water →
  moderate** (Otsu t=0.223; 2.34M of 18.0M px; confidence high, validated preset). Directionally
  consistent with a district whose entire area was affected at peak, measured two months later
  in the post-flood aftermath.
- **Data caveat (why the dates shifted):** the GEE `S2_SR`/`S2_SR_HARMONIZED` collection has a
  **persistent monsoon-season gap over this region** — L1C scenes exist but were never processed
  to surface reflectance, so Jun–Oct 2017 return zero SR scenes (verified Delhi-wide, not just
  Bihar). The peak-period window (Aug 2017) is therefore not analyzable with the SR-based
  pipeline. The preset uses a dry-winter pre-flood baseline (2017-01-15) vs. the earliest
  clear post-flood composite (2017-11-01) to capture the residual flood/water signal.
Also validated the other two demo modes against real events: **Gospers Mountain NSW 2019–20
bushfires** (`nbr`) → 53.5% of AOI burned (severe), and **Po Valley 2022 drought** (`ndvi`,
year-over-year 2021 vs 2022) → 27.6% vegetation-stress decline (severe). All three presets
are wired into the frontend AOI picker.

## Change Log
- 2026-08-07 Phase 0: Project scaffolded at `~/Documents/YCCEHACK`. Reused the working
  backend pipeline + venv built earlier at `~/hackpreneur-satellite` (GEE auth token and
  dependency set were already verified), relocated the code, symlinked `venv`, added
  `pillow` to requirements (needed by `pipeline/visualization.py`), and created this doc.
  Architecture decision: pipeline modules are pure functions (unit-testable without HTTP);
  the web server is a thin orchestration layer. Decision made per spec; no changes to
  prior architecture required.
- 2026-08-07 Phase 1-3: Verified the relocated GEE pipeline end-to-end on the 2022
  Pakistan flood (Sindh bbox [68.85,26.80,69.05,27.00], ndwi, 2022-05-15 → 2022-09-01).
  Two bugs found and fixed: (1) `computePixels` requests exceeded GEE's 48 MB cap —
  `features.MAX_TILE_PIXELS` lowered 1_250_000 → 800_000 (~32 MB payloads with 10
  float32 bands); (2) the feature stack never produced a `change` band though
  `fusion_classifier.FEATURE_COLUMNS` and `analyze.run_analysis` both referenced it —
  added `change = after - before` to `build_feature_stack` (now pixel-aligned on the
  UTM grid) and removed the redundant recompute in the classifier. Full run OK: 4.4M px,
  Otsu t=0.255, 68.2k changed px, 682 ha (1.55%), severity mild, classifier trained in
  seconds (100k bootstrap labels).
- 2026-08-07 Phase 4: Built `backend/main.py` (FastAPI). Endpoints `/health` (GEE probe)
  and `POST /analyze` (full pipeline). Error handling maps domain exceptions to clean
  JSON (400/422/429/503/500), never stack traces; CORS open to the Vite origin; GEE
  initialized at startup. Key discovery: `getThumbURL` URLs return 404 in an
  unauthenticated browser, so `visualization.py` was rewritten to download thumbnails
  server-side via an authenticated `AuthorizedSession` and return base64 PNG data URLs
  (consistent with the mask overlay). Verified with curl: /health ok; /analyze on a small
  Sindh AOI → HTTP 200 in ~32 s with real embedded thumbnails; invalid AOI/mode/dates all
  return clean 422s. Note: `run_analysis` uses `bands["change"]`, `scale` param is
  honored; test with `python -m backend.tests.run_pipeline`.
- 2026-08-07 Phase 5: Built the React + Vite frontend (dark "mission-control" theme per the
  design-taste-frontend skill: Space Grotesk + JetBrains Mono self-hosted, zinc-blue palette,
  cyan accent, mono data numerals, functional severity colors). Components: `AOIPicker`
  (3 validated presets + map-click custom AOI), `DateRangeSelect`, `MapView` (Leaflet with
  ESRI World Imagery base, before/after segmented toggle, change-mask overlay toggle),
  `AlertCard` + `SeverityBadge`, `api/client.js` (fetch + `/api` Vite proxy → backend).
  Loading overlay, inline error banner, empty state. Verified `npm run build` clean and the
  full chain through the Vite proxy (5173 → 8000 → GEE) returns 200.
- 2026-08-07 Phase 6: Documented the 2022 Pakistan flood in `sample_events/` with UNOSAT/NDMA
  reported figures and ran two validation AOIs (Jacobabad → severe 28.29%; Sindh margin →
  mild 1.55%). Also validated the burn preset (Gospers Mountain 2019–20, nbr → 53.5% severe)
  and reworked the crop-stress preset to a year-over-year 2021-vs-2022 comparison after the
  in-season April→Aug run proved confounded by wheat harvest (27.6% severe, consistent with
  the declared Po Valley drought emergency). Shrunk oversized demo AOIs that exceeded the
  comfortable runtime.
- 2026-08-07 Phase 7: End-to-end polish. Restarted and re-verified backend + Vite dev server;
  frontend builds with zero errors; all three presets return 200 with plausible severe/moderate
  results; error paths (bad AOI/mode/dates) return clean 422s. Added Known Limitations and
  Validation sections above. Stretch goals noted below.
- 2026-08-07 Phase 9: Pre-demo edge case hardening. Ran all three adversarial cases against
  the live backend and recorded actual behavior under "Edge Case Testing": reversed dates
  (422 @ pydantic, 1 ms), fully cloudy Amazon AOI (clean 422 in ~37 s), tiny ~50 m AOI (was
  a misleading 422, now a clean 400 via a new `MIN_AOI_PIXELS = 10_000` guard that rejects
  statistically meaningless AOIs up front with a clear message). No pipeline-module logic
  was refactored; only the pre-flight guard + error mapping changed.
- 2026-08-07 Phase 8: Cached fallback for the three validated presets. New
  `backend/cache.py` (`PRESETS` registry mirroring the frontend, `preset_id_for`,
  `load_cached`, `save_cached`) + `use_cache` request flag (default true) + `cached`
  response field. `/analyze` serves near-instant cached JSON for exact preset matches;
  live preset runs auto-save the cache so it regenerates itself. Frontend sends
  `use_cache` for presets and shows a subtle "cached preset result" pill + "Run live
  from GEE" button on `AlertCard` when `cached` is true. Verified: all three cached
  responses return in 7–11 ms with values identical to the live validation runs
  (28.29% / 27.64% / 53.5% severe). One bug fixed during verification: `save_cached`
  failed on `date` objects from `model_dump()` → added `default=str` JSON fallback.
  Note: NSW preset live run is slow (~5–6 min); Pak/Po ~2.5–4 min.
- 2026-08-07 Phase 10: Guard against classifier overclaiming. Renamed
  `classifier_train_score` → `classifier_bootstrap_fit_score` in the `/analyze`
  response + schemas + tests and added a `classifier_note` field (source constant
  `BOOTSTRAP_SCORE_NOTE` in `fusion_classifier.py`) that honestly frames the ~1.0
  self-fit score as bootstrap-label fit, not held-out accuracy. Frontend surfaces
  the note behind a small "Model note" info tooltip on the result card
  (`AlertCard.jsx`). Cache files were patched in place to the new schema (identical
  numbers; only the field name + constant note changed), so no full re-run was
  needed. Verified cached read returns the renamed field + note in ~29 ms.
- 2026-08-07 Phase 11: Added the offline demo-video reminder at the top of this doc
  ("TODO before demo: record a 30–45s screen-capture of the live end-to-end flow as an
  offline fallback"). Manual step — no code changes.
- 2026-08-07 Phase 12: Frontend visual sanity check via headless Chromium (puppeteer-core
  against the Vite dev server). All three presets render the intended dark mission-control
  theme (probed computed styles: body `#0a0f16`, Space Grotesk, card `#101821`, cyan `#38c8ff`
  primary button — no unstyled/default component output). Each preset showed its correct
  severe result, the subtle "cached preset result" pill + "Run live from GEE" button, and the
  "Model note" tooltip. **One real bug found and fixed:** `onClick={runAnalysis}` (App.jsx)
  passed React's synthetic click event as the new `useCache` argument, so the MouseEvent was
  `JSON.stringify`-ed into the request body → "Converting circular structure to JSON" error
  banner on every run. Fixed with `onClick={() => runAnalysis()}`; re-verified all three
  presets render and error paths (reversed dates 422, tiny AOI 400, bad mode 422) return
  clean JSON through the Vite proxy. Only remaining console noise is a cosmetic
  `/favicon.ico` 404 (no visual impact; left alone per spec).
- 2026-08-07 Phase 13: Crop-mode seasonal guard. Added `comparison_type`
  (`"same_season"` | `"year_over_year"`, default `same_season`) to the `/analyze`
  request and a `caveat` response field. `seasonal_caveat()` in
  `backend/pipeline/analyze.py` returns a warning (not a hard block) when
  `mode == ndvi`, `comparison_type == same_season`, and the within-calendar-year
  gap exceeds 45 days — the Po Valley harvest-confound generalized to any AOI.
  Frontend: crop mode now defaults to a year-over-year flow (one "analysis date",
  before auto-derived one year prior via `frontend/src/lib/dates.js:oneYearBefore`,
  leap-day clamped), with same-season as an explicit toggle that shows an inline
  caveat. `result.caveat` is also rendered as a warning strip on the alert card.
  Verified: caveat helper unit tests pass; a live same-season Po Valley run
  (2022-04-01 → 2022-08-15) returned 200 with `caveat` set (25.95% "severe" —
  largely harvest-driven, now flagged).
- 2026-08-07 Phase 14a: Adaptive compositing window. `backend/pipeline/ingestion.py`
  `median_composite` now returns a 4-tuple `(composite, scene_count, window_used_days,
  valid_fraction)` and widens the ± window on the fly (6d → 10d → 14d, `MIN_VALID_FRACTION
  = 0.3`) when the SCL clear-sky fraction is poor. `analyze.py` threads the actual window
  per period into `build_feature_stack` (clear-fraction feature bands now use each period's
  real window) and logs `composites  before window=…d valid=… scenes=… | after …`. One bug
  caught live: `_valid_fraction` used `bandNames().get(0)` (a server-side string) in
  `image.select()` which requires a band *list* → GEE `Invalid type` error → fixed with
  `bandNames().slice(0,1)`. Effect: the Phase 9 "cloudy Amazon" hard-422 now completes
  (see Edge Case Testing update).
- 2026-08-07 Phase 14b: Confidence score. Added `confidence_grade()` in
  `backend/pipeline/analyze.py` producing `confidence` (high/medium/low) +
  `confidence_note` from per-period valid fractions, min scene count, and validated-preset
  status. Presets with adequate coverage score "high"; custom regions are capped lower and
  the note always flags "not independently validated". The `confidence`/`confidence_note`
  schema fields (added Phase 13) are now populated on every live run. Live check: Po Valley
  custom box → "medium" ("Adequate clear-sky coverage; this custom region is not
  independently validated.").
- 2026-08-07 Phase 14c: Hemisphere check for year-over-year date math. `oneYearBefore`
  unit checks pass for both hemispheres and leap days (NH `2022-08-15→2021-08-15`, SH
  `2022-03-20`, `2022-12-31`, leap `2020-02-29→2019-02-28`, `2024-02-29→2023-02-28`) —
  the fixed calendar offset is hemisphere-agnostic, so no code change was needed.
- 2026-08-07 Phase 14d: Regional robustness smoke tests (see "Robustness Smoke Tests").
  Cloudy tropical / arid desert / high-latitude regions all run cleanly (200s) with honest
  confidence grades; recorded observed behavior, not ground-truth accuracy.
- 2026-08-07 Phase 14e: Re-confirmed the tiny-AOI guard fires before any adaptive retries
  (HTTP 400 in 0.57 s). Cache files patched in place with comparison_type, caveat, and
  confidence/confidence_note (Pak/NSW `same_season`, Po `year_over_year`; all three
  `confidence: high` with the validated-preset note); cached reads verified ~13–26 ms with
  correct fields.
- 2026-08-07 Phase 15: Location naming. New `backend/geocoder.py` — Nominatim reverse-geocoder
  (`reverse_geocode(lat, lon)`, 1 req/s throttle, 5 s timeout, in-memory cache keyed by
  centroid rounded to 2 dp, curated `_display_name` hierarchy: settlement/district/country,
  postcode stripped, adjacent tokens deduped; `NOMINATIM_URL` env override for outage
  simulation). `location_for(aoi_bounds, preset_id)` returns hardcoded `PRESET_LOCATIONS`
  for the three validated presets (never geocoded, no network) and a live geocode otherwise,
  degrading to `(None, None)` on any failure. `AnalyzeResponse` gained `location_name`
  (str|None) and `location_precision` (default `"coordinates"`). `enrich_response` in
  `backend/messages.py` wires location naming into both cached and live `/analyze` responses.
  Verified: presets return hardcoded names; real Nominatim resolved Jacobabad area →
  `"Jacobabad, Jacobabad District, Pakistan"` (city), cached on repeat; failure-sim
  (`NOMINATIM_URL=http://127.0.0.1:9/reverse`) → 200 with `location_name=null`,
  `location_precision=null`, alert fell back to "the selected area", no crash/hang. Cache
  files patched in place with the new fields.
- 2026-08-07 Phase 16: Alert messages. New `backend/messages.py` — `build_alert_message(mode,
  severity, location_name, pct, ha)` returns a per-mode/per-severity natural-language headline
  (flood: "…shows new water coverage…"; crop: "Crop stress detected near …"; burn: "Fire/burn
  damage detected near … — approximately N km² affected…"; mild variants start "No major …"),
  falling back to "the selected area" when `location_name` is None; `build_caveats(caveat,
  classifier_note)` orders the Phase 13 seasonal `caveat` first (when set) then always appends
  `classifier_note`; `enrich_response` populates `location_name`, `location_precision`,
  `alert_message`, and `caveats` on every response. Frontend: map header shows the resolved
  location name + formatted coords (`.map-loc-name`/`.map-loc-coords`), the alert card shows a
  prominent `.alert-message` headline + `.alert-location` (name + coords), and `caveats` render
  as a list of `.caveat-row` banners; note panel now points to "the model note in the caveats
  above" instead of duplicating `classifier_note`. Verified end-to-end in headless Chromium:
  all three presets render header name/coords + matching alert headline + 1 caveat row
  (NSW shows "33.15° S" — Southern-hemisphere coords correct); a map-click custom AOI resolved
  "Shikarpur Taluka, Shikarpur District, Pakistan" (live, no cache badge); the crop-mode
  same-season toggle shows the inline hint and the result card renders 2 caveat rows (seasonal
  warning + classifier note) alongside the alert headline.
- 2026-08-07 Phase 15/16 bugfix: Po Valley cache-matching. The frontend's year-over-year flow
  derives `before_date = oneYearBefore("2022-08-15") = "2021-08-15"`, but `backend/cache.py`
  PRESETS specified the Po Valley before as `"2021-08-01"` → `preset_id_for` returned None and
  the preset button ran a minutes-long live GEE run instead of the instant cache read
  (exactly what headless tests observed: Po "NO RESULT CARD" on timeout). Fixed the PRESETS
  date to `"2021-08-15"` and patched `backend/cache/po-valley-drought-2022.json` to match;
  Pakistan/NSW (same_season, fixed dates) cache matching unaffected. Verified: preset read now
  matches (`preset_id_for(...) → 'po-valley-drought-2022'`) and all three presets render
  instant cached results in headless UI checks.
- 2026-08-07 Phase 17: **Preset swap — Pakistan flood → Bihar/Nepal-border flood (2017)**.
  The validated flood preset was the 2022 Pakistan (Jacobabad/Sindh) event; replaced with the
  **August 2017 Bihar floods** at **Kishanganj district** (borders Nepal), a more relevant event
  for an India-based audience (unprecedented rainfall in Nepal's Himalayan catchment overflowed
  the Kankai/Mahananda rivers into North Bihar). `backend/cache.py` PRESETS +
  `backend/geocoder.py` PRESET_LOCATIONS + `frontend/src/components/AOIPicker.jsx` + the
  standalone `backend/tests/run_pipeline.py` were swapped to `kishanganj-flood-2017`
  (`[87.75,25.75,88.15,26.15]`, ndwi, label "Bihar Floods 2017 (Nepal border)",
  `location_name = "Kishanganj District, Bihar, India"`); the old `pakistan-flood-2022.json`
  cache file was deleted. **Date constraint found live:** GEE `S2_SR`/`S2_SR_HARMONIZED` has a
  persistent monsoon-season gap over this region — Jun–Oct 2017 return *zero* SR scenes even
  though L1C scenes exist (verified Delhi-wide, so not a bbox artifact; both S2_SR collections
  affected). The requested 2017-06-01 → 2017-08-20 (peak) dates are therefore impossible with
  the SR-based pipeline; the preset uses the earliest analyzable post-flood composite:
  **before 2017-01-15 (dry-winter pre-flood baseline, valid 1.00, 6 scenes) → after 2017-11-01
  (post-flood aftermath, valid 1.00, 6 scenes)**. Live run → **23,430 ha = 13.01% new water →
  moderate** (Otsu t=0.223, 2.34M/18.0M px, confidence high), directionally consistent with the
  "all 7 blocks severely affected" reporting two months post-peak. Cache regenerated + verified
  (~12 ms read). Frontend build clean; headless UI checks pass for all three presets.
- 2026-08-07 Phase 17 bugfix: **4× area inflation in `quantify`**. While validating the new
  preset, `aoi_ha` for the 0.4°×0.4° box came out 720,379 ha (true ~178,000 ha) — an exact 4×
  overcount caused by `analyze.py` passing `scale_m=request.scale` (20 m) to
  `quantify.quantify` while the feature stack is sampled on a fixed **10 m UTM grid**
  (`features.DEFAULT_SCALE = 10`). Every scale-20 analysis (i.e. all three presets) reported
  `affected_ha`/`aoi_ha` 4× too large; `affected_pct` and severity (pixel ratios) were always
  correct. Fixed `analyze.py` to pass `scale_m=features.DEFAULT_SCALE`. Patched the Po/NSW cache
  files in place (÷4 on `affected_ha`/`aoi_ha`; `alert_message` regenerates at read time so the
  NSW km² headline self-corrected); regenerated the Kishanganj cache via a fresh live run. New
  verified figures: Kishanganj 23,430 ha/180,095 ha; Po 15,953 ha/57,727 ha (was 63,814/230,909);
  NSW 34,740 ha/64,934 ha, burn headline now ~347 km² (was ~1,390 km²). All cached reads still
  7–12 ms.
- 2026-08-07 Phase 17 frontend fix: **sidebar AOI picker now shows the resolved location name**
  (was stuck on the generic "Custom AOI (click the map)" placeholder). The dropdown in
  `frontend/src/components/AOIPicker.jsx` previously had a static `__custom__` option; it now
  computes a dynamic label from new `analyzing`/`result` props (wired in `App.jsx`):
  - custom AOI + result present → `result.location_name` (e.g. "New South Wales, Australia");
  - custom AOI + result present but geocode failed (Phase 15 fallback) → raw bounds in the same
    format as the coords line below (e.g. "68.52 · 28.15 · 68.62 · 28.25") — never stuck on the
    placeholder and never blank;
  - custom AOI + analysis in flight → "Resolving location…" (maps to `loading`, since geocoding
    happens server-side during `/analyze`; there is no frontend geocode call);
  - otherwise → the original "Custom AOI (click the map)" placeholder.
  Preset options now display each preset's hardcoded location name too (the frontend `region`
  field pre-run, `result.location_name` post-run — same `?? ` precedence as the map header).
  Verified headless in Chromium: all 3 presets show correct names pre- and post-run; custom flow
  updates placeholder → "Resolving location…" → resolved name in order (sidebar text equals the
  map header); and with `NOMINATIM_URL=http://127.0.0.1:9/reverse` the sidebar falls back to the
  coords while header shows "Custom AOI" and the alert uses "the selected area". Raw lat/lon
  bounds line and `npm run build` unchanged/clean.
- 2026-08-07 Phase 18a: **"Why" explainability line**. Added `before_coverage_pct` /
  `after_coverage_pct` (per-date independent signal coverage vs a fixed per-mode threshold;
  see `COVERAGE_THRESHOLDS` in `backend/pipeline/quantify.py`) and `why_explanation`
  (`backend/messages.py:build_why_explanation`, direction-aware verb so the sentence is always
  honest). Coverage is computed in `analyze.py` from the already-sampled feature-stack bands, so
  it adds no extra GEE work. Frontend renders it as a "Why?" line directly under the `alert_message`
  headline (`AlertCard.jsx`). Verified live: NDVI custom run `53.9% → 1.1%` coverage with the
  honest sentence; headless UI check confirms the why-line renders with the "Why?" prefix. Note:
  coverage is the raw per-date signal share, NOT the diff-based severity — e.g. a severe flood
  flagged mostly by NDWI *relative* rise can still show modest absolute water coverage at each date.
- 2026-08-07 Phase 18b: **Recommended actions**. Added `recommended_actions` (list[str]) to every
  `/analyze` response via `backend/messages.py:recommended_actions_for(mode, severity)` — a plain
  dict lookup (`RECOMMENDED_ACTIONS`) keyed on mode → severity, no scoring model. Frontend renders
  the list as a check-icon checklist (`.action-list` / `.action-item`) directly under the "Why?"
  line. Verified live: custom NDVI severe run → ["Field inspection recommended", "Check irrigation
  / pest status", "Consider a yield-loss assessment"].
- 2026-08-07 Phase 18c: **Priority watchlist**. New `GET /watchlist` endpoint
  (`backend/watchlist.py`) that ranks *already-analyzed* results — the 3 cached presets plus any
  custom-AOI results from this server session (in-memory, deduped by aoi_bounds+dates+mode). Each
  entry is a full `/analyze` response + `priority` + `preset_id`; `priority` is a **direct relabel
  of the existing severity bucket** (severe → High, moderate → Medium, mild → Low), sorted High →
  Medium → Low then `affected_pct` descending. `watchlist.remember()` fires in `main.py` on every
  successful custom live run; preset entries are re-enriched at read time and flagged `cached:
  true`. Frontend: `Watchlist.jsx` panel in the sidebar (priority badge reusing severity tones,
  mode icon, location, affected %) — clicking an entry loads that full result onto the main map
  (reuses `AlertCard`/map rendering via `handleWatchlistSelect`, no duplicated render code),
  refreshed after every analysis. Verified headless: 4 entries (3 presets + a session custom run)
  ranked 53.50% High → 45.08% High → 24.31% High → 13.01% Medium; clicking each loads the correct
  result on the map with why-line + actions intact; graceful with just presets (empty-safe panel).
- 2026-08-07 Phase 19: **Glassmorphic bento "mission-control" redesign (visual only)**. Frontend
  layout rebuilt as floating glass panels over a **full-viewport Leaflet map** (`.app` → absolute
  `.map-stage`, `MapView.jsx` untouched). Dark glass system: `rgba(15,23,42,0.78)` + `backdrop-filter:
  blur(12px) saturate(140%)`, uniform `16px` radius, `1px rgba(255,255,255,0.1)` borders, drop
  shadows (`app.css` `--glass-*` vars + `.glass`). Layout: floating top bar (brand + GEE health
  chip), floating left sidebar (16 px margins, sticky run button), floating location chip top-right,
  centered glass error banner, and a **bottom Command Center** bento grid: Box 1 alert headline +
  mono "Why?" line, Box 2 two stacked massive KPIs (affected ha — white, % of AOI — neon cyan,
  `clamp(32px,3.6vw,48px)` Space Grotesk bold tabular), Box 3 recommended-actions checklist with
  hover lift. Caveats now hidden behind a `<details>` "System Notes & Caveats" accordion; the old
  alert-meta row became a mono meta strip (dates / Otsu t / px / confidence / Model note toggle).
  Detection mode + comparison window are **segmented pill toggles** (cyan glow on active); watchlist
  entries are bento cards with a **glowing severity left-border** (`::before` colored via a `--tone`
  var, `box-shadow: 0 0 10px`). Before/after + change-mask controls and the "click map" legend
  repositioned via CSS to the map's top-right / right edge (below the location chip) so the sidebar
  never covers them. Map overlay, before/after slider, change-mask overlay, all state/data logic,
  and the API contract are unchanged. **Bugfix:** `api/client.js` now unwraps pydantic 422 bodies
  (`detail` is an array → join `detail[].msg`); previously the error banner rendered "[object
  Object]" for reversed-date requests. Verified headless (puppeteer-core + system Chromium at
  1440×900, scripts in `/tmp/opencode/verify/`): 25 checks pass (glass computed styles, no panel
  overlaps via bounding-box audit, KPIs don't overflow, caveats accordion opens with the right
  count, watchlist glow + sort, before/after + mask toggle keep 2→1 image layers, map-click custom
  AOI flow, reversed-dates 422 shows the clean message); only console noise remains the known
  cosmetic `/favicon.ico` 404. NOTE: `media/demo-capture.mp4` still shows the pre-Phase-19
  layout — re-record the demo video with the glass UI before the demo (see top reminder).

## Stretch Goals (not yet built)
- Tile-proxy endpoint (`/tiles/{z}/{x}/{y}`) so the map can stream GEE imagery instead of
  embedding full thumbnails (cuts first-run latency and payload size).
- Before/after date-window slider in the UI (multiple composites to trace flood progression).
- Persist runs to disk/DB and serve a run history list.
- Export GeoJSON of the change mask for GIS tools.
- Multi-date anomaly mode for crop stress (NDVI z-score vs. a 5-year baseline) to further
  separate drought from phenology.

- 2026-08-07 Phase 19: **Light Glassmorphic UI & Real-Data Visualization**. 
  Completely overhauled the frontend presentation wrapper without altering core GEE logic or map interactions. 
  - **Aesthetic Shift:** Transitioned from the dark "mission control" theme to a premium light-glassmorphic UI (translucent white panels, `backdrop-filter: blur(16px)`, deep slate typography for high contrast, and refined severity color palettes) inspired by modern spatial intelligence platforms. 
  - **Bento-Box Layout:** Reorganized the floating alert panel into a clean grid separating the headline/explanation, massive KPI numbers, actionable checklists, and system caveats (now cleanly tucked into a collapsible accordion).
  - **Real-Data Graph Integration:** Added a data visualization component to the Alert Card. Instead of dummy data, this graph strictly binds to the existing `before_coverage_pct` and `after_coverage_pct` fields from the `/analyze` response payload, providing a visual representation of the signal shift (Phase 18a explainability data) over the selected time window.


  - 2026-08-07 Phase 21: **UI Legibility & Contrast Fix** (attempted, later reverted). An early high-contrast pass tried `rgba(255,255,255,0.45)`/`0.65` opacities + `brightness(1.4)` in the backdrop-filter + a white `text-shadow` halo. The halo made text look blurry/smudged and the heavy panels killed the liquid-glass look, so on user request everything except the final legibility decisions below was reverted back to the transparent glass stack (0.15/0.25, `blur(24px) saturate(150%)`, no `brightness()` in any backdrop filter).

  - 2026-08-07 Phase 23: **Crisp Black Text (text glow fully removed)**. Final typography state, CSS-only (`frontend/src/styles/app.css`):
  - **Glow eradicated:** all `text-shadow` declarations removed (10 selectors + the `--text-glow` var); verified zero `text-shadow`/`text-glow` remain in `frontend/src`.
  - **Pure black text:** `--muted` and `--faint` (grey text tokens) both set to `#0F172A` so every secondary label reads as ultra-dark slate; severity amber caveat (`#b45309`) and indigo KPI accent (`#4338ca`) intentionally preserved.
  - **Weight armor kept:** inputs 600, field labels 650, panel/box titles 700, KPI labels 650, hints/why-line/btn-note/chart axis/notes/caveats/coords 600, bar labels/notes strong/watchlist % 650.
  - **Result:** typography is crisp and sharp, relying solely on dark-slate-on-brightened-glass contrast — no artificial text borders. Verified headless (13 checks: zero text-shadows across 28 elements, black colors + weights intact, accents preserved); `npm run build` clean. Zero logic files touched.

- 2026-08-08 Phase 19a: **Per-stage timing instrumentation (dev-only)**. New `backend/pipeline/timing.py` (`Timings`, a dict of stage-name → ms with a `timeit` context-manager stopwatch) threaded through `run_analysis`, `render_thumbnails`, and `enrich_response`. The `/analyze` response gains a `timing_ms` dict (see API contract above) recording each stage's wall-clock duration: `aoi_guard`, `ingestion`, `indices` (feature-stack build + computePixels sampling), `change_detection`, `fusion_classifier`, `quantify`, `masking` (always 0 — SCL masking lives inside ingestion composites, JRC water is folded into the sampled stack, so there's no separate GEE round-trip), `geocoding` (filled by `messages.enrich_response`), `thumbnail_before/after/mask`, and `total`. `main.py` logs the breakdown per analysis and re-timestamps `total` to cover the full request. Purpose: diagnose where GEE time actually goes before optimizing. Also made `check_connectivity` run its probe on a worker thread with a hard 8 s timeout so a GEE outage can't wedge `/health` or startup. Schema-compatible: cached reads emit `timing_ms: {"total": <ms>, "cached": 1}` so the frontend contract is uniform.
- 2026-08-08 Phase 19b: **Custom-AOI speedup (default sampling scale 10 m → 20 m, parallel thumbnails, in-memory repeat cache)**. Verified wins: a custom Po-Valley crop run went 237 s → ~41 s live, with the per-stage breakdown showing ingestion+sampling dominating. Three changes: (1) `AnalyzeRequest.scale` default is now **20 m** (was 10); the live custom-AOI path honors it (4× fewer pixels → ~4× faster `computePixels`), while **presets always sample at the fixed 10 m grid** (`features.DEFAULT_SCALE`) that produced their validated cache numbers — `quantify` gets the matching `scale_m` so area math stays exact and cached values are bit-identical. `build_feature_stack`/`sample_feature_stack` now accept a `scale` param. (2) `visualization.render_thumbnails` renders the two true-color downloads **in parallel** on a shared `AuthorizedSession` (`ThreadPoolExecutor`), and preview resolution dropped 768 → 512 px. (3) `main.py` adds an **in-memory response cache** keyed by exact request parameters (AOI+dates+mode+comparison+window+scale) for repeated *custom* requests in the same session (presets already hit the on-disk cache). Verification: all three presets still reproduce their cached numbers exactly (13.01% / 24.31% / 53.5%).
- 2026-08-08 Phase 19c: **Mask cleanup TRIALED AND REVERTED — raw fusion mask is final**. Goal: remove sub-3 px salt noise from the change mask so the overlay looks cleaner. Two approaches were measured against all three presets (one live pass each, RAW numbers verified bit-identical to cache): a 3×3 morphological open+close (`morphological_cleanup`) and a connected-component size filter dropping ≤4 px islands (`remove_small_components`, vectorized via `kept[labels]`, 0.1 s on 18M px). Measured deltas on affected_% (raw → cleaned):

  | Preset | 3×3 open+close | ≤4 px CC filter |
  |---|---|---|
  | Kishanganj 13.01% (moderate) | −2.39 pp | −0.39 pp |
  | Po Valley 24.31% (severe) | −1.19 pp | −0.19 pp |
  | NSW 53.50% (severe) | −4.26 pp | **−0.70 pp** |

  The CC filter is far gentler than morphology (it preserves real region boundaries instead of eroding them) but **NSW still moved −0.70 pp, exceeding the ~0.5 pp acceptance gate** — and any cleanup would desync live runs from the cached headline numbers (e.g. 53.50% cached vs 52.80% live) unless caches were regenerated, which the rules forbid. Decision: **revert to the raw fusion mask** (no cleanup, no contour drawing in `mask_thumbnail`); the only surviving artifact is `mask_cleanup` timing being absent from `timing_ms`. The tuned component filter remains available as `change_detection.remove_small_components` if a future realignment of cache + cleanup is wanted, but today a working plain mask beats a half-tuned cosmetic.
- 2026-08-08 Phase 19a/19b/19c health check (post-revert): `GET /health` → `{"status":"ok","gee_connected":true}`; all 3 cached presets load instantly with their known-correct numbers (13.01% moderate / 24.31% severe / 53.5% severe, ~3–6 ms reads); `npm run build` clean (2.85 s); one live custom-AOI run (Quistello, Mantua, Italy, ndvi year-over-year, scale 20) completed in **41 s** — cached=False, 28.48% severe, full `timing_ms` breakdown present. Backend restarted (setsid, `--reload`) to pick up the revert. **Pipeline is now locked — no further backend changes before the demo.**
- 2026-08-08 Phase 19d: **Fails-fast hardening — a hung GEE request can no longer leave the user stuck forever.** Trigger: a live custom-AOI request stayed on "Analyzing…" ~30 min with no error and no cancel option (a stalled `computePixels`/thumbnail GEE call with the client's absent read timeout). Two independent layers shipped, per fix-priority order:
  - **Frontend (cheapest, highest-value).** `App.jsx` now creates an `AbortController` per `/analyze` run with a **90 s hard cap** (`ANALYZE_TIMEOUT_MS` in `api/client.js`). On timeout or user cancel the fetch rejects with `AbortError` → the loading overlay clears and a friendly banner shows: "This is taking longer than expected (GEE is slow or unreachable). Try again, or use a cached preset." A **visible "Cancel" button** (`.btn-cancel`) now sits on the map loading overlay, so a judge is never trapped mid-run. Verified headless (puppeteer-core, `/tmp/opencode/verify/19d-cancel.mjs`): Cancel button appears while a live run is held open; clicking it clears the overlay and surfaces the friendly message — all 3 checks PASS.
  - **Backend (belt + suspenders).** New `backend/pipeline/gee_timeout.py` — `call_with_timeout(fn, timeout_s, label)` runs a GEE call on a worker thread with a hard wall-clock deadline and raises typed **`GeeTimeoutError` → HTTP 504** (clean JSON, added to `main.py:_ERROR_MESSAGES`, never a hang). Wrapped around the three GEE-bound phases in `run_analysis` (no pipeline-logic changes): `median_composite` (90 s per before/after), `sample_feature_stack` computePixels (120 s), `render_thumbnails` (90 s) — generous headroom over the observed ~41–63 s live budget. The abandoned worker thread is left to die on its own (CPython can't kill threads) but never blocks the request again — same pattern as `check_connectivity`'s timeout.
  - **Retry-loop bound confirmed (no bug found).** The Phase 14a adaptive window ladder is provably bounded: `WIDENING_WINDOWS = (10, 14)` + `MAX_WINDOW_DAYS = 14` → at most 3 composite attempts per period, each now capped at 90 s by the wrapper above, then a clean `NoImageryError` (422). Worst-case backend bound ≈ 90+90+120+90 s ≈ 6.5 min, far below "never"; the frontend 90 s cap means a user sees an error well before that. Attempted windows are logged per period (`attempts=[6,10,14]`) so the ladder is visible in logs.
  - **GEE quota:** no evidence of rate-limit/quota exhaustion — `/health` stays `{"status":"ok","gee_connected":true}` and live runs completed tonight (41 s, 64 s) with zero `EEException` retries in logs. The 429 path is unchanged (Phase 4 `EEException` → 429).
  - **Verification:** all 3 cached presets still load instantly with correct numbers (13.01 / 24.31 / 53.5, ~4–6 ms — cache path untouched by the wrappers); one live custom-AOI run completed under the new ceilings (64 s wall, total 63.4 s, same 28.48% severe result); `npm run build` clean; backend auto-reloaded via `--reload`. Backend pipeline logic remains locked (Phase 19a/19b/19c) — only the new timeout wrapper layer was added.
- 2026-08-08 Phase 19e: **Timeout tuning for elevated GEE latency (see the top-of-doc note for the full diagnosis)**. A previously-working custom AOI (`[68.35,28.05,68.45,28.15]`, ndvi, same-season `2024-05-08 → 2025-05-21`) timed out at 01:42:43. Log forensics: composites fine (valid 1.00 both periods), GEE hit transient **503s** on `computePixels` (client retry recovered), then **thumbnail rendering hit the 90 s backend ceiling → 504**. Zero `EEException`/429/quota lines; direct GEE probe passed (4.8 s) while `/health`'s marginal 8 s probe flapped degraded — so this was **elevated GEE latency (Scenario 2a), not a code bug or quota rejection**, and the request *would have succeeded, just slowly*. Fix (timeout/UX only; locked pipeline logic untouched): frontend `ANALYZE_TIMEOUT_MS` 90 s → **180 s** (with comment), backend `SAMPLE_TIMEOUT_S` 120 s → **180 s** and `THUMBNAIL_TIMEOUT_S` 90 s → **180 s** (`INGESTION_TIMEOUT_S` stays 90 s — composites are fast). Loading overlay now shows a **live elapsed counter** ("Still working… Ns elapsed — large/slow requests can take up to ~3 minutes") driven by a 1 s `setInterval` in `App.jsx` (cleared in `finally`; Cancel button unchanged). Verified: `npm run build` clean (2.79 s); all 3 cached presets read instantly with correct numbers (13.01 moderate / 24.31 severe / 53.5 severe, `cached: true`); `/health` → ok; backend auto-reloaded with the new constants. **Honesty note:** live custom runs may still exceed 180 s under tonight's load — rely on the 3 cached presets for the demo; a true quota rejection would log `EEException` (429), which has never been observed.
- 2026-08-22 Watershed Development Module (Steps 1–7, commits c6298bb → this
  entry): **ground-truth photo ↔ satellite fusion** built per problem statement,
  `/analyze` pipeline untouched. New pieces:
  - `backend/routers/watershed.py` (first APIRouter; main.py gets a 2-line diff)
    — `POST /photos/upload` (multipart; EXIF-GPS guard → AOI containment → disk
    store `data/photos/<id>.jpg` + atomic index.json), `GET /photos`,
    `GET /photos/{id}/file`, `GET /photos/narrative`. Clean JSON errors:
    missing GPS/corrupt → 400, outside AOI → 422.
  - `backend/pipeline/photo_ingest.py`: pure EXIF parsing via Pillow GPS IFD
    (DMS→signed decimal, hemisphere signs, altitude rational); typed errors.
  - `backend/pipeline/photo_classifier.py` + `backend/groq_client.py`: Groq
    vision on `qwen/qwen3.6-27b` (`GROQ_API_KEY`, dotenv-loaded in main.py).
    Provider registry + env override = the pluggable interface. Never raises:
    unconfigured key/malformed/unknown-category all degrade to `unclear` +
    honest note. **Real bug found live:** qwen3.6 reasoning blocks ate the
    token budget (unparseable, then empty replies) → fixed with
    `reasoning_effort="none"` + `/no_think` prompt hint + think-block stripping
    (handles truncated blocks). Vision images downscaled ≤768 px JPEG;
    retry/backoff honours `retry-after`.
  - `backend/pipeline/satellite_sample.py`: NDVI+NDWI AT the photo point from
    the locked ingestion+indices stages (45 m box, ±6 d, hard timeout wrapper);
    returns None when GEE unavailable so uploads never break. Injectable
    composite/index/reducer/geometry seams for offline tests. Source labelled
    "SRISHTI-DRISHTI-equivalent … pending public access" (no public API exists).
  - `backend/pipeline/narrative.py`: field reports from measured facts only
    (counts/interventions/date span/mean NDVI-NDWI with n=); template fallback
    `generated=false`; GET /photos/narrative always 200.
  - Frontend: PhotoLayer markers colour-coded by category (shared CATEGORY_META),
    glass popups binding thumbnail/classification/fusion/badge, PhotoFocus
    auto-fit when no result is on screen (Leaflet culls off-view circles to
    d="M0 0" — fresh uploads were invisible otherwise), PhotoUpload panel
    (AOI-gated file picker, inline clean errors, result card), hand-rolled SVG
    TimelineChart (date vs NDVI, no-index lane), Generate-field-report button
    with provenance line (groq:model vs amber template fallback).
  - `.env` (+ .gitignore) carries GROQ_API_KEY; python-dotenv loaded at main.py
    import. messages.py discrepancy vs build prompt: it contains NO OpenAI call
    (deterministic templates since Phase 16) — nothing to swap; narratives are
    additive for watershed endpoints only, /analyze responses byte-identical.
  - Verified: 38 offline tests green (13 API + 15 classifier/fusion + 10
    narrative); headless UI checks for marker rendering/colors, upload flow
    through real file input (honest unclear @0.95 on synthetic image), live
    satellite fusion values in card, timeline lanes, live Groq report that
    flagged its own sparse sample; all 3 cached presets still read instantly
    with canonical numbers (13.01 moderate / 24.31 severe / 53.5 severe,
    33–53 ms) after the work; npm build clean throughout.
