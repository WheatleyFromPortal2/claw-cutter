import { useCallback, useEffect, useRef, useState } from "react";
import {
  getProject,
  listProjectCards,
  startResearch,
  startProjectCut,
  approveCard,
  trashCard,
  restoreCard,
  exportCards,
  updateProject,
  approveAllCards,
  trashUnapprovedCards,
  addCardFromUrl,
} from "./api.js";

const TAB_LABELS = {
  researched: "Researched",
  approved: "Approved",
  cut: "Cut",
  trashed: "Trash",
};

function CardRow({ card, tab, onApprove, onTrash, onRestore, onSelect, onView, selected, showSelect }) {
  const statusColor = {
    researched: "var(--text-muted)",
    approved: "var(--warning)",
    cut: "var(--success)",
    trashed: "#f87171",
  };

  // In researched tab: show article title as primary, tag as secondary
  const primaryTitle = tab === "researched"
    ? (card.title || card.tag || "Untitled")
    : (card.tag || card.title || "Untitled");

  const handleRowClick = () => {
    if (showSelect) {
      // For approved/cut tabs: click row to view, checkbox to select
      onView(card.id);
    } else {
      onView(card.id);
    }
  };

  return (
    <div className={`res-card-row ${selected ? "selected" : ""}`} onClick={handleRowClick}>
      {showSelect && (
        <input
          type="checkbox"
          className="res-card-checkbox"
          checked={selected}
          onChange={() => onSelect(card.id)}
          onClick={(e) => e.stopPropagation()}
        />
      )}
      <div className="res-card-body">
        <div className="res-card-tag">
          {primaryTitle}
          {card.missing_full_text && (
            <span className="res-card-missing" title="Full article text not available"> ⚠</span>
          )}
        </div>
        <div className="res-card-cite">
          {[card.initials, card.date ? card.date.slice(0, 4) : null, card.author, card.publisher]
            .filter(Boolean)
            .join(" · ")}
        </div>
      </div>
      <div className="res-card-actions" onClick={(e) => e.stopPropagation()}>
        <span className="res-status-dot" style={{ background: statusColor[card.card_status] }} title={card.card_status} />
        {card.card_status === "researched" && (
          <>
            <button className="res-btn approve" onClick={() => onApprove(card.id)} title="Approve">✓</button>
            <button className="res-btn trash" onClick={() => onTrash(card.id)} title="Trash">✕</button>
          </>
        )}
        {card.card_status === "approved" && (
          <button className="res-btn trash" onClick={() => onTrash(card.id)} title="Trash">✕</button>
        )}
        {card.card_status === "trashed" && (
          <button className="res-btn approve" onClick={() => onRestore(card.id)} title="Restore">↩</button>
        )}
      </div>
    </div>
  );
}

