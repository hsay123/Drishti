import { WMSTileLayer } from "react-leaflet";
import { GlobeHemisphereWest } from "@phosphor-icons/react";
import { STATES } from "../data/india-regions.js";

/**
 * SRISHTI-alignment spike: togglable watershed-boundary overlay pulled LIVE
 * from NRSC/Bhuvan's documented public OGC service (no auth, no scraping —
 * machine-readable WMS by design).
 *
 * Endpoint verified during the investigation spike:
 *   https://bhuvan-vec2.nrsc.gov.in/bhuvan/wms  (GeoServer, WMS 1.1.1)
 * Layer family found via GetCapabilities: watershed:<STATE>_WS for 24 states.
 * These are NRSC-published watershed boundary layers — NOT live IWMP/SRISHTI
 * project records; treat them as context, not ground truth.
 *
 * Uses Leaflet's native WMS support through react-leaflet's WMSTileLayer
 * (already a project dependency) — no new libraries. Off by default.
 */

const WMS_URL = "https://bhuvan-vec2.nrsc.gov.in/bhuvan/wms";

export function SrishtiWmsLayer({ enabled, stateCode }) {
  if (!enabled) return null;
  return (
    <WMSTileLayer
      url={WMS_URL}
      layers={`watershed:${stateCode}_WS`}
      version="1.1.1"
      format="image/png"
      transparent={true}
      styles=""
      opacity={0.55}
    />
  );
}

/** Floating on/off control + state picker (rendered outside the map panes). */
export function SrishtiToggleControl({ enabled, onEnabled, stateCode, onStateCode }) {
  return (
    <div className="srishti-toggle">
      <label className="mask-toggle">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => onEnabled(e.target.checked)}
        />
        <GlobeHemisphereWest size={14} weight="duotone" />
        Watershed boundaries
      </label>
      <select
        className="srishti-state"
        aria-label="Bhuvan watershed layer state"
        value={stateCode}
        disabled={!enabled}
        onChange={(e) => onStateCode(e.target.value)}
      >
        {STATES.map(({ code, name }) => (
          <option key={code} value={code}>
            {name}
          </option>
        ))}
      </select>
      <span className="srishti-src">Live · NRSC Bhuvan WMS</span>
    </div>
  );
}
