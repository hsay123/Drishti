import { CircleMarker, Popup, Tooltip } from "react-leaflet";
import { CATEGORY_META } from "../lib/photoCategories.js";

/**
 * Ground-truth photo points for the watershed module.
 *
 * Each photo is a color-coded dot at its GPS location; the popup binds the
 * full /photos payload — thumbnail (served by the backend from data/photos/),
 * Groq classification, fused satellite NDVI/NDWI and capture date. Points
 * without classification render gray with an "unclear" label so a missing
 * classifier never hides ground truth.
 */
export function PhotoLayer({ photos }) {
  if (!Array.isArray(photos) || photos.length === 0) return null;
  return (
    <>
      {photos.map((p) => (
        <PhotoMarker key={p.id} photo={p} />
      ))}
    </>
  );
}

function PhotoMarker({ photo }) {
  const meta = categoryMeta(photo);
  const cls = photo.classification;
  const sat = photo.satellite;

  return (
    <CircleMarker
      center={[photo.lat, photo.lon]}
      radius={7}
      pathOptions={{
        color: "#ffffff",
        weight: 1.5,
        fillColor: meta.color,
        fillOpacity: 0.95,
      }}
    >
      <Tooltip direction="top" offset={[0, -6]} opacity={1}>
        <span className="photo-tooltip">
          {meta.label}
          {cls?.structure_type && cls.structure_type !== "other"
            ? ` · ${prettyStructure(cls.structure_type)}`
            : ""}
        </span>
      </Tooltip>
      <Popup className="photo-popup">
        <div className="pp-card">
          {photo.id && (
            <img
              className="pp-thumb"
              src={`/api/photos/${photo.id}/file`}
              alt={`Field photo ${photo.id}`}
            />
          )}
          <div className="pp-head">
            <span className="pp-dot" style={{ background: meta.color }} />
            <strong>{meta.label}</strong>
            {typeof cls?.confidence === "number" && (
              <span className="pp-conf mono">{Math.round(cls.confidence * 100)}%</span>
            )}
          </div>
          {cls?.structure_type && cls.structure_type !== "other" && (
            <div className="pp-row">Structure · {prettyStructure(cls.structure_type)}</div>
          )}
          <div className="pp-row mono">
            {fmtLatLon(photo.lat, photo.lon)}
          </div>
          {photo.captured_at && (
            <div className="pp-row mono">Captured {photo.captured_at.slice(0, 16).replace("T", " ")}</div>
          )}
          {sat && (sat.ndvi != null || sat.ndwi != null) ? (
            <div className="pp-sat">
              <span>NDVI {fmtIdx(sat.ndvi)}</span>
              <span>NDWI {fmtIdx(sat.ndwi)}</span>
            </div>
          ) : (
            <div className="pp-sat pp-sat-missing">Satellite indices unavailable</div>
          )}
          {photo.intervention_verified && (
            <div className="pp-badge">Intervention verified</div>
          )}
          {photo.notes && <div className="pp-notes">{photo.notes}</div>}
        </div>
      </Popup>
    </CircleMarker>
  );
}

function categoryMeta(photo) {
  const key = photo.classification?.category ?? null;
  const meta = CATEGORY_META[key];
  if (!meta) {
    return { color: "#9aa4b2", label: "Unclassified field photo" };
  }
  return meta;
}

function prettyStructure(s) {
  return String(s).replaceAll("_", " ");
}

function fmtIdx(v) {
  if (v == null) return "—";
  return v.toFixed(2);
}

function fmtLatLon(lat, lon) {
  const ns = lat >= 0 ? "N" : "S";
  const ew = lon >= 0 ? "E" : "W";
  return `${Math.abs(lat).toFixed(4)}° ${ns} · ${Math.abs(lon).toFixed(4)}° ${ew}`;
}
