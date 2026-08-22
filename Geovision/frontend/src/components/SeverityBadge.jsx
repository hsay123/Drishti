const SEVERITY_META = {
  mild: { label: "Mild", tone: "mild" },
  moderate: { label: "Moderate", tone: "moderate" },
  severe: { label: "Severe", tone: "severe" },
};

function SeverityBadge({ severity }) {
  const meta = SEVERITY_META[severity] ?? { label: severity, tone: "mild" };
  return (
    <span className={`severity-badge severity-${meta.tone}`}>
      <span className="severity-dot" />
      {meta.label}
    </span>
  );
}

export { SeverityBadge, SEVERITY_META };
