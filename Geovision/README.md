# GeoVision AI — Satellite Change Detection + Watershed Monitor

Full-stack MVP for rapid satellite change detection (flood / crop-stress / burn)
plus a **watershed development module** that fuses geo-tagged ground photos with
the same Sentinel-2 pipeline — field truth meets pixel truth.

## Two modules, one stack

### 1. Change detection (`/analyze`)
Sentinel-2 median composites → NDVI/NDWI/NBR differencing → Otsu adaptive
threshold → Random-forest fusion refinement → hectares + severity + alerts,
rendered on a Leaflet before/after map. Three validated presets are served from
an on-disk cache for instant demos (Bihar floods 2017 · Po Valley drought 2022 ·
NSW bushfires 2019). See `CLAUDE.md` for the full phase-by-phase log.

### 2. Watershed monitor (`/photos/*`, new)
Ground-truth photo ingestion and fusion:

```
geo-tagged photo upload (multipart)
        │  EXIF GPS parse → AOI containment guard
        ▼
[photo_classifier]  Groq vision (qwen/qwen3.6-27b) → category +
        │           structure type + confidence; degrades to "unclear",
        │           never fails the upload
        ▼
[satellite_sample]  NDVI/NDWI AT THE PHOTO POINT via the existing
        │           ingestion+indices stages (45 m box, ±6 d window,
        │           hard timeout); returns None when GEE is unavailable
        ▼
[photo_store]       data/photos/<id>.jpg + index.json (atomic writes)
        ▼
[map layer]         color-coded markers + rich popups (thumbnail,
        │           classification, fused indices, intervention badge)
        ├─ [TimelineChart]   hand-rolled SVG: date vs NDVI, colour = class
        └─ [narrative]       Groq writes a field report from measured facts
                            only; deterministic template fallback otherwise
```

**Data-source honesty:** SRISHTI-DRISHTI has no public API at build time, so
point sampling uses SRISHTI-DRISHTI-*equivalent* open data through Google Earth
Engine and labels itself accordingly in every response
(`SatelliteSample.source`). The sampler's composite/index/reducer callables are
injectable seams — swapping in the real API later touches one module.

### SRISHTI-DRISHTI alignment

SRISHTI (NRSC's Bhuvan-based IWMP portal) is an inventory/locator for watershed
projects; DRISHTI is its companion field-collection app. Our data model mirrors
**DRISHTI's project hierarchy — state → district → block → gram panchayat →
micro-watershed (`micro_watershed_id`) → `project_year`** (IWMP-style year
ranges such as `"2014-15"`) — so field data collected via DRISHTI could be
ingested with a thin adapter. All of these fields are optional/nullable on
every photo: a casual upload fills none of them, nothing is fabricated, and any
value shown in the UI that was hand-entered or imported from a sample file is
labelled as such (never implied to be live government data). There is **no
scraping or automated interaction with the live bhuvan-app1.nrsc.gov.in
portal** — alignment is schema-level plus a structured CSV import path only.

**Bulk import:** the frontend's "Import from DRISHTI-style export" button posts
a CSV to `POST /photos/import`; rows flow through the same schema, storage,
and satellite fusion as single uploads (marked `not_analyzed` since no image
is attached). A DRISHTI export could be ingested with at most a thin column
mapping — no scraping of or login to the SRISHTI portal anywhere in the code.

### Bhuvan WMS overlay (shipped)

Investigation spike result: NRSC/Bhuvan's **documented OGC services are live
and public**. `https://bhuvan5.nrsc.gov.in/bhuvan/wms` (the URL we started
from) no longer resolves; NRSC's own wiki/Thematic-Services pages document the
current GeoServer endpoints instead. Unauthenticated `GetCapabilities` +
`GetMap` verified against `https://bhuvan-vec2.nrsc.gov.in/bhuvan/wms`
(WMS 1.1.1), which exposes a dedicated `watershed:` workspace with per-state
watershed-boundary layers (`watershed:BR_WS`, …24 states) plus LULC 50K /
wasteland thematic layers. We shipped a **togglable map overlay**
(`SrishtiLayerToggle.jsx`, off by default, state picker, labelled "Live · NRSC
Bhuvan WMS") using react-leaflet's `WMSTileLayer` — no new libraries, no
credentials, no scraping of app HTML. Honesty note: these layers are
NRSC-published watershed *boundaries* used as map context; they are NOT live
IWMP/SRISHTI project records, and nothing in this module claims otherwise.

## Quickstart

```bash
# backend (Python 3.13)
source venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env            # then fill in keys (see below)
uvicorn backend.main:app --reload --port 8000

# frontend
cd frontend && npm install && npm run dev    # http://localhost:5173

# tests (offline; no GEE/Groq/network needed)
python -m pytest backend/tests/ -q
```

`.env` keys: Google Earth Engine credentials as per `earthengine authenticate`,
plus `GROQ_API_KEY=` (free tier, https://console.groq.com/keys) for photo
classification + narratives. Optional overrides: `GROQ_MODEL`,
`PHOTO_CLASSIFIER_PROVIDER`. `.env` is gitignored.

Without a Groq key everything still works: uploads succeed, classifications
come back `unclear` with an explanatory note, narratives fall back to a
deterministic template (`generated=false`).

## Watershed API

| Endpoint | Purpose |
|---|---|
| `POST /photos/upload` | multipart `file` + `aoi` ([w,s,e,n] or GeoJSON) + optional `watershed_id`, `notes`, `auto_classify` (default true) + optional DRISHTI-hierarchy fields (`state`, `district`, `block`, `gram_panchayat`, `micro_watershed_id`, `project_year` — all nullable). Rejects missing-GPS (400), corrupt images (400), outside-AOI (422) with clean JSON. Returns the full `PhotoPoint` incl. classification + satellite fusion. |
| `GET /photos?watershed_id=` | stored points, newest first |
| `GET /photos/{id}/file` | the image bytes |
| `GET /photos/narrative?watershed_id=` | Groq field report over aggregated stats; always 200 |
| `POST /photos/import` | multipart CSV shaped like a DRISHTI field-data export (`photo_ref`, `latitude`, `longitude`, optional `timestamp` + hierarchy columns + pre-existing `classification` text). Per-row validation: bad rows are skipped individually with reasons; only an unparseable file fails (400). Optional `aoi` form field skips outside-rows instead of rejecting. Imported points carry `classification="not_analyzed"` (no image to classify) and satellite fusion when GEE is up. Sample fixture: `backend/tests/fixtures/drishti_sample_import.csv` (clearly fake demo data). |

Errors never leak stack traces; classifier/fusion failures become null fields
on an otherwise-successful upload (`intervention_verified` is set only when the
classifier says `conservation_structure`).

## Design rules honoured

- `/analyze` pipeline untouched — watershed work lives in `backend/routers/`,
  `backend/pipeline/{photo_ingest,photo_classifier,satellite_sample,narrative}.py`
  and additive frontend components.
- Pluggable providers: `photo_classifier.PROVIDERS` registry (+ env override),
  injectable data-source seams in `satellite_sample`.
- Free-tier friendly: vision images auto-downscaled to ≤768 px JPEG, reasoning
  disabled on qwen3 (`reasoning_effort="none"`) so answers aren't eaten by
  `<think>` blocks, retry/backoff honours `retry-after` on 429/5xx.
- Honest AI: bootstrap-fit scores are framed as such, coverage vs severity kept
  distinct, model notes surfaced in tooltips, template fallbacks labelled.
