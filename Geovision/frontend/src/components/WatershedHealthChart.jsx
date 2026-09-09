import { useState } from "react";

/**
 * Hand-rolled SVG "watershed health" comparison chart (no chart library),
 * matching the TimelineChart conventions (viewBox, native <title> tooltips).
 *
 * One horizontal health bar (0-100) per priority watershed, sorted strongest
 * first. Clicking a bar toggles a breakdown of the three sub-scores
 * (vegetation / water / interventions) with their provenance labels; rows with
 * no computable score render a distinct "no data" state (dashed, muted) —
 * never a misleading 0-width bar.
 */
const SUB_META = [
  { key: "vegetation", label: "Vegetation" },
  { key: "water", label: "Water" },
  { key: "interventions", label: "Interventions" },
];

const W = 300;
const ROW_H = 34;
const EXPAND_H = 60; // extra height while a breakdown is open
const PAD = 4;
const BAR_W = W - 34; // track width
const UNKNOWN = Symbol("unknown");

function scoreColor(score) {
  const hue = Math.round(110 - (score / 100) * 110); // 110 green → 0 red
  return `hsl(${hue} 62% 42%)`;
}

function prepare(entries) {
  return (entries ?? [])
    .slice()
    .sort((a, b) => {
      const sa = a.overall;
      const sb = b.overall;
      if (sa == null && sb == null) return 0;
      if (sa == null) return 1; // no-data rows sort last
      if (sb == null) return -1;
      return sb - sa;
    });
}

function BarRow({ entry, open, onToggle }) {
  const sub = entry.sub_scores ?? {};
  const noData = entry.overall == null;

  return (
    <g>
      <rect
        x={0}
        y={2}
        width={W - PAD}
        height={ROW_H - 4}
        rx={9}
        className={`health-row-hit ${open ? "is-open" : ""}`}
        onClick={onToggle}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && onToggle()}
        aria-expanded={open}
        aria-label={`${entry.location_name ?? "Custom watershed"}, health ${entry.overall ?? "no data"}`}
      />

      <g className="health-row">
        <text x={10} y={15} className="health-row-label">
          {entry.location_name ?? "Custom watershed"}
        </text>
        <text
          x={W - PAD - 12}
          y={15}
          textAnchor="end"
          className={`health-row-score mono ${noData ? "is-na" : ""}`}
        >
          {noData ? "no data" : entry.overall}
        </text>

        <rect x={10} y={20} width={BAR_W} height={6} rx={3} className="health-bar-track" />
        {!noData ? (
          <rect
            x={10}
            y={20}
            width={Math.max(3, Math.round((entry.overall / 100) * BAR_W))}
            height={6}
            rx={3}
            className="health-bar-fill"
            style={{ fill: scoreColor(entry.overall) }}
          >
            <title>{`${entry.location_name} · health ${entry.overall}/100`}</title>
          </rect>
        ) : (
          <line
            x1={10}
            x2={10 + BAR_W}
            y1={23}
            y2={23}
            className="health-bar-track-no"
            strokeDasharray="2 3"
          >
            <title>{`${entry.location_name} · no health data yet (no satellite or field-signal evidence)`}</title>
          </line>
        )}
        <path
          d={open ? "M 0 3 L 4 8 L 0 13" : "M 0 3 L 4 8 L 0 13"}
          className="health-chevron"
          transform={`translate(${W - PAD - 20} 16) rotate(${open ? 90 : 0}) scale(1)`}
          style={{ transformOrigin: "2px 8px" }}
        />
      </g>

      {open && (
        <g className="health-breakdown">
          {SUB_META.map((s, i) => {
            const v = sub[s.key];
            const source = sub[`${s.key}_source`];
            const y = ROW_H + 12 + i * 17;
            return (
              <g key={s.key} transform="translate(14 0)">
                <text x={0} y={y} className="health-chip-key">
                  {s.label}
                </text>
                <text x={104} y={y} className={`health-chip-value mono ${v == null ? "is-na" : ""}`}>
                  {v == null ? "— no data" : `${v}`}
                </text>
                <text x={138} y={y} className="health-chip-source">
                  {source ?? "no signal"}
                </text>
              </g>
            );
          })}
        </g>
      )}
    </g>
  );
}

export function WatershedHealthChart({ entries }) {
  const [openEntry, setOpenEntry] = useState(UNKNOWN);
  const rows = prepare(entries);
  if (rows.length === 0) return null;

  const openIndex = rows.findIndex((r) => r === openEntry);

  const H = PAD + rows.length * ROW_H + (openIndex >= 0 ? EXPAND_H : 0);

  function toggle(entry) {
    setOpenEntry((cur) => (cur === entry ? UNKNOWN : entry));
  }

  return (
    <div className="health-chart">
      <div className="health-chart-head">
        <h3 className="watchlist-title">
          Watershed health
          <span className="watchlist-count mono">{rows.length}</span>
        </h3>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Watershed health scores">
        {rows.map((e, i) => {
          const open = i === openIndex;
          const dy = PAD + i * ROW_H + (openIndex >= 0 && i > openIndex ? EXPAND_H : 0);
          return (
            <g key={`${e.preset_id ?? "custom"}-${e.location_name ?? i}`} transform={`translate(0 ${dy})`}>
              <BarRow entry={e} open={open} onToggle={() => toggle(e)} />
            </g>
          );
        })}
      </svg>
      <p className="timeline-note health-note">
        0–100 · overall = 40% vegetation · 30% water · 30% interventions
        <span className="health-cta"> · click a bar for signal details</span>
      </p>
    </div>
  );
}