function ActivityLog({ entries, running, title }) {
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries?.length]);

  if (!entries || entries.length === 0) return null;

  return (
    <div className="activity-log">
      <div className="activity-log-title">
        {running && <span className="activity-log-spinner" />}
        {title}
      </div>
      <div className="activity-log-list">
        {entries.map((e, i) => (
          <div
            key={i}
            className={`activity-log-entry ${i === entries.length - 1 ? "latest" : ""}`}
          >
            {e.msg}
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}

export default function ProjectDetail({ projectId, onBack, onSelectCard }) {
  const [project, setProject] = useState(null);
  const [cards, setCards] = useState([]);
  const [tab, setTab] = useState("researched");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(new Set());
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState({ name: "", topic: "", description: "" });
  const [saving, setSaving] = useState(false);
  const [addUrlVisible, setAddUrlVisible] = useState(false);
  const [addUrlValue, setAddUrlValue] = useState("");
  const [addingUrl, setAddingUrl] = useState(false);
  const pollRef = useRef(null);

  const loadProject = useCallback(async () => {
    try {
      const res = await getProject(projectId);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setProject(await res.json());
    } catch (e) {
      setError(e.message);
    }
  }, [projectId]);

  const loadCards = useCallback(async () => {
    try {
      const res = await listProjectCards(projectId, { cardStatus: tab, q: search || undefined });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setCards(await res.json());
    } catch (e) {
      setError(e.message);
    }
  }, [projectId, tab, search]);

  useEffect(() => {
    loadProject();
    loadCards();
  }, [loadProject, loadCards]);

  // Poll while research or cut is running
  useEffect(() => {
    const needsPoll =
      project?.research_status === "running" || project?.cut_status === "running";
    if (needsPoll && !pollRef.current) {
      pollRef.current = setInterval(async () => {
        await loadProject();
        await loadCards();
      }, 2000);
    }
    if (!needsPoll && pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [project?.research_status, project?.cut_status, loadProject, loadCards]);

  const handleStartEdit = () => {
    setEditForm({ name: project.name, topic: project.topic, description: project.description });
    setEditing(true);
  };

  const handleSaveEdit = async () => {
    setSaving(true);
    try {
      const res = await updateProject(projectId, editForm);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const updated = await res.json();
      setProject((p) => ({ ...p, ...updated }));
      setEditing(false);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleResearch = async () => {
    const res = await startResearch(projectId);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      alert("Research failed: " + (err.detail || res.status));
      return;
    }
    setProject((p) => ({ ...p, research_status: "running", research_log: [] }));
  };

  const handleCut = async () => {
    const res = await startProjectCut(projectId);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      alert("Cut failed: " + (err.detail || res.status));
      return;
    }
    setProject((p) => ({ ...p, cut_status: "running", cut_log: [] }));
  };

  const handleApprove = async (id) => {
    await approveCard(id);
    setCards((prev) => prev.map((c) => (c.id === id ? { ...c, card_status: "approved" } : c)));
    if (tab === "researched") setCards((prev) => prev.filter((c) => c.id !== id));
  };

  const handleTrash = async (id) => {
    await trashCard(id);
    setCards((prev) => prev.filter((c) => c.id !== id));
    setSelected((s) => { const n = new Set(s); n.delete(id); return n; });
  };

  const handleRestore = async (id) => {
    await restoreCard(id);
    setCards((prev) => prev.filter((c) => c.id !== id));
  };

  const handleApproveAll = async () => {
    if (!window.confirm(`Approve all ${cards.length} researched cards?`)) return;
    const res = await approveAllCards(projectId);
    if (res.ok) {
      await loadCards();
    }
  };

  const handleTrashUnapproved = async () => {
    if (!window.confirm(`Trash all ${cards.length} unapproved cards?`)) return;
    const res = await trashUnapprovedCards(projectId);
    if (res.ok) {
      await loadCards();
    }
  };

  const handleAddFromUrl = async () => {
    const url = addUrlValue.trim();
    if (!url) return;
    setAddingUrl(true);
    try {
      const res = await addCardFromUrl(projectId, url);
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        alert("Failed: " + (err.detail || res.status));
      } else {
        setAddUrlValue("");
        setAddUrlVisible(false);
        await loadCards();
      }
    } catch (e) {
      alert("Error: " + e.message);
    } finally {
      setAddingUrl(false);
    }
  };

  const toggleSelect = (id) => {
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  };

  const handleExport = async () => {
    const ids = selected.size > 0 ? [...selected] : cards.map((c) => c.id);
    if (!ids.length) return;
    setExporting(true);
    try {
      await exportCards(ids);
    } catch (e) {
      alert("Export failed: " + e.message);
    } finally {
      setExporting(false);
    }
  };

  if (error) return (
    <div className="project-detail">
      <button className="btn-back" onClick={onBack}>← Projects</button>
      <div className="error-box">{error}</div>
    </div>
  );

  if (!project) return (
    <div className="project-detail">
      <button className="btn-back" onClick={onBack}>← Projects</button>
      <div className="loading">Loading…</div>
    </div>
  );

  const isResearching = project.research_status === "running";
  const isCutting = project.cut_status === "running";

  return (
    <div className="project-detail">
      {/* Header */}
      <div className="pd-header">
        <button className="btn-back" onClick={onBack}>← Projects</button>
        <div className="pd-title-block">
          <h2 className="pd-title">{project.name}</h2>
          {project.topic && <span className="pd-topic">{project.topic}</span>}
        </div>
        <div className="pd-actions">
          <button className="btn-secondary" style={{ fontSize: 13 }} onClick={handleStartEdit}>
            Edit
          </button>
          <button
            className="btn-primary"
            style={{ padding: "6px 14px", fontSize: 13 }}
            onClick={handleResearch}
            disabled={isResearching}
          >
            {isResearching ? "Researching…" : "Research"}
          </button>
          <button
            className="btn-cut"
            onClick={handleCut}
            disabled={isCutting}
          >
            {isCutting ? "Cutting…" : "Cut Approved"}
          </button>
        </div>
      </div>

      {/* Status banners */}
      {project.research_status === "error" && (
        <div className="error-box">Research error: {project.research_error}</div>
      )}
      {project.cut_status === "error" && (
        <div className="error-box">Cut error: {project.cut_error}</div>
      )}

      {/* Research log */}
      {(isResearching || (project.research_log && project.research_log.length > 0 && project.research_status !== "idle")) && (
        <ActivityLog
          entries={project.research_log || []}
          running={isResearching}
          title="Research Log"
        />
      )}

      {/* Cut log */}
      {(isCutting || (project.cut_log && project.cut_log.length > 0 && project.cut_status !== "idle")) && (
        <ActivityLog
          entries={project.cut_log || []}
          running={isCutting}
          title="Cut Log"
        />
      )}

      {/* Edit form */}
      {editing && (
        <div className="project-create-form">
          <div className="pcf-row">
            <label className="pcf-label">Name *</label>
            <input className="pcf-input" value={editForm.name} onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))} autoFocus />
          </div>
          <div className="pcf-row">
            <label className="pcf-label">Topic</label>
            <input className="pcf-input" value={editForm.topic} onChange={(e) => setEditForm((f) => ({ ...f, topic: e.target.value }))} />
          </div>
          <div className="pcf-row">
            <label className="pcf-label">Argument</label>
            <textarea className="pcf-input" rows={4} value={editForm.description} onChange={(e) => setEditForm((f) => ({ ...f, description: e.target.value }))} />
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button className="btn-secondary" onClick={() => setEditing(false)}>Cancel</button>
            <button className="btn-primary" style={{ fontSize: 13, padding: "6px 16px" }} onClick={handleSaveEdit} disabled={!editForm.name.trim() || saving}>
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      )}

      {/* Link story */}
      {project.link_story && (
        <div className="link-story">
          <div className="link-story-label">Argument Chain</div>
          <div className="link-story-text">{project.link_story}</div>
        </div>
      )}

      {/* Tabs */}
      <div className="pd-tabs">
        {Object.entries(TAB_LABELS).map(([key, label]) => (
          <button
            key={key}
            className={`pd-tab ${tab === key ? "active" : ""}`}
            onClick={() => { setTab(key); setSelected(new Set()); }}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Search + export + bulk actions */}
      <div className="pd-toolbar">
        <input
          className="pd-search"
          placeholder="Search author, title, publisher…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {tab === "researched" && cards.length > 0 && (
          <>
            <button className="res-btn approve" style={{ padding: "6px 12px", fontSize: 12 }} onClick={handleApproveAll}>
              Approve All
            </button>
            <button className="res-btn trash" style={{ padding: "6px 12px", fontSize: 12 }} onClick={handleTrashUnapproved}>
              Trash All
            </button>
          </>
        )}
        {tab === "researched" && (
          <button
            className="btn-secondary"
            style={{ fontSize: 12, padding: "5px 12px", whiteSpace: "nowrap" }}
            onClick={() => setAddUrlVisible((v) => !v)}
          >
            + Add by URL
          </button>
        )}
        {(tab === "cut" || tab === "approved") && (
          <button
            className="btn-export"
            onClick={handleExport}
            disabled={exporting}
          >
            {exporting ? "Exporting…" : selected.size > 0 ? `Export ${selected.size}` : "Export All"}
          </button>
        )}
      </div>

      {/* Add by URL form */}
      {addUrlVisible && tab === "researched" && (
        <div className="add-url-form">
          <input
            className="add-url-input"
            placeholder="Paste article URL…"
            value={addUrlValue}
            onChange={(e) => setAddUrlValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !addingUrl && handleAddFromUrl()}
            autoFocus
          />
          <button
            className="btn-primary"
            style={{ fontSize: 13, padding: "6px 16px", whiteSpace: "nowrap" }}
            onClick={handleAddFromUrl}
            disabled={!addUrlValue.trim() || addingUrl}
          >
            {addingUrl ? "Fetching…" : "Add"}
          </button>
          <button className="btn-secondary" style={{ fontSize: 13 }} onClick={() => { setAddUrlVisible(false); setAddUrlValue(""); }}>
            Cancel
          </button>
        </div>
      )}

      {/* Card list */}
      {cards.length === 0 ? (
        <div className="empty-state">
          {tab === "researched" && !isResearching
            ? 'No researched cards. Click "Research" to have AI find articles.'
            : tab === "approved"
            ? "No approved cards yet."
            : tab === "cut"
            ? 'No cut cards. Approve cards then click "Cut Approved".'
            : "Trash is empty."}
        </div>
      ) : (
        <div className="res-card-list">
          {cards.map((card) => (
            <CardRow
              key={card.id}
              card={card}
              tab={tab}
              onApprove={handleApprove}
              onTrash={handleTrash}
              onRestore={handleRestore}
              onSelect={toggleSelect}
              onView={(id) => onSelectCard(id)}
              selected={selected.has(card.id)}
              showSelect={tab === "cut" || tab === "approved"}
            />
          ))}
        </div>
      )}

      {selected.size > 0 && (
        <div className="selection-bar">
          {selected.size} selected
          <button className="btn-export" onClick={handleExport} disabled={exporting}>
            {exporting ? "Exporting…" : "Export Selected"}
          </button>
          <button className="btn-secondary" onClick={() => setSelected(new Set())}>Clear</button>
        </div>
      )}
    </div>
  );
}
