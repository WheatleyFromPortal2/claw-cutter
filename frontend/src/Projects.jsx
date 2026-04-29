import { useEffect, useState } from "react";
import { listProjects, createProject, deleteProject } from "./api.js";

function formatDate(iso) {
  if (!iso) return "—";
  return new Date(iso + "Z").toLocaleDateString();
}

const STATUS_COLOR = {
  idle: "var(--text-muted)",
  running: "var(--warning)",
  done: "var(--success)",
  error: "#f87171",
};

export default function Projects({ onSelectProject }) {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: "", topic: "", description: "" });
  const [submitError, setSubmitError] = useState(null);

  const load = async () => {
    try {
      const res = await listProjects();
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setProjects(await res.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!form.name.trim()) return;
    setSubmitError(null);
    try {
      const res = await createProject(form);
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const p = await res.json();
      setProjects((prev) => [p, ...prev]);
      setForm({ name: "", topic: "", description: "" });
      setCreating(false);
    } catch (e) {
      setSubmitError(e.message);
    }
  };

  const handleDelete = async (e, id) => {
    e.stopPropagation();
    if (!confirm("Delete this project and all its cards?")) return;
    try {
      await deleteProject(id);
      setProjects((prev) => prev.filter((p) => p.id !== id));
    } catch (e) {
      alert("Delete failed: " + e.message);
    }
  };

  return (
    <div className="projects-page">
      <div className="projects-header">
        <h2 className="section-title" style={{ marginBottom: 0 }}>Research Projects</h2>
        <button className="btn-primary" style={{ padding: "6px 16px", fontSize: 13 }} onClick={() => setCreating((v) => !v)}>
          {creating ? "Cancel" : "+ New Project"}
        </button>
      </div>

      {creating && (
        <form className="project-create-form" onSubmit={handleCreate}>
          <div className="pcf-row">
            <label className="pcf-label">Name *</label>
            <input
              className="pcf-input"
              placeholder="e.g. Econ Disadvantage — Dollar Heg"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              autoFocus
            />
          </div>
          <div className="pcf-row">
            <label className="pcf-label">Topic</label>
            <input
              className="pcf-input"
              placeholder="Debate topic or resolution"
              value={form.topic}
              onChange={(e) => setForm((f) => ({ ...f, topic: e.target.value }))}
            />
          </div>
          <div className="pcf-row">
            <label className="pcf-label">Argument</label>
            <textarea
              className="pcf-input"
              placeholder="Describe the argument structure and what evidence is needed…"
              rows={4}
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            />
          </div>
          {submitError && <div className="error-box">{submitError}</div>}
          <button className="btn-primary" type="submit" disabled={!form.name.trim()} style={{ alignSelf: "flex-end" }}>
            Create Project
          </button>
        </form>
      )}

      {error && <div className="error-box">{error}</div>}

      {loading ? (
        <div className="loading">Loading projects…</div>
      ) : projects.length === 0 ? (
        <div className="empty-state">No projects yet. Create one to start researching cards.</div>
      ) : (
        <div className="project-list">
          {projects.map((p) => (
            <div key={p.id} className="project-card" onClick={() => onSelectProject(p.id)}>
              <div className="project-card-top">
                <div className="project-card-name">{p.name}</div>
                <button className="btn-delete" onClick={(e) => handleDelete(e, p.id)}>✕</button>
              </div>
              {p.topic && <div className="project-card-topic">{p.topic}</div>}
              <div className="project-card-meta">
                <span className="project-meta-item">
                  <span className="project-meta-dot" style={{ background: STATUS_COLOR[p.research_status] || "var(--text-muted)" }} />
                  Research: {p.research_status}
                </span>
                <span className="project-meta-item">{p.card_count} card{p.card_count !== 1 ? "s" : ""}</span>
                <span className="project-meta-item" style={{ color: "var(--text-muted)" }}>{formatDate(p.created_at)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
