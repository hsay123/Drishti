# Sample Event — 2022 Pakistan Monsoon Floods

The benchmark event used to validate the HackPreneur Satellite Change Detector.

## Event
| Field | Value |
| --- | --- |
| Name | 2022 Pakistan monsoon floods |
| Window | Monsoon season, mid-June → mid-October 2022 (peak inundation late August) |
| Type | Riverine + pluvial (rain-driven) flooding |
| Location | Southern/central Pakistan; worst hit: Sindh and Balochistan provinces |
| People affected | ~33 million (NDMA) |
| Reported inundation | ~55,000 km² detected by satellite (UNOSAT, 26 Aug 2022, in ~780,000 km² of cloud-free analyzed area) |
| Reported crop damage | >1.7 million ha of crops damaged/destroyed (NDMA); ~70% of national losses in Sindh |

**Sources:** UNOCHA/ReliefWeb "Revised Pakistan 2022 Floods Response Plan" (Dec 2023);
FloodList (27 Aug 2022) citing UNOSAT VIIRS flood mapping; NDMA situation reports.

## AOI / Parameters
Two analysis runs, both in `ndwi` (flood) mode, before = 2022-05-15 (pre-monsoon dry
baseline), after = 2022-09-01 (post-peak flood extent), ±6-day median composites,
20 m sampling.

### Run 1 — Sindh margin (semi-arid, moderate impact)
- AOI bbox: `[68.85, 26.80, 69.05, 27.00]`
- AOI area: 44,074 ha
- Result: **682 ha (1.55%) flagged as new water — severity `mild`**
- Otsu threshold 0.255; 68,196 changed px of 4,407,396

### Run 2 — Jacobabad district (severely inundated)
- AOI bbox: `[68.30, 28.00, 68.50, 28.20]`
- AOI area: 175,943 ha
- Result: **49,776 ha (28.29%) flagged as new water — severity `severe`**
- Otsu threshold 0.441; 1,244,399 changed px of 4,398,576

## How to reproduce
```
# backend running at http://localhost:8000
curl -X POST http://localhost:8000/analyze -H "Content-Type: application/json" \
  -d '{"aoi":[68.3,28.0,68.5,28.2],"before_date":"2022-05-15",' \
      '"after_date":"2022-09-01","mode":"ndwi","scale":20}'
```
Or select the **Pakistan Flood 2022** preset in the frontend and run change detection.

## Interpretation vs. reported figures
- UNOSAT mapped ~55,000 km² of cumulative flood water nationally at peak; a large share
  was in Sindh. Our pipeline detects **new water only** (JRC permanent rivers/lakes are
  subtracted) and reflects the flood state on the after-date composite (1 Sep), so it is
  a lower bound of total inundation rather than a national total.
- Jacobabad district was among the worst affected in 2022 (large areas submerged for
  weeks). Our per-AOI result — ~28% of the box flagged as new water, rated `severe` —
  is consistent with that reporting.
- The Sindh-margin box (Run 1) is semi-arid farmland on the edge of the flood extent;
  the modest 1.55% / `mild` result reflects genuinely lighter impact there.
- Severity is reported as **% of AOI affected** (mild <5%, moderate 5–20%, severe >20%),
  which makes a 44,000 ha vs 176,000 ha AOI comparison meaningful.
