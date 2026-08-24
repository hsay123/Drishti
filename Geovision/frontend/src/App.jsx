import { useEffect, useRef, useState } from "react";
import { Play, CircleNotch, Broadcast, WarningCircle, Globe, MapPin, X } from "@phosphor-icons/react";
import { AOIPicker, PRESETS } from "./components/AOIPicker.jsx";
import { DateRangeSelect } from "./components/DateRangeSelect.jsx";
import { MapView } from "./components/MapView.jsx";
import { AlertCard } from "./components/AlertCard.jsx";
import { Watchlist } from "./components/Watchlist.jsx";
import { PhotoUpload } from "./components/PhotoUpload.jsx";
import { postAnalyze, getHealth, getWatchlist, getPhotos, ANALYZE_TIMEOUT_MS } from "./api/client.js";
import { oneYearBefore } from "./lib/dates.js";

const DEFAULT_PRESET = PRESETS[0];

export default function App() {
  const [presetId, setPresetId] = useState(DEFAULT_PRESET.id);
  const [mode, setMode] = useState(DEFAULT_PRESET.mode);
  const [aoi, setAoi] = useState(DEFAULT_PRESET.aoi);
  const [before, setBefore] = useState(DEFAULT_PRESET.before_date);
  const [after, setAfter] = useState(DEFAULT_PRESET.after_date);
  const [comparisonType, setComparisonType] = useState(
    DEFAULT_PRESET.mode === "ndvi" ? "year_over_year" : "same_season"
  );

  const [health, setHealth] = useState(null);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState(null);
  const [watchlist, setWatchlist] = useState(null);
  const [photos, setPhotos] = useState([]);
  const abortRef = useRef(null);
  const elapsedRef = useRef(null);

  const [view, setView] = useState("after");
  const [showMask, setShowMask] = useState(true);

  useEffect(() => {
    getHealth().then(setHealth);
    getWatchlist().then(setWatchlist).catch(() => setWatchlist([]));
    refreshPhotos();
  }, []);

  function refreshPhotos() {
    getPhotos().then(setPhotos).catch(() => {});
  }

  function refreshWatchlist() {
    getWatchlist().then(setWatchlist).catch(() => {});
  }

  function selectPreset(id) {
    const p = PRESETS.find((x) => x.id === id);
    if (!p) return;
    setPresetId(p.id);
    setMode(p.mode);
    setComparisonType(p.mode === "ndvi" ? "year_over_year" : "same_season");
    setAoi(p.aoi);
    setBefore(p.before_date);
    setAfter(p.after_date);
    setResult(null);
    setError(null);
  }

  function handleMode(m) {
    setMode(m);
    setComparisonType(m === "ndvi" ? "year_over_year" : "same_season");
    setResult(null);
    setError(null);
  }

  function handleAoiClick(latlng) {
    const d = 0.05;
    setAoi([latlng.lng - d, latlng.lat - d, latlng.lng + d, latlng.lat + d]);
    setPresetId("__custom__");
    setResult(null);
    setError(null);
  }

  function handleAoiReset() {
    setAoi(null);
    setResult(null);
    setError(null);
  }

  function cancelAnalysis() {
    abortRef.current?.abort();
    setLoading(false);
    setError(null);
  }  async function runAnalysis(useCache = true) {
    if (!Array.isArray(aoi) || aoi.length !== 4 || !before || !after) {
      setError("Pick an AOI (preset or map click) and both dates.");
      return;
    }
    setError(null);
    setResult(null);
    setLoading(true);
    setElapsed(0);

    // Hard client-side cap: never leave the user stuck on "Analyzing…" forever
    // (Phase 19d). 180 s sits above the ~60 s live-run budget (and tonight's
    // elevated GEE latency) but far below "never". A 504 from the backend also
    // surfaces via the same catch.
    const controller = new AbortController();
    abortRef.current = controller;
    const startedAt = Date.now();
    const timer = setTimeout(() => controller.abort("timeout"), ANALYZE_TIMEOUT_MS);
    elapsedRef.current = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    try {
      const requestBefore =
        comparisonType === "year_over_year" ? oneYearBefore(after) : before;
      const res = await postAnalyze(
        {
          aoi,
          before_date: requestBefore,
          after_date: after,
          mode,
          comparison_type: comparisonType,
          scale: 20,
          use_cache: presetId !== "__custom__" ? useCache : false,
        },
        controller.signal
      );
      setResult(res);
      setView("after");
      setShowMask(true);
      refreshWatchlist();
    } catch (err) {
      if (err?.name === "AbortError" || controller.signal.aborted) {
        setError(
          "This is taking longer than expected (GEE is slow or unreachable). " +
            "Try again, or use a cached preset."
        );
      } else {
        setError(err.message);
      }
    } finally {
      clearTimeout(timer);
      clearInterval(elapsedRef.current);
      abortRef.current = null;
      setLoading(false);
    }
  }

  function handleWatchlistSelect(entry) {
    setResult(entry);
    setAoi(entry.aoi_bounds ?? entry.aoi ?? null);
    setMode(entry.mode);
    setBefore(entry.before_date);
    setAfter(entry.after_date);
    setComparisonType(entry.comparison_type ?? "same_season");
    setPresetId(entry.preset_id ?? "__custom__");
    setError(null);
    setView("after");
    setShowMask(true);
  }

  return (
    <div className="app">
      <div className="map-stage">
        <MapView
          result={result}
          view={view}
          onViewChange={setView}
          showMask={showMask}
          onToggleMask={setShowMask}
          onAoiClick={handleAoiClick}
          photos={photos}
        />
        {loading && (
          <div className="map-loading">
            <CircleNotch size={26} weight="bold" className="spin" />
            <span>Fetching Sentinel-2 scenes and classifying change…</span>
            <small>
              Still working… {elapsed}s elapsed — large/slow requests can take
              up to ~3 minutes
            </small>
            <button type="button" className="btn-cancel" onClick={cancelAnalysis}>
              <X size={14} weight="bold" />
              Cancel
            </button>
          </div>
        )}
      </div>

      <Header health={health} />

      <MapLocationHeader presetId={presetId} aoi={aoi} result={result} />

      {error && (
        <div className="error-banner" role="alert">
          <WarningCircle size={18} weight="fill" />
          <span>{error}</span>
        </div>
      )}

      <aside className="sidebar glass">
        <div className="sidebar-scroll">
          <section className="panel-section">
            <h2 className="panel-title">Region & signal</h2>
            <AOIPicker
              presetId={presetId}
              onPreset={selectPreset}
              mode={mode}
              onMode={handleMode}
              aoi={aoi}
              onAoiChange={handleAoiReset}
              analyzing={loading}
              result={result}
            />
            <DateRangeSelect
              mode={mode}
              comparisonType={comparisonType}
              before={before}
              after={after}
              onBefore={setBefore}
              onAfter={setAfter}
              onComparisonType={setComparisonType}
            />
          </section>

          <section className="panel-section">
            <h2 className="panel-title">Field photos</h2>
            <PhotoUpload aoi={aoi} onUploaded={refreshPhotos} photos={photos} />
          </section>

          {Array.isArray(watchlist) && watchlist.length > 0 && (
            <section className="panel-section">
              <Watchlist entries={watchlist} onSelect={handleWatchlistSelect} />
            </section>
          )}

          <div className="panel-actions">
            <button
              type="button"
              className="btn-primary"
              onClick={() => runAnalysis()}
              disabled={loading}
            >
              {loading ? (
                <>
                  <CircleNotch size={18} weight="bold" className="spin" />
                  Analyzing…
                </>
              ) : (
                <>
                  <Play size={18} weight="fill" />
                  Run change detection
                </>
              )}
            </button>
            <p className="btn-note">Sentinel-2 median composites · Otsu thresholding · RF fusion</p>
          </div>
        </div>
      </aside>

      {result ? (
        <AlertCard result={result} onRunLive={() => runAnalysis(false)} />
      ) : (
        !loading &&
        !error && (
          <div className="command-center glass">
            <div className="empty-state">
              <Globe size={30} weight="duotone" />
              <h3>No analysis yet</h3>
              <p>
                Choose a region, pick before/after dates and a detection mode,
                then run change detection.
              </p>
            </div>
          </div>
        )
      )}
    </div>
  );
}

