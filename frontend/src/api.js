const BASE = "/api";
const getToken = () => localStorage.getItem("token") || "";
const headers = () => ({ Authorization: `Bearer ${getToken()}` });
const jsonHeaders = () => ({ ...headers(), "Content-Type": "application/json" });

// ── Docx cutting jobs ──────────────────────────────────────────────────────

export const submitJob = async (file, settings) => {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("settings", JSON.stringify(settings));
  const res = await fetch(`${BASE}/jobs`, {
    method: "POST",
    headers: headers(),
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
};

export const getJob = (id) => fetch(`${BASE}/jobs/${id}`, { headers: headers() });
export const listJobs = () => fetch(`${BASE}/jobs`, { headers: headers() });

export const downloadJob = async (id, originalFilename) => {
  const res = await fetch(`${BASE}/jobs/${id}/download`, { headers: headers() });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const stem = originalFilename ? originalFilename.replace(/\.docx$/i, "") : "output";
  const a = document.createElement("a");
  a.href = url;
  a.download = `${stem}_CUT.docx`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};

export const deleteJob = (id) =>
  fetch(`${BASE}/jobs/${id}`, { method: "DELETE", headers: headers() });

export const listModels = () => fetch(`${BASE}/models`, { headers: headers() });
export const getStats = () => fetch(`${BASE}/stats`, { headers: headers() });
export const getPrompts = () => fetch(`${BASE}/prompts`, { headers: headers() });
export const getRole = () => fetch(`${BASE}/role`, { headers: headers() });

// ── Projects ───────────────────────────────────────────────────────────────

export const listProjects = () => fetch(`${BASE}/projects`, { headers: headers() });

export const createProject = (data) =>
  fetch(`${BASE}/projects`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(data),
  });

export const getProject = (id) => fetch(`${BASE}/projects/${id}`, { headers: headers() });

export const updateProject = (id, data) =>
  fetch(`${BASE}/projects/${id}`, {
    method: "PATCH",
    headers: jsonHeaders(),
    body: JSON.stringify(data),
  });

export const deleteProject = (id) =>
  fetch(`${BASE}/projects/${id}`, { method: "DELETE", headers: headers() });

export const startResearch = (id) =>
  fetch(`${BASE}/projects/${id}/research`, { method: "POST", headers: headers() });

export const startProjectCut = (id) =>
  fetch(`${BASE}/projects/${id}/cut`, { method: "POST", headers: headers() });

// ── Cards ──────────────────────────────────────────────────────────────────

export const listProjectCards = (projectId, { cardStatus, q } = {}) => {
  const params = new URLSearchParams();
  if (cardStatus) params.set("card_status", cardStatus);
  if (q) params.set("q", q);
  const qs = params.toString();
  return fetch(`${BASE}/projects/${projectId}/cards${qs ? "?" + qs : ""}`, {
    headers: headers(),
  });
};

export const getCard = (id) => fetch(`${BASE}/cards/${id}`, { headers: headers() });

export const updateCard = (id, data) =>
  fetch(`${BASE}/cards/${id}`, {
    method: "PATCH",
    headers: jsonHeaders(),
    body: JSON.stringify(data),
  });

export const approveCard = (id) =>
  fetch(`${BASE}/cards/${id}/approve`, { method: "POST", headers: headers() });

export const trashCard = (id) =>
  fetch(`${BASE}/cards/${id}/trash`, { method: "POST", headers: headers() });

export const restoreCard = (id) =>
  fetch(`${BASE}/cards/${id}/restore`, { method: "POST", headers: headers() });

export const generateCite = (id, articleText) =>
  fetch(`${BASE}/cards/${id}/cite`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({ article_text: articleText }),
  });

export const exportCards = async (cardIds, hlColor = "cyan") => {
  const res = await fetch(`${BASE}/cards/export`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({ card_ids: cardIds, hl_color: hlColor }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "lionclaw_export.docx";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};
