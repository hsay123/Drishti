import { CATEGORY_META } from "../lib/photoCategories.js";

/**
 * Hand-rolled SVG timeline of field photos (no chart library).
 *
 * X axis: capture date (upload date fallback). Y axis: fused satellite NDVI
 * clamped to [-1, 1] with a zero line — dot color is the classification
 * category. Photos without satellite values sit in a bottom "no index" lane
 * rather than vanishing, so ground truth stays visible when GEE is down.
 * Native <title> elements provide hover tooltips without extra machinery.
 */
export function TimelineChart({ photos }) {
  const points = prepare(photos);
  if (points.length === 0) return null;

  const W = 300;
  const H = 150;
  const M = { top: 10, right: 12, bottom: 22, left: 30 };
  const iw = W - M.left - M.right;
  const ih = H - M.top - M.bottom;
  const noDataH = 14;

  const t0 = points[0].t;
  const tN = points[points.length - 1].t;
  const span = Math.max(tN - t0, 1);

  const y = (v) => {
    const c = Math.max(-1, Math.min(1, v));
    return M.top + ((1 - c) / 2) * ih; // top = +1, bottom = -1
  };
  // The main plot area shrinks to leave the "no index" lane at the bottom.
  const plotBottom = M.top + ih - noDataH;
  const yClamped = (v) =>
    Math.min(plotBottom, Math.max(M.top, y(v)));
  const x = (t) => M.left + ((t - t0) / span) * iw;

  const zeroY = y(0);
  const fmtDate = (d) =>
    d.toLocaleDateString(undefined, { day: "numeric", month: "short" });

  return (
    <div className="timeline-chart">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label="Field photos over time, positioned by satellite NDVI"
      >
        {/* NDVI gridlines */}
        {[1, 0.5, 0, -0.5, -1].map((v) => (
          <g key={v}>
            <line
              x1={M.left}
              x2={W - M.right}
              y1={yClamped(v)}
              y2={yClamped(v)}
              className="tl-grid"
              opacity={v === 0 ? 1 : 0.45}
            />
            {(v === 1 || v === 0 || v === -1) && (
              <text x={M.left - 4} y={yClamped(v) + 3} className="tl-ylabel" textAnchor="end">
                {v}
              </text>
            )}
          </g>
        ))}

        {/* no-index lane separator */}
        <line
          x1={M.left}
          x2={W - M.right}
          y1={plotBottom + noDataH / 2}
          y2={plotBottom + noDataH / 2}
          className="tl-grid"
          strokeDasharray="2 3"
        />
        {points.some((p) => p.ndvi == null) && (
          <text x={M.left + 3} y={plotBottom + noDataH - 3} className="tl-lane-label">
            no satellite index
          </text>
        )}

        {/* dots */}
        {points.map((p) => (
          <circle
            key={p.id}
            cx={x(p.t)}
            cy={p.ndvi == null ? plotBottom + noDataH / 2 : yClamped(p.ndvi)}
            r={p.ndvi == null ? 3 : 4}
            fill={p.color}
            stroke="#ffffff"
            strokeWidth={1}
            className="tl-dot"
          >
            <title>
              {`${p.label} · ${fmtDate(p.date)}${p.ndvi != null ? ` · NDVI ${p.ndvi.toFixed(2)}` : ""}`}
            </title>
          </circle>
        ))}

        {/* x-axis date labels */}
        <text x={M.left} y={H - 6} className="tl-xlabel">
          {fmtDate(points[0].date)}
        </text>
        <text x={W - M.right} y={H - 6} className="tl-xlabel" textAnchor="end">
          {fmtDate(points[points.length - 1].date)}
        </text>
      </svg>
      <p className="timeline-note">field photos · position = satellite NDVI · colour = class</p>
    </div>
  );
}

function prepare(photos) {
  return (photos ?? [])
    .map((p) => {
      const rawDate = p.captured_at || p.uploaded_at || "";
      const parsed = rawDate ? new Date(rawDate) : null;
      const meta = CATEGORY_META[p.classification?.category];
      return {
        id: p.id,
        t: parsed ? parsed.getTime() : 0,
        date: parsed && !Number.isNaN(parsed.getTime()) ? parsed : new Date(0),
        ndvi: typeof p.satellite?.ndvi === "number" ? p.satellite.ndvi : null,
        color: meta?.color ?? "#9aa4b2",
        label: meta?.label ?? "Unclassified field photo",
      };
    })
    .sort((a, b) => a.t - b.t);
}
