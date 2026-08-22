import { useRef, useState } from "react";
import {
  Camera,
  CircleNotch,
  WarningCircle,
  CheckCircle,
  Sparkle,
} from "@phosphor-icons/react";
import { uploadPhoto, getNarrative } from "../api/client.js";
import { CATEGORY_META } from "../lib/photoCategories.js";
import { TimelineChart } from "./TimelineChart.jsx";

/**
 * Field-photo upload for the watershed module.
 *
 * Uses the current AOI selection as the containment check (backend rejects
 * photos without GPS EXIF or outside it with clean 400/422s), posts multipart
 * to /photos/upload and shows a compact result card binding the classifier's
 * verdict plus the fused satellite indices. Failures surface as an inline
 * message; neither state blocks the rest of the app.
 */
export function PhotoUpload({ aoi, onUploaded, photos }) {
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [last, setLast] = useState(null);
  const [narrative, setNarrative] = useState(null);
  const [narrBusy, setNarrBusy] = useState(false);

  async function handleFile(file) {
    if (!file) return;
    setError(null);
    setLast(null);
    setBusy(true);
    try {
      const point = await uploadPhoto({ file, aoi });
      setLast(point);
      onUploaded?.(point);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
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

      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/heic"
        hidden
        onChange={(e) => handleFile(e.target.files?.[0])}
      />

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
