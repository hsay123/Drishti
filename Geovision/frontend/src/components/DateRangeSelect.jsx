import { ArrowUUpLeft } from "@phosphor-icons/react";
import { oneYearBefore } from "../lib/dates.js";

const SAME_SEASON_CAVEAT =
  "Same-season comparison over a long gap may conflate crop stress with normal " +
  "harvest/senescence. Year-over-year comparison is recommended for more " +
  "reliable crop-stress detection.";

function DateRangeSelect({
  mode,
  comparisonType,
  before,
  after,
  onBefore,
  onAfter,
  onComparisonType,
}) {
  const cropMode = mode === "ndvi";
  const derivedBefore = oneYearBefore(after);

  return (
    <div className="field-stack">
      {cropMode && (
        <div className="field">
          <label className="field-label">Comparison window</label>
          <div className="seg-control compare-seg">
            <button
              type="button"
              className={comparisonType === "year_over_year" ? "is-active" : ""}
              onClick={() => onComparisonType("year_over_year")}
              aria-pressed={comparisonType === "year_over_year"}
            >
              Year-over-year
            </button>
            <button
              type="button"
              className={comparisonType === "same_season" ? "is-active" : ""}
              onClick={() => onComparisonType("same_season")}
              aria-pressed={comparisonType === "same_season"}
            >
              Same-season
            </button>
          </div>
        </div>
      )}

      {cropMode && comparisonType === "year_over_year" ? (
        <div className="field">
          <label className="field-label" htmlFor="analysis-date">
            Analysis date (year-over-year)
          </label>
          <input
            id="analysis-date"
            type="date"
            className="input"
            value={after}
            onChange={(e) => onAfter(e.target.value)}
          />
          <p className="hint yoy-hint">
            <ArrowUUpLeft size={13} weight="duotone" />
            Before window auto-derived: <span className="mono">{derivedBefore}</span>{" "}
            (same calendar window, one year prior — hemispheres both fine).
          </p>
        </div>
      ) : (
        <div className="date-grid">
          <div className="field">
            <label className="field-label" htmlFor="before-date">
              Before date
            </label>
            <input
              id="before-date"
              type="date"
              className="input"
              value={before}
              max={after}
              onChange={(e) => onBefore(e.target.value)}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="after-date">
              After date
            </label>
            <input
              id="after-date"
              type="date"
              className="input"
              value={after}
              min={before}
              onChange={(e) => onAfter(e.target.value)}
            />
          </div>
        </div>
      )}

      {cropMode && comparisonType === "same_season" && (
        <p className="hint caveat-hint">{SAME_SEASON_CAVEAT}</p>
      )}

      <p className="hint">
        Sentinel-2 composites over a ±6 day window; clouds are masked via the SCL
        band. Revisit cadence is ~5 days, so results are near-real-time.
      </p>
    </div>
  );
}

export { DateRangeSelect, SAME_SEASON_CAVEAT };
