import { ChartBar } from "@phosphor-icons/react";

function CoverageChart({ before, after }) {
  const hasBefore = typeof before === "number" && Number.isFinite(before);
  const hasAfter = typeof after === "number" && Number.isFinite(after);
  const hasData = hasBefore && hasAfter;

  const max = hasData ? Math.max(before, after, 0.5) : 1;
  const heightPct = (v) => (hasData ? Math.max((v / max) * 100, 3) : 3);
  const delta = hasData ? after - before : 0;
  const deltaAbs = Math.abs(delta);
  const showDelta = hasData && deltaAbs >= 0.05;

  return (
    <div className="coverage-chart">
      <div className="coverage-scale" aria-hidden="true">
        <span>{max.toFixed(1)}%</span>
        <span>{max / 2 >= 0.5 ? (max / 2).toFixed(1) : ""}</span>
        <span>0</span>
      </div>

      <div className="coverage-bars" role="img" aria-label="Signal coverage before and after">
        <div className="coverage-bar-col">
          <div className="coverage-track">
            <div
              className="coverage-fill coverage-before"
              style={{ height: `${heightPct(before)}%` }}
            >
              <span className="coverage-bar-value mono">
                {hasBefore ? before.toFixed(1) : "—"}%
              </span>
            </div>
          </div>
          <span className="coverage-bar-label">Before</span>
        </div>

        <div className="coverage-bar-col">
          <div className="coverage-track">
            <div
              className="coverage-fill coverage-after"
              style={{ height: `${heightPct(after)}%` }}
            >
              <span className="coverage-bar-value mono">
                {hasAfter ? after.toFixed(1) : "—"}%
              </span>
            </div>
          </div>
          <span className="coverage-bar-label">After</span>
        </div>
      </div>

      <p className="coverage-note">
        {hasData ? (
          <>
            {showDelta && (
              <span className={`coverage-delta mono delta-${delta >= 0 ? "up" : "down"}`}>
                {delta >= 0 ? "▲" : "▼"} {deltaAbs.toFixed(1)} pp
              </span>
            )}
            <span>
              independent per-date signal share, scale auto-set to {max.toFixed(1)}%
            </span>
          </>
        ) : (
          <span>no per-date coverage available for this result</span>
        )}
      </p>
      <span className="coverage-icon" aria-hidden="true">
        <ChartBar size={14} weight="duotone" />
      </span>
    </div>
  );
}

export { CoverageChart };
