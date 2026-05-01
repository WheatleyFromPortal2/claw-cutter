import { useEffect, useState } from "react";
import { getCard, updateCard, populateCiteFromVerbatim, approveCard, trashCard, restoreCard } from "./api.js";

function CardTextRenderer({ text, underlined = [], highlighted = [] }) {
  if (!text) return null;

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

const FIELDS = [
  { key: "tag", label: "Tag", multiline: true },
  { key: "author", label: "Author" },
  { key: "author_qualifications", label: "Qualifications", multiline: true },
  { key: "date", label: "Date" },
  { key: "title", label: "Title", multiline: true },
  { key: "publisher", label: "Publisher" },
  { key: "url", label: "URL" },
  { key: "initials", label: "Initials" },
  { key: "topic", label: "Topic" },
];

// Fields where null means "unknown" (not just empty)
const CITE_FIELDS = new Set(["author", "author_qualifications", "date", "title", "publisher", "initials"]);

function FieldValue({ fieldKey, value }) {
  if (fieldKey === "url" && value) {
    return <a href={value} target="_blank" rel="noreferrer" className="cv-link">{value}</a>;
  }
  if (value === null && CITE_FIELDS.has(fieldKey)) {
    return (
      <span className="cv-field-unknown">
        <span>⚠</span> Unknown — use "Populate cite" or edit manually
      </span>
    );
  }
  if (!value) {
    return <em style={{ color: "var(--text-muted)" }}>—</em>;
  }
  return <span>{value}</span>;
}

export default function CardViewer({ cardId, onBack }) {
  const [card, setCard] = useState(null);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({});
  const [citeText, setCiteText] = useState("");
  const [showCiteInput, setShowCiteInput] = useState(false);
  const [showPopulateText, setShowPopulateText] = useState(false);
  const [populateText, setPopulateText] = useState("");
  const [saving, setSaving] = useState(false);
  const [populatingCite, setPopulatingCite] = useState(false);
  const [populatingText, setPopulatingText] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await getCard(cardId);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setCard(data);
        setForm(data);
      } catch (e) {
        setError(e.message);
      }
    })();
  }, [cardId]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await updateCard(cardId, {
        tag: form.tag,
        author: form.author,
        author_qualifications: form.author_qualifications,
        date: form.date,
        title: form.title,
        publisher: form.publisher,
        url: form.url,
        initials: form.initials,
        topic: form.topic,
        card_text: form.card_text,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const updated = await res.json();
      setCard(updated);
      setForm(updated);
      setEditing(false);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handlePopulateCite = async () => {
    if (!citeText.trim()) return;
    setPopulatingCite(true);
    try {
      const res = await populateCiteFromVerbatim(cardId, citeText);
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const updated = await res.json();
      setCard(updated);
      setForm(updated);
      setCiteText("");
      setShowCiteInput(false);
    } catch (e) {
      setError(e.message);
    } finally {
      setPopulatingCite(false);
    }
  };

  const handlePopulateText = async () => {
    if (!populateText.trim()) return;
    setPopulatingText(true);
    try {
      const res = await updateCard(cardId, { card_text: populateText });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const updated = await res.json();
      setCard(updated);
      setForm(updated);
      setPopulateText("");
      setShowPopulateText(false);
    } catch (e) {
      setError(e.message);
    } finally {
      setPopulatingText(false);
    }
  };

  const handleStatusAction = async (action) => {
    try {
      let res;
      if (action === "approve") res = await approveCard(cardId);
      else if (action === "trash") res = await trashCard(cardId);
      else if (action === "restore") res = await restoreCard(cardId);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setCard((c) => ({ ...c, card_status: data.card_status }));
    } catch (e) {
      setError(e.message);
    }
  };

  if (error && !card) return (
    <div className="card-viewer">
      <button className="btn-back" onClick={onBack}>← Back</button>
      <div className="error-box">{error}</div>
    </div>
  );

  if (!card) return (
    <div className="card-viewer">
      <button className="btn-back" onClick={onBack}>← Back</button>
      <div className="loading">Loading card…</div>
    </div>
  );

  const hasUnknownCiteFields = CITE_FIELDS.has && [...CITE_FIELDS].some(f => card[f] === null);

  return (
    <div className="card-viewer">
      <div className="cv-header">
        <button className="btn-back" onClick={onBack}>← Back</button>
        <div className="cv-header-actions">
          <span className={`status-badge status-${card.card_status}`}>{card.card_status}</span>
          {card.card_status === "researched" && (
            <button className="res-btn approve" onClick={() => handleStatusAction("approve")}>Approve</button>
          )}
          {(card.card_status === "researched" || card.card_status === "approved") && (
            <button className="res-btn trash" onClick={() => handleStatusAction("trash")}>Trash</button>
          )}
          {card.card_status === "trashed" && (
            <button className="res-btn approve" onClick={() => handleStatusAction("restore")}>Restore</button>
          )}
          <button
            className="btn-secondary"
            onClick={() => { if (editing) handleSave(); else setEditing(true); }}
            disabled={saving}
          >
            {editing ? (saving ? "Saving…" : "Save") : "Edit"}
          </button>
          {editing && (
            <button className="btn-secondary" onClick={() => { setEditing(false); setForm(card); }}>
              Cancel
            </button>
          )}
        </div>
      </div>

      {error && <div className="error-box">{error}</div>}

      {/* Missing full text warning */}
      {card.missing_full_text && (
        <div className="missing-text-banner">
          <span className="missing-text-dot" />
          Full article text could not be fetched automatically. Use "Populate Article Text" below to paste it in.
        </div>
      )}

      {/* Populate cite from Cite Creator */}
      <div className="cv-cite-section">
        <button className="advanced-toggle" onClick={() => setShowCiteInput((v) => !v)}>
          {showCiteInput ? "▲" : "▼"} Populate cite from Cite Creator
        </button>
        {showCiteInput && (
          <div className="cv-cite-input">
            <textarea
              className="prompt-textarea"
              placeholder={'Paste a Verbatim Cite Creator formatted cite here…\nExample: Smith 24 – John Smith, Professor at Harvard, Jan 2024, "Title of Article," Publisher. https://url.com'}
              rows={4}
              value={citeText}
              onChange={(e) => setCiteText(e.target.value)}
            />
            <button
              className="btn-primary"
              style={{ fontSize: 13, padding: "6px 16px" }}
              onClick={handlePopulateCite}
              disabled={!citeText.trim() || populatingCite}
            >
              {populatingCite ? "Populating…" : "Populate Cite Fields"}
            </button>
          </div>
        )}
      </div>

      {/* Populate article text */}
      <div className="cv-populate-section">
        <button className="advanced-toggle" onClick={() => setShowPopulateText((v) => !v)}>
          {showPopulateText ? "▲" : "▼"} Populate article text
        </button>
        {showPopulateText && (
          <div className="cv-populate-input">
            <textarea
              className="prompt-textarea"
              placeholder="Paste the full article text here…"
              rows={8}
              value={populateText}
              onChange={(e) => setPopulateText(e.target.value)}
            />
            <button
              className="btn-primary"
              style={{ fontSize: 13, padding: "6px 16px" }}
              onClick={handlePopulateText}
              disabled={!populateText.trim() || populatingText}
            >
              {populatingText ? "Saving…" : "Set Article Text"}
            </button>
          </div>
        )}
      </div>

      {/* Card metadata fields */}
      <div className="cv-fields">
        {FIELDS.map(({ key, label, multiline }) => (
          <div key={key} className="cv-field-row">
            <span className="cv-field-label">{label}</span>
            {editing ? (
              multiline ? (
                <textarea
                  className="cv-field-input"
                  rows={2}
                  value={form[key] || ""}
                  onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                />
              ) : (
                <input
                  className="cv-field-input"
                  value={form[key] || ""}
                  onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                />
              )
            ) : (
              <span className="cv-field-value">
                <FieldValue fieldKey={key} value={card[key]} />
              </span>
            )}
          </div>
        ))}
      </div>

      {/* Card text */}
      <div className="cv-card-text">
        <div className="cv-card-text-label">Card Text</div>
        {editing ? (
          <textarea
            className="prompt-textarea"
            rows={12}
            value={form.card_text || ""}
            onChange={(e) => setForm((f) => ({ ...f, card_text: e.target.value }))}
          />
        ) : (
          <div className="cv-card-text-body">
            {card.card_text
              ? <CardTextRenderer
                  text={card.card_text}
                  underlined={card.underlined || []}
                  highlighted={card.highlighted || []}
                />
              : <em style={{ color: "var(--text-muted)" }}>No card text.</em>
            }
          </div>
        )}
      </div>
    </div>
  );
}
