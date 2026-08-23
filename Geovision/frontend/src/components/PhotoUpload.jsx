import { useRef, useState } from "react";
import {
  Camera,
  CircleNotch,
  WarningCircle,
  CheckCircle,
  Sparkle,
  DownloadSimple,
} from "@phosphor-icons/react";
import { uploadPhoto, getNarrative, importPhotos } from "../api/client.js";
import { CATEGORY_META } from "../lib/photoCategories.js";
import { STATES, DISTRICTS_BY_STATE } from "../data/india-regions.js";
import { TimelineChart } from "./TimelineChart.jsx";

/**
 * Field-photo upload for the watershed module.
 *
 * Uses the current AOI selection as the containment check (backend rejects
 * photos without GPS EXIF or outside it with clean 400/422s), posts multipart
 * to /photos/upload and shows a compact result card binding the classifier's
 * verdict plus the fused satellite indices. Failures surface as an inline
 * message; neither state blocks the rest of the app.
 *
 * The optional "DRISHTI details" block lets users tag uploads with the
 * SRISHTI-DRISHTI project hierarchy (state → district → block → micro-
 * watershed). Every field is optional — leaving them blank is the default.
 */
export function PhotoUpload({ aoi, onUploaded, photos }) {
  const inputRef = useRef(null);
  const csvInputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState(null);
  const [importError, setImportError] = useState(null);
  const [last, setLast] = useState(null);
  const [importSummary, setImportSummary] = useState(null);
  const [narrative, setNarrative] = useState(null);
  const [narrBusy, setNarrBusy] = useState(false);
  const [context, setContext] = useState({
    state: "",
    district: "",
    block: "",
    micro_watershed_id: "",
  });

  async function handleFile(file) {
    if (!file) return;
    setError(null);
    setLast(null);
    setBusy(true);
    try {
      const point = await uploadPhoto({ file, aoi, context });
      setLast(point);
      onUploaded?.(point);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function handleCsv(file) {
    if (!file) return;
    setImportError(null);
    setImportSummary(null);
    setImporting(true);
    try {
      setImportSummary(await importPhotos({ file }));
      onUploaded?.(); // refresh map markers
    } catch (err) {
      setImportError(err.message);
    } finally {
      setImporting(false);
      if (csvInputRef.current) csvInputRef.current.value = "";
    }
  }

  async function handleNarrative() {
    setNarrBusy(true);
    try {
      setNarrative(await getNarrative());
    } catch (err) {
      setNarrative({
        narrative: null,
        note: err.message,
        generated: false,
        provider: "unavailable",
      });
    } finally {
      setNarrBusy(false);
    }
  }

  return (
    <div className="photo-upload">
      <button
        type="button"
        className="btn-secondary"
        disabled={busy || !aoi}
        onClick={() => inputRef.current?.click()}
        title={aoi ? undefined : "Select an AOI first — photos must fall inside it"}
      >
        {busy ? (
          <>
            <CircleNotch size={16} weight="bold" className="spin" />
            Analyzing photo…
          </>
        ) : (
          <>
            <Camera size={16} weight="duotone" />
            Upload field photo
          </>
        )}
      </button>
      <p className="btn-note">
        Geo-tagged JPG inside the selected AOI · classified + fused with satellite NDVI/NDWI
      </p>

      <details className="drishti-details">
        <summary>DRISHTI details (optional)</summary>
        <div className="drishti-grid">
          <label className="field drishti-field">
            <span className="field-label">State · optional</span>
            <select
              className="input"
              value={context.state}
              onChange={(e) =>
                setContext((c) => ({
                  ...c,
                  state: e.target.value,
                  district: "", // never carry a district across states
                }))
              }
            >
              <option value="">—</option>
              {STATES.map(({ code, name }) => (
                <option key={code} value={code}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label className="field drishti-field">
            <span className="field-label">District · optional</span>
            <select
              className="input"
              value={context.district}
              disabled={!context.state}
              onChange={(e) =>
                setContext((c) => ({ ...c, district: e.target.value }))
              }
            >
              <option value="">—</option>
              {(DISTRICTS_BY_STATE[context.state] ?? []).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          {[
            ["block", "Block"],
            ["micro_watershed_id", "Micro-watershed ID"],
          ].map(([key, label]) => (
            <label key={key} className="field drishti-field">
              <span className="field-label">{label} · optional</span>
              <input
                className="input"
                type="text"
                value={context[key]}
                placeholder="—"
                onChange={(e) =>
                  setContext((c) => ({ ...c, [key]: e.target.value }))
                }
              />
            </label>
          ))}
        </div>
        <p className="btn-note">
          Mirrors DRISHTI's project hierarchy so field data lines up with SRISHTI
          records. Leave blank if you don't have it — never required for upload.
        </p>
      </details>

      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/heic"
        hidden
        onChange={(e) => handleFile(e.target.files?.[0])}
      />

      <button
        type="button"
        className="btn-secondary"
        disabled={importing}
        onClick={() => csvInputRef.current?.click()}
      >
        {importing ? (
          <>
            <CircleNotch size={16} weight="bold" className="spin" />
            Importing…
          </>
        ) : (
          <>
            <DownloadSimple size={16} weight="duotone" />
            Import from DRISHTI-style export
          </>
        )}
      </button>
      <input
        ref={csvInputRef}
        type="file"
        accept=".csv,text/csv"
        hidden
        onChange={(e) => handleCsv(e.target.files?.[0])}
      />

      {importError && (
        <div className="upload-error" role="alert">
          <WarningCircle size={15} weight="fill" />
          <span>{importError}</span>
        </div>
      )}

      {importSummary && <ImportSummaryCard summary={importSummary} />}

      {error && (
        <div className="upload-error" role="alert">
          <WarningCircle size={15} weight="fill" />
          <span>{error}</span>
        </div>
      )}

      {last && !error && <UploadResultCard point={last} />}

      {Array.isArray(photos) && photos.length > 0 && (
        <TimelineChart photos={photos} />
      )}

      <button
        type="button"
        className="btn-secondary"
        disabled={narrBusy}
        onClick={handleNarrative}
      >
        {narrBusy ? (
          <>
            <CircleNotch size={16} weight="bold" className="spin" />
            Writing report…
          </>
        ) : (
          <>
            <Sparkle size={16} weight="duotone" />
            Generate field report
          </>
        )}
      </button>

      {narrative && (
        <div className={`narrative-card ${narrative.generated ? "" : "narrative-fallback"}`}>
          {narrative.narrative && <p className="narrative-text">{narrative.narrative}</p>}
          {!narrative.narrative && narrative.note && (
            <p className="narrative-text">{narrative.note}</p>
          )}
          <span className="narrative-meta mono">
            {narrative.provider}
            {!narrative.generated && " · template fallback"}
          </span>
        </div>
      )}
    </div>
  );
}

/**
 * Per-row result summary for a DRISHTI-style CSV import — every skipped row
 * is shown with its reason; imported points are marked "not analyzed".
 */
function ImportSummaryCard({ summary }) {
  const { total_rows: total, imported_count: okCount, skipped } = summary;
  return (
    <div className="import-summary">
      <div className="is-head">
        <CheckCircle size={15} weight="fill" />
        <strong>
          Imported {okCount} of {total} row{total === 1 ? "" : "s"}
        </strong>
      </div>
      <p className="btn-note">
        Metadata-only import — points are marked "not analyzed" (no photo to
        classify). Data shown is as-supplied by the uploaded file.
      </p>
      {skipped?.length > 0 && (
        <ul className="is-skipped">
          {skipped.map((s) => (
            <li key={s.row_number}>
              <WarningCircle size={13} weight="fill" />
              <span>
                Row {s.row_number}: {s.reason}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function UploadResultCard({ point }) {
  const cls = point.classification;
  const meta = cls ? CATEGORY_META[cls.category] : null;
  const label = meta?.label ?? "Unclassified field photo";
  const sat = point.satellite;

  return (
    <div className="upload-result">
      {point.id && (
        <img className="ur-thumb" src={`/api/photos/${point.id}/file`} alt="Uploaded field photo" />
      )}
      <div className="ur-rows">
        <div className="ur-head">
          <span className="pp-dot" style={{ background: meta?.color ?? "#9aa4b2" }} />
          <strong>{label}</strong>
          {typeof cls?.confidence === "number" && cls.confidence > 0 && (
            <span className="pp-conf mono">{Math.round(cls.confidence * 100)}%</span>
          )}
        </div>
        {cls?.note && <div className="ur-note">{cls.note}</div>}
        {point.intervention_verified && <div className="pp-badge">Intervention verified</div>}
        {sat && sat.ndvi != null ? (
          <div className="pp-sat mono">
            <span>NDVI {sat.ndvi.toFixed(2)}</span>
            <span>NDWI {sat.ndwi != null ? sat.ndwi.toFixed(2) : "—"}</span>
          </div>
        ) : (
          <div className="pp-sat pp-sat-missing">Satellite indices unavailable</div>
        )}
        <div className="ur-ok mono">
          <CheckCircle size={13} weight="fill" />
          Pinned to map at {point.lat.toFixed(4)}, {point.lon.toFixed(4)}
        </div>
      </div>
    </div>
  );
}
