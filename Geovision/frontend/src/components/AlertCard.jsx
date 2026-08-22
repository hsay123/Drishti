import { useState } from "react";
import {
  Warning,
  Siren,
  Plant,
  Drop,
  Flame,
  Scan,
  CalendarBlank,
  ClockCounterClockwise,
  Lightning,
  Info,
  MapPin,
  Lightbulb,
  Check,
} from "@phosphor-icons/react";
import { SeverityBadge, SEVERITY_META } from "./SeverityBadge.jsx";
import { CoverageChart } from "./CoverageChart.jsx";

const MODE_META = {
  ndvi: { label: "Crop stress", icon: Plant },
  ndwi: { label: "Flood", icon: Drop },
  nbr: { label: "Burn scar", icon: Flame },
};

function AlertCard({ result, onRunLive }) {
  const [showNote, setShowNote] = useState(false);
  if (!result) return null;
  const { severity, affected_ha, affected_pct, mode } = result;
  const modeMeta = MODE_META[mode] ?? { label: mode, icon: Scan };
  const ModeIcon = modeMeta.icon;
  const meta = SEVERITY_META[severity] ?? {};
  const locationName = result.location_name ?? "Custom AOI";
  const caveats = Array.isArray(result.caveats) ? result.caveats : [];
  const actions = Array.isArray(result.recommended_actions)
    ? result.recommended_actions
    : [];

  return (
    <section className="command-center glass">
      <header className="cc-head">
        <div className="cc-head-main">
          <span className={`alert-marker marker-${severity}`}>
            <Siren size={18} weight="fill" />
          </span>
          <div className="cc-id">
            <div className="cc-kicker">
              <ModeIcon size={13} weight="duotone" />
              {modeMeta.label} · Change alert
            </div>
            <div className="cc-location">
              <MapPin size={12} weight="duotone" />
              <span className="cc-loc-name">{locationName}</span>
              {result.aoi_bounds && (
                <span className="cc-loc-coords mono">{fmtCoords(result.aoi_bounds)}</span>
              )}
            </div>
          </div>
        </div>
        <div className="cc-head-side">
          <SeverityBadge severity={severity} />
          {result.confidence && (
            <span className={`confidence-chip confidence-${result.confidence}`}>
              <span className="confidence-dot" />
              {confidenceLabel(result.confidence)}
            </span>
          )}
          {result.cached && (
            <div className="cached-row">
              <span className="cached-badge">
                <ClockCounterClockwise size={13} weight="duotone" />
                cached preset result
              </span>
              <button type="button" className="btn-live" onClick={onRunLive}>
                <Lightning size={13} weight="fill" />
                Run live from GEE
              </button>
            </div>
          )}
        </div>
      </header>

      <div className="cc-grid">
        <div className="cc-box cc-headline">
          {result.alert_message && (
            <p className="alert-message" role="status">
              {result.alert_message}
            </p>
          )}
          {result.why_explanation && (
            <p className="why-line" role="note">
              <span className="why-label mono">
                <Lightbulb size={12} weight="duotone" />
                Why?
              </span>
              <span>{result.why_explanation}</span>
            </p>
          )}
        </div>

        <div className="cc-box cc-kpi">
          <div className="kpi">
            <div className="kpi-num-row">
              <span className="kpi-value">{affected_ha.toLocaleString()}</span>
              <span className="kpi-suffix">ha</span>
            </div>
            <span className="kpi-label">affected area</span>
          </div>
          <div className="kpi-divider" aria-hidden="true" />
          <div className="kpi">
            <div className="kpi-num-row">
              <span className="kpi-value kpi-accent">{affected_pct.toFixed(2)}%</span>
            </div>
            <span className="kpi-label">of monitored AOI</span>
          </div>
        </div>

        <div className="cc-box cc-chart">
          <h4 className="cc-box-title">Signal coverage</h4>
          <CoverageChart
            before={result.before_coverage_pct}
            after={result.after_coverage_pct}
          />
        </div>

        <div className="cc-box cc-actions">
          <h4 className="cc-box-title">Recommended actions</h4>
          {actions.length > 0 && (
            <ul className="action-list">
              {actions.map((a, i) => (
                <li className="action-item" key={i}>
                  <Check size={13} weight="bold" />
                  <span>{a}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="cc-meta">
        <span className="meta-item">
          <CalendarBlank size={13} weight="duotone" />
          {result.before_date} → {result.after_date}
        </span>
        <span className="meta-item">
          <Scan size={13} weight="duotone" />
          Otsu t = {result.otsu_threshold}
        </span>
        <span className="meta-item px-count">
          {result.changed_pixels.toLocaleString()} / {result.total_pixels.toLocaleString()} px
        </span>
        <span className="meta-item">
          <button
            type="button"
            className="note-toggle"
            onClick={() => setShowNote(!showNote)}
            aria-expanded={showNote}
          >
            <Info size={13} weight="duotone" />
            Model note
          </button>
        </span>
      </div>

      {showNote && (
        <div className="note-panel">
          {result.confidence_note && (
            <p className="classifier-note">
              <strong>Confidence basis:</strong> {result.confidence_note}
            </p>
          )}
          {result.classifier_bootstrap_fit_score != null && (
            <p className="classifier-note">
              <strong>Fusion layer:</strong> trained on{" "}
              {result.classifier_labeled_pixels.toLocaleString()} auto-bootstrapped
              labels (self-fit {result.classifier_bootstrap_fit_score.toFixed(2)}).
              Not held-out accuracy — see the model note in the caveats below.
            </p>
          )}
        </div>
      )}

      {caveats.length > 0 && (
        <details className="cc-notes">
          <summary>
            <Warning size={14} weight="fill" />
            System Notes &amp; Caveats
            <span className="cc-notes-count mono">{caveats.length}</span>
          </summary>
          <div className="caveat-list">
            {caveats.map((c, i) => (
              <div className="caveat-row" role="note" key={i}>
                <Warning size={14} weight="fill" />
                <span>{c}</span>
              </div>
            ))}
          </div>
        </details>
      )}

      <p className={`alert-foot foot-${severity}`}>
        {footCopy(severity, meta.label, affected_pct)}
      </p>
    </section>
  );
}

function footCopy(severity, label, pct) {
  switch (severity) {
    case "severe":
      return `Over 20% of the AOI is flagged — a large-scale event. Field teams should be notified immediately.`;
    case "moderate":
      return `Between 5% and 20% of the AOI is flagged — localized impact confirmed. Monitor with the next revisit.`;
    default:
      return `Under 5% of the AOI is flagged — a low-intensity signal. No immediate response triggered.`;
  }
}

function confidenceLabel(c) {
  switch (c) {
    case "high":
      return "High";
    case "low":
      return "Low";
    default:
      return "Medium";
  }
}

function fmtCoords(bounds) {
  const [w, s, e, n] = bounds;
  const lat = (s + n) / 2;
  const lon = (w + e) / 2;
  const ns = lat >= 0 ? "N" : "S";
  const ew = lon >= 0 ? "E" : "W";
  return `${Math.abs(lat).toFixed(2)}° ${ns}  ·  ${Math.abs(lon).toFixed(2)}° ${ew}`;
}

export { AlertCard };
