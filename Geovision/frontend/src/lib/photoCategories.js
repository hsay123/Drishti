/**
 * Shared metadata for photo classification categories — single source of
 * truth for marker colors and human labels, mirrored from
 * backend/pipeline/photo_classifier.py ALLOWED_CATEGORIES.
 */
export const CATEGORY_META = {
  conservation_structure: {
    color: "#38c8ff",
    label: "Conservation structure",
  },
  water_body: { color: "#3b82f6", label: "Water body" },
  vegetation_healthy: { color: "#22c55e", label: "Healthy vegetation" },
  vegetation_degraded: { color: "#f59e0b", label: "Degraded vegetation" },
  agriculture_active: { color: "#a3e635", label: "Active agriculture" },
  other: { color: "#9aa4b2", label: "Other" },
  // CSV-imported metadata-only points — never a model verdict.
  not_analyzed: { color: "#94a3b8", label: "Not analyzed (imported)" },
};
