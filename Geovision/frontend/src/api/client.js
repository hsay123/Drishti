const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

//: Hard client-side cap so the UI can never hang forever on a stalled request
//: (Phase 19d). Independent of backend timeouts — belt and suspenders.
//: 180 s (was 90 s) — tonight's elevated GEE latency made legitimate live runs
//: (thumbnails especially) exceed 90 s while still progressing.
export const ANALYZE_TIMEOUT_MS = 180_000;

async function postAnalyze(payload, signal) {
  const res = await fetch(`${API_BASE}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });

  let body;
  try {
    body = await res.json();
  } catch {
    body = null;
  }

  if (!res.ok) {
    let message;
    if (Array.isArray(body?.detail)) {
      message = body.detail.map((d) => d.msg).filter(Boolean).join(" ");
    } else {
      message = body?.detail?.error ?? body?.detail ?? body?.error;
    }
    message = message ?? `Request failed (HTTP ${res.status})`;
    throw new Error(message);
  }
  return body;
}

async function getHealth() {
  const res = await fetch(`${API_BASE}/health`);
  if (!res.ok) return { status: "degraded", gee_connected: false };
  try {
    return await res.json();
  } catch {
    return { status: "degraded", gee_connected: false };
  }
}

async function getWatchlist() {
  const res = await fetch(`${API_BASE}/watchlist`);
  if (!res.ok) throw new Error(`Watchlist request failed (HTTP ${res.status})`);
  const body = await res.json();
  return Array.isArray(body) ? body : [];
}

async function getPhotos(watershedId) {
  const params = watershedId ? `?watershed_id=${encodeURIComponent(watershedId)}` : "";
  const res = await fetch(`${API_BASE}/photos${params}`);
  if (!res.ok) throw new Error(`Photos request failed (HTTP ${res.status})`);
  const body = await res.json();
  return Array.isArray(body) ? body : [];
}

async function uploadPhoto({ file, aoi, watershedId, notes, context }) {
  const form = new FormData();
  form.append("file", file);
  form.append("aoi", JSON.stringify(aoi));
  if (watershedId) form.append("watershed_id", watershedId);
  if (notes) form.append("notes", notes);
  // Optional DRISHTI-hierarchy context (Task: SRISHTI-DRISHTI alignment) —
  // sent only when the user typed something; empty fields stay absent.
  if (context) {
    for (const [key, value] of Object.entries(context)) {
      if (value && String(value).trim()) form.append(key, String(value).trim());
    }
  }
  // auto_classify omitted — backend default true.
  let body;
  try {
    const res = await fetch(`${API_BASE}/photos/upload`, { method: "POST", body: form });
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    if (!res.ok) {
      const d = body?.detail;
      const message = typeof d === "string" ? d : d?.detail || d?.error || body?.error;
      throw new Error(message ?? `Upload failed (HTTP ${res.status})`);
    }
    return body;
  } catch (err) {
    if (err instanceof Error && err.message) throw err;
    throw new Error("Upload failed — network error");
  }
}

async function getNarrative(watershedId) {
  const params = watershedId ? `?watershed_id=${encodeURIComponent(watershedId)}` : "";
  const res = await fetch(`${API_BASE}/photos/narrative${params}`);
  if (!res.ok) throw new Error(`Narrative request failed (HTTP ${res.status})`);
  return res.json();
}

// DRISHTI-style CSV bulk import — returns the per-row summary
// ({total_rows, imported[], skipped[]}) rather than a single pass/fail.
async function importPhotos({ file }) {
  const form = new FormData();
  form.append("file", file);
  let body;
  try {
    const res = await fetch(`${API_BASE}/photos/import`, { method: "POST", body: form });
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    if (!res.ok) {
      const d = body?.detail;
      const message = typeof d === "string" ? d : d?.detail || d?.error || body?.error;
      throw new Error(message ?? `Import failed (HTTP ${res.status})`);
    }
    return body;
  } catch (err) {
    if (err instanceof Error && err.message) throw err;
    throw new Error("Import failed — network error");
  }
}

export { postAnalyze, getHealth, getWatchlist, getPhotos, uploadPhoto, getNarrative, importPhotos };
