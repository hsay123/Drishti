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
| `POST /photos/upload` | multipart `file` + `aoi` ([w,s,e,n] or GeoJSON) + optional `watershed_id`, `notes`, `auto_classify` (default true). Rejects missing-GPS (400), corrupt images (400), outside-AOI (422) with clean JSON. Returns the full `PhotoPoint` incl. classification + satellite fusion. |
| `GET /photos?watershed_id=` | stored points, newest first |
| `GET /photos/{id}/file` | the image bytes |
| `GET /photos/narrative?watershed_id=` | Groq field report over aggregated stats; always 200 |

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

