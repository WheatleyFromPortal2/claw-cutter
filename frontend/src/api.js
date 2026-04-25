const BASE = "/api";
const getToken = () => localStorage.getItem("token") || "";
const headers = () => ({ Authorization: `Bearer ${getToken()}` });

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

export const getJob = (id) =>
  fetch(`${BASE}/jobs/${id}`, { headers: headers() });

export const listJobs = () =>
  fetch(`${BASE}/jobs`, { headers: headers() });

export const downloadJob = async (id) => {
  const res = await fetch(`${BASE}/jobs/${id}/download`, { headers: headers() });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const disposition = res.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="?([^"]+)"?/);
  const filename = match ? match[1] : "traced_output.docx";
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};

export const deleteJob = (id) =>
  fetch(`${BASE}/jobs/${id}`, { method: "DELETE", headers: headers() });

export const listModels = () =>
  fetch(`${BASE}/models`, { headers: headers() });
