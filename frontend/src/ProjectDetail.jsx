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

function CardTextRenderer({ text, underlined = [], highlighted = [] }) {
  if (!text) return <em style={{ color: "var(--text-muted)" }}>No card text.</em>;

  const types = Array(text.length).fill("plain");
  for (const phrase of underlined) {
    let idx = text.indexOf(phrase);
    while (idx !== -1) {
      for (let p = idx; p < idx + phrase.length; p++) {
        if (types[p] === "plain") types[p] = "ul";
      }
      idx = text.indexOf(phrase, idx + phrase.length);
    }
  }
  for (const phrase of highlighted) {
    let idx = text.indexOf(phrase);
    while (idx !== -1) {
      for (let p = idx; p < idx + phrase.length; p++) {
        types[p] = "hl";
      }
      idx = text.indexOf(phrase, idx + phrase.length);
    }
  }

  const segs = [];
  let i = 0;
  while (i < text.length) {
    const t = types[i];
    let j = i;
    while (j < text.length && types[j] === t) j++;
    segs.push({ type: t, text: text.slice(i, j) });
    i = j;
  }

  return (
    <p className="card-body-text">
      {segs.map((seg, idx) => {
        if (seg.type === "hl") return <mark key={idx} className="card-hl">{seg.text}</mark>;
        if (seg.type === "ul") return <span key={idx} className="card-ul">{seg.text}</span>;
        return <span key={idx}>{seg.text}</span>;
      })}
    </p>
  );
}

function CutCardPreview({ card }) {
  const [expanded, setExpanded] = useState(false);
  const cite = [card.initials, card.date?.slice(0, 4), card.author, card.publisher]
    .filter(Boolean).join(" · ");

  return (
    <div className="cut-card-preview">
      <div className="cut-card-header" onClick={() => setExpanded((v) => !v)}>
        <span className="cut-card-toggle">{expanded ? "▼" : "▶"}</span>
        <div className="res-card-body" style={{ flex: 1 }}>
          <div className="res-card-tag">{card.tag || card.title || "Untitled"}</div>
          {cite && <div className="res-card-cite">{cite}</div>}
        </div>
        <span className="cut-card-stats">
          {(card.underlined || []).length} UL · {(card.highlighted || []).length} HL
        </span>
      </div>
      {expanded && (
        <div className="cut-card-body">
          <CardTextRenderer
            text={card.card_text}
            underlined={card.underlined || []}
            highlighted={card.highlighted || []}
          />
        </div>
      )}
    </div>
  );
}

