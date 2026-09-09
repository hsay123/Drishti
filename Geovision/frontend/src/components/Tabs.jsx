/**
 * Minimal shared tab bar for the sidebar reorganization — no UI library.
 *
 * Renders a button row plus the active panel. Only the active panel stays
 * mounted; tab switches are plain conditional rendering. Accessibility: each
 * tab is a real <button> with aria-selected, matching the segmented-control
 * pattern already used across the app.
 */
import { Children, isValidElement, useState } from "react";

function Tabs({ tabs, initial, className, children }) {
  const [active, setActive] = useState(initial ?? tabs[0]?.id);
  const activeTab = tabs.find((t) => t.id === active) ?? tabs[0];

  const activePanel = Children.toArray(children).find(
    (child) => isValidElement(child) && child.props.id === activeTab?.id
  );

  return (
    <div className={`tabs ${className ?? ""}`}>
      <div className="tab-list" role="tablist" aria-label="Sidebar sections">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={activeTab?.id === t.id}
            className={`tab-btn ${activeTab?.id === t.id ? "is-active" : ""}`}
            onClick={() => {
              setActive(t.id);
            }}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="tab-panels">{activePanel}</div>
    </div>
  );
}

function TabPanel({ id, children }) {
  return (
    <div className="tab-panel" role="tabpanel" aria-label={id}>
      {children}
    </div>
  );
}

export { Tabs, TabPanel };