import { Plant, Drop, Flame, Scan, ArrowRight } from "@phosphor-icons/react";

const MODE_META = {
  ndvi: { label: "Crop stress", icon: Plant },
  ndwi: { label: "Flood", icon: Drop },
  nbr: { label: "Burn scar", icon: Flame },
};

function Watchlist({ entries, onSelect }) {
  if (!entries || entries.length === 0) return null;
  return (
    <section className="watchlist">
      <h3 className="watchlist-title">
        Priority watchlist
        <span className="watchlist-count mono">{entries.length}</span>
      </h3>
      <ul className="watchlist-list">
        {entries.map((e, i) => {
          const modeMeta = MODE_META[e.mode] ?? { label: e.mode, icon: Scan };
          const ModeIcon = modeMeta.icon;
          const tone = e.severity ?? "mild";
          return (
            <li key={i}>
              <button
                type="button"
                className={`watchlist-item severity-${tone}`}
                onClick={() => onSelect(e)}
                aria-label={`Load ${e.location_name ?? "custom area"} result`}
              >
                <span className={`severity-badge priority-badge severity-${tone}`}>
                  <span className="severity-dot" />
                  {e.priority}
                </span>
                <ModeIcon size={16} weight="duotone" className="watchlist-icon" />
                <span className="watchlist-loc">{e.location_name ?? "Custom AOI"}</span>
                <span className="watchlist-pct mono">
                  {typeof e.affected_pct === "number" ? e.affected_pct.toFixed(2) : "—"}%
                </span>
                <ArrowRight size={13} weight="bold" className="watchlist-arrow" />
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export { Watchlist };
