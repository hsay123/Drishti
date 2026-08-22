# Sample Event — August 2017 Bihar Floods (Nepal border)

The benchmark flood event for the HackPreneur Satellite Change Detector preset
**"Bihar Floods 2017 (Nepal border)"** — an India-relevant event whose rainfall
originated in Nepal's Himalayan catchment and overflowed into North Bihar.

## Event
| Field | Value |
| --- | --- |
| Name | August 2017 Bihar floods (part of the 2017 South Asia floods) |
| Window | Unprecedented monsoon rainfall peaked mid–late August 2017; flood waters receded over Sep–Oct |
| Type | Riverine flood — overflow of Nepali-origin rivers (Mahananda, Kankai, Kosi, Gandak) into North Bihar |
| Location | Kishanganj district (borders Nepal), North Bihar, India |
| Impact (Kishanganj) | **All 7 blocks (100%) of the district severely affected**; the Kankai river flooded for the first time in ~50 years |
| Impact (Bihar-wide) | 19 districts affected, ~514 deaths, ~17.1 million people affected (regional context only — our AOI is the single district) |

**Sources:** Wikipedia "2017 South Asia floods"; ReliefWeb; Al Jazeera reporting on the
Bihar floods and the Nepal-catchment monsoon that triggered them.

## AOI / Parameters
`ndwi` (flood) mode. AOI bbox `[87.75, 25.75, 88.15, 26.15]` (Kishanganj district,
borders Nepal). ±6-day median composites, 20 m request scale, 10 m analysis grid.

**Why the dates shifted from the event peak:** the GEE `S2_SR` / `S2_SR_HARMONIZED`
collection has a **persistent monsoon-season gap over this region** — Sentinel-2 L1C
scenes exist for Jun–Oct 2017 but were never processed to surface reflectance, so the
SR collection returns **zero scenes** for the whole peak window (verified across North
India, not just this bbox). The preset therefore uses:
- **before 2017-01-15** — dry-winter pre-flood baseline (valid fraction 1.00, 6 scenes)
- **after 2017-11-01** — earliest clear post-flood composite (valid fraction 1.00, 6 scenes)

This captures the residual flood / elevated-seasonal-water signal ~2.5 months after the
peak, which is the closest analyzable proxy for the event with the SR-based pipeline.

## Result (validated preset, cached)
| Field | Value |
| --- | --- |
| Affected area | **23,430 ha** (234.3 km²) |
| Share of AOI | **13.01%** new water |
| Severity | **moderate** (5–20% bucket) |
| AOI area | 180,095 ha (bbox ~178,000 ha geometric) |
| Otsu threshold | 0.223 |
| Changed / total px | 2,343,024 / 18,009,480 |
| Scenes | 6 before / 6 after |
| Confidence | high (validated preset) |

## How to reproduce
```
# backend running at http://localhost:8000
curl -X POST http://localhost:8000/analyze -H "Content-Type: application/json" \
  -d '{"aoi":[87.75,25.75,88.15,26.15],"before_date":"2017-01-15",' \
      '"after_date":"2017-11-01","mode":"ndwi","scale":20,"use_cache":true}'
```
Or select the **Bihar Floods 2017 (Nepal border)** preset in the frontend and run.

## Interpretation vs. reported figures
- The district's **entire area (all 7 blocks) was affected at peak** (Aug 2017); our
  analysis is measured on the Nov 2017 aftermath composite, two months after the peak,
  so the 13.01% figure is a **residual** water signal, not peak inundation — a lower
  bound, directionally consistent with a district fully engulfed weeks earlier.
- The Mahananda/Kankai floodplains and waterlogged lowland fields retain water well
  into the post-monsoon season, so elevated NDWI in Nov reflects both flood aftermath
  and the normal seasonal high-water state of the river system vs. the dry-winter
  baseline.
- Bihar-wide reported figures (19 districts, ~514 deaths, ~17.1M affected) are cited as
  **regional context**, not a target to match — this AOI covers only one district.
