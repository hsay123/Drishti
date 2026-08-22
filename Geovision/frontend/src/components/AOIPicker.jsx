import { MapPin, Waveform, CloudSun, Campfire } from "@phosphor-icons/react";

const PRESETS = [
  {
    id: "kishanganj-flood-2017",
    label: "Bihar Floods 2017 (Nepal border)",
    region: "Kishanganj District, Bihar — Nepal border",
    mode: "ndwi",
    aoi: [87.75, 25.75, 88.15, 26.15],
    before_date: "2017-01-15",
    after_date: "2017-11-01",
  },
  {
    id: "po-valley-drought-2022",
    label: "Po Valley Drought 2022",
    region: "Emilia-Romagna, Italy",
    mode: "ndvi",
    aoi: [10.9, 44.85, 11.15, 45.1],
    before_date: "2021-08-01",
    after_date: "2022-08-15",
  },
  {
    id: "nsw-bushfires-2019",
    label: "NSW Bushfires 2019–20",
    region: "Gospers Mountain, NSW",
    mode: "nbr",
    aoi: [150.55, -33.25, 150.85, -33.05],
    before_date: "2019-09-15",
    after_date: "2020-02-15",
  },
];

const MODES = [
  { id: "ndvi", label: "Crop stress", icon: CloudSun },
  { id: "ndwi", label: "Flood", icon: Waveform },
  { id: "nbr", label: "Burn scar", icon: Campfire },
];

function AOIPicker({ presetId, onPreset, mode, onMode, aoi, onAoiChange, analyzing, result }) {
  const customLabel = (() => {
    if (result) {
      if (result.location_name) return result.location_name;
      if (Array.isArray(aoi) && aoi.length === 4) return fmtBbox(aoi);
      return "Custom AOI";
    }
    if (analyzing) return "Resolving location…";
    return "Custom AOI (click the map)";
  })();

  return (
    <div className="field-stack">
      <div className="field">
        <label className="field-label">Area of interest</label>
        <div className="preset-row">
          <MapPin size={16} weight="duotone" className="field-icon" />
          <select
            className="select"
            value={presetId}
            onChange={(e) => onPreset(e.target.value)}
            aria-label="Preset area of interest"
          >
            <option value="__custom__">{customLabel}</option>
            {PRESETS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.id === presetId && result?.location_name
                  ? result.location_name
                  : p.region}
              </option>
            ))}
          </select>
        </div>
        <div className="aoi-coords">
          <span className="mono">{fmtBbox(aoi)}</span>
          <span className="hint">Click the map to draw a ~0.1° box</span>
        </div>
      </div>

      <div className="field">
        <label className="field-label">Detection mode</label>
        <div className="mode-grid">
          {MODES.map((m) => {
            const Icon = m.icon;
            const active = mode === m.id;
            return (
              <button
                key={m.id}
                type="button"
                className={`mode-card ${active ? "is-active" : ""}`}
                onClick={() => onMode(m.id)}
                aria-pressed={active}
              >
                <Icon size={18} weight={active ? "duotone" : "regular"} />
                <span>{m.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      <button
        type="button"
        className="btn-aoi-custom"
        onClick={() => onAoiChange(null)}
      >
        Reset AOI
      </button>
    </div>
  );
}

function fmtBbox(aoi) {
  if (!Array.isArray(aoi) || aoi.length !== 4) return "—";
  return aoi.map((v) => v.toFixed(2)).join(" · ");
}

export { AOIPicker, PRESETS, MODES };