function CardRow({ card, onApprove, onTrash, onRestore, onSelect, selected, showSelect, showTitle }) {
  const statusColor = {
    researched: "var(--text-muted)",
    approved: "var(--warning)",
    cut: "var(--success)",
    trashed: "#f87171",
  };

  const displayName = showTitle
    ? (card.title || card.tag || "Untitled")
    : (card.tag || card.title || "Untitled");

  return (
    <div className={`res-card-row ${selected ? "selected" : ""}`} onClick={() => onSelect(card.id)}>
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
        <div className="res-card-tag">{displayName}</div>
        <div className="res-card-cite">
          {[card.initials, card.date ? card.date.slice(0, 4) : "", card.author, card.publisher]
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
  const [bulkWorking, setBulkWorking] = useState(false);
  const [addUrlOpen, setAddUrlOpen] = useState(false);
  const [addUrlValue, setAddUrlValue] = useState("");
  const [addUrlWorking, setAddUrlWorking] = useState(false);
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
      }, 3000);
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
      await loadCards();
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
    setProject((p) => ({ ...p, research_status: "running" }));
  };

  const handleCut = async () => {
    const res = await startProjectCut(projectId);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      alert("Cut failed: " + (err.detail || res.status));
      return;
    }
    setProject((p) => ({ ...p, cut_status: "running" }));
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
    setBulkWorking(true);
    try {
      await approveAllCards(projectId);
      await loadCards();
    } catch (e) {
      setError(e.message);
    } finally {
      setBulkWorking(false);
    }
  };

  const handleTrashUnapproved = async () => {
    if (!confirm("Trash all unapproved (researched) cards?")) return;
    setBulkWorking(true);
    try {
      await trashUnapprovedCards(projectId);
      await loadCards();
    } catch (e) {
      setError(e.message);
    } finally {
      setBulkWorking(false);
    }
  };

  const handleAddFromUrl = async () => {
    if (!addUrlValue.trim()) return;
    setAddUrlWorking(true);
    try {
      const res = await addCardFromUrl(projectId, addUrlValue.trim());
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      setAddUrlValue("");
      setAddUrlOpen(false);
      await loadCards();
    } catch (e) {
      setError(e.message);
    } finally {
      setAddUrlWorking(false);
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
      {(isResearching || isCutting) && (
        <div className="activity-log">
          <div className="activity-log-title">
            {isResearching ? "Researching…" : "Cutting cards…"}
          </div>
          <div className="activity-log-entries">
            {(isResearching ? (project.research_log || []) : (project.cut_log || [])).map((line, i) => (
              <div key={i} className="activity-log-line">{line}</div>
            ))}
            {(isResearching ? (project.research_log || []) : (project.cut_log || [])).length === 0 && (
              <div className="activity-log-line muted">Starting…</div>
            )}
          </div>
        </div>
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

      {/* Search + bulk actions */}
      <div className="pd-toolbar">
        <input
          className="pd-search"
          placeholder="Search author, title, publisher…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {tab === "researched" && (
          <>
            <button
              className="btn-export"
              onClick={handleApproveAll}
              disabled={bulkWorking || cards.length === 0}
              title="Approve all visible cards"
            >
              {bulkWorking ? "Working…" : "Approve All"}
            </button>
            <button
              className="btn-bulk-trash"
              onClick={handleTrashUnapproved}
              disabled={bulkWorking || cards.length === 0}
              title="Trash all unapproved cards"
            >
              Trash All
            </button>
          </>
        )}
        {tab === "approved" && (
          <>
            <button
              className="btn-secondary"
              style={{ fontSize: 12, padding: "5px 10px", whiteSpace: "nowrap" }}
              onClick={() => setAddUrlOpen((v) => !v)}
            >
              + Add from URL
            </button>
            <button
              className="btn-export"
              onClick={handleExport}
              disabled={exporting}
            >
              {exporting ? "Exporting…" : selected.size > 0 ? `Export ${selected.size}` : "Export All"}
            </button>
          </>
        )}
        {tab === "cut" && (
          <button
            className="btn-export"
            onClick={handleExport}
            disabled={exporting}
          >
            {exporting ? "Exporting…" : selected.size > 0 ? `Export ${selected.size}` : "Export All"}
          </button>
        )}
      </div>

      {tab === "approved" && addUrlOpen && (
        <div className="add-url-form">
          <input
            className="pd-search"
            placeholder="Paste article URL here…"
            value={addUrlValue}
            onChange={(e) => setAddUrlValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAddFromUrl()}
            autoFocus
          />
          <button className="btn-primary" style={{ fontSize: 12, padding: "5px 14px" }} onClick={handleAddFromUrl} disabled={addUrlWorking || !addUrlValue.trim()}>
            {addUrlWorking ? "Adding…" : "Add"}
          </button>
          <button className="btn-secondary" style={{ fontSize: 12, padding: "5px 10px" }} onClick={() => { setAddUrlOpen(false); setAddUrlValue(""); }}>
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
      ) : tab === "cut" ? (
        <div className="cut-card-list">
          {cards.map((card) => (
            <div key={card.id} className="cut-card-row-wrap">
              <input
                type="checkbox"
                className="res-card-checkbox"
                checked={selected.has(card.id)}
                onChange={() => toggleSelect(card.id)}
                style={{ marginTop: 2, flexShrink: 0 }}
              />
              <CutCardPreview card={card} />
            </div>
          ))}
        </div>
      ) : (
        <div className="res-card-list">
          {cards.map((card) => (
            <CardRow
              key={card.id}
              card={card}
              onApprove={handleApprove}
              onTrash={handleTrash}
              onRestore={handleRestore}
              onSelect={(id) => {
                if (tab === "approved") toggleSelect(id);
                else onSelectCard(id);
              }}
              selected={selected.has(card.id)}
              showSelect={tab === "approved"}
              showTitle={tab === "researched"}
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