function Header({ health }) {
  const ok = health?.status === "ok" && health?.gee_connected;
  return (
    <header className="header">
      <div className="brand">
        <span className="brand-mark">
          <Globe size={20} weight="duotone" />
        </span>
        <div>
          <h1 className="brand-name">GeoVision AI</h1>
          <p className="brand-sub">Satellite Change Detector</p>
        </div>
      </div>
      <div className="header-right">
        <span className={`health-chip ${ok ? "ok" : "bad"}`}>
          <Broadcast size={13} weight="duotone" />
          {ok ? "GEE connected" : "GEE offline"}
        </span>
        <span className="cadence">Sentinel-2 · ~5 day revisit · near-real-time</span>
      </div>
    </header>
  );
}

function MapLocationHeader({ presetId, aoi, result }) {
  const presetRegion =
    presetId !== "__custom__"
      ? PRESETS.find((p) => p.id === presetId)?.region ?? null
      : null;
  const displayBounds = result?.aoi_bounds ?? (Array.isArray(aoi) && aoi.length === 4 ? aoi : null);
  const locName =
    result?.location_name ?? presetRegion ?? (displayBounds ? "Custom AOI" : null);

  return (
    <div className="map-header">
      <MapPin size={15} weight="duotone" className="map-loc-icon" />
      <div className="map-loc">
        <span className="map-loc-name">{locName ?? "Select a region"}</span>
        {displayBounds && (
          <span className="map-loc-coords mono">{fmtCoords(displayBounds)}</span>
        )}
      </div>
    </div>
  );
}

function fmtCoords(bounds) {
  const [w, s, e, n] = bounds;
  const lat = (s + n) / 2;
  const lon = (w + e) / 2;
  const ns = lat >= 0 ? "N" : "S";
  const ew = lon >= 0 ? "E" : "W";
  return `${Math.abs(lat).toFixed(2)}° ${ns}  ·  ${Math.abs(lon).toFixed(2)}° ${ew}`;
}
