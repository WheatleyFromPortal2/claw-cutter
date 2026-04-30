import { useEffect, useState } from "react";
import { getCard, updateCard, populateCiteFromCreator, populateArticleText, approveCard, trashCard, restoreCard } from "./api.js";

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

function FieldValue({ fieldKey, value, card }) {
  if (value === null || value === undefined) {
    return (
      <span className="cv-field-unknown">
        <span className="cv-field-flag" title="Not known with confidence">⚠</span>
        Unknown
      </span>
    );
  }
  if (fieldKey === "url" && value) {
    return <a href={value} target="_blank" rel="noreferrer" className="cv-link">{value}</a>;
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
  const [showArticleInput, setShowArticleInput] = useState(false);
  const [articleText, setArticleText] = useState("");
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
      const res = await populateCiteFromCreator(cardId, citeText);
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
    if (!articleText.trim()) return;
    setPopulatingText(true);
    try {
      const res = await populateArticleText(cardId, articleText);
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const updated = await res.json();
      setCard(updated);
      setForm(updated);
      setArticleText("");
      setShowArticleInput(false);
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

  const isCut = card.card_status === "cut";
  const hasFullText = card.full_text_fetched === "yes";

  return (
    <div className="card-viewer">
      <div className="cv-header">
        <button className="btn-back" onClick={onBack}>← Back</button>
        <div className="cv-header-actions">
          <span className={`status-badge status-${card.card_status}`}>{card.card_status}</span>
          {!hasFullText && (
            <span className="cv-flag-badge" title="Article text was not fetched from the source">⚠ No full text</span>
          )}
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

      {/* Cite Creator input */}
      <div className="cv-cite-section">
        <button className="advanced-toggle" onClick={() => setShowCiteInput((v) => !v)}>
          {showCiteInput ? "▲" : "▼"} Populate cite from Cite Creator
        </button>
        {showCiteInput && (
          <div className="cv-cite-input">
            <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 4 }}>
              Paste a citation in Verbatim Cite Creator format (e.g. <em>Smith 23 (John Smith, Professor, Jan 2023, "Title," Publisher, URL)</em>)
            </div>
            <textarea
              className="prompt-textarea"
              placeholder="Paste Verbatim Cite Creator formatted citation here…"
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
              {populatingCite ? "Populating…" : "Populate Fields"}
            </button>
          </div>
        )}
      </div>

      {/* Populate article text */}
      <div className="cv-cite-section">
        <button className="advanced-toggle" onClick={() => setShowArticleInput((v) => !v)}>
          {showArticleInput ? "▲" : "▼"} Populate article text
          {!hasFullText && <span className="cv-flag-inline"> ⚠ not fetched</span>}
        </button>
        {showArticleInput && (
          <div className="cv-cite-input">
            <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 4 }}>
              Paste the full article text here to use as the card body.
            </div>
            <textarea
              className="prompt-textarea"
              placeholder="Paste article text here…"
              rows={8}
              value={articleText}
              onChange={(e) => setArticleText(e.target.value)}
            />
            <button
              className="btn-primary"
              style={{ fontSize: 13, padding: "6px 16px" }}
              onClick={handlePopulateText}
              disabled={!articleText.trim() || populatingText}
            >
              {populatingText ? "Saving…" : "Save Article Text"}
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
                <FieldValue fieldKey={key} value={card[key]} card={card} />
              </span>
            )}
          </div>
        ))}
      </div>

      {/* Cut card preview (only show when cut) */}
      {isCut && (card.underlined?.length > 0 || card.highlighted?.length > 0) && (
        <div className="cv-cut-preview">
          <div className="cv-card-text-label">Cut Card Preview</div>
          <div className="cv-cut-cite">
            {[card.initials, card.date?.slice(0, 4), card.author, card.author_qualifications, card.title ? `"${card.title}"` : "", card.publisher]
              .filter(Boolean).join(" · ")}
          </div>
          <div className="cv-card-text-body">
            {card.card_text
              ? <CardTextRenderer
                  text={card.card_text}
                  underlined={card.underlined || []}
                  highlighted={card.highlighted || []}
                />
              : <em style={{ color: "var(--text-muted)" }}>No card text available to preview.</em>
            }
          </div>
        </div>
      )}

      {/* Card text (raw) */}
      <div className="cv-card-text">
        <div className="cv-card-text-label">
          Card Text
          {!hasFullText && <span className="cv-flag-inline"> ⚠ article text not fetched</span>}
        </div>
        {editing ? (
          <textarea
            className="prompt-textarea"
            rows={12}
            value={form.card_text || ""}
            onChange={(e) => setForm((f) => ({ ...f, card_text: e.target.value }))}
            style={{ margin: "10px 14px", width: "calc(100% - 28px)" }}
          />
        ) : (
          <div className="cv-card-text-body">
            {card.card_text
              ? <CardTextRenderer
                  text={card.card_text}
                  underlined={card.underlined || []}
                  highlighted={card.highlighted || []}
                />
              : <em style={{ color: "var(--text-muted)" }}>No card text. Use "Populate article text" to add it.</em>
            }
          </div>
        )}
      </div>
    </div>
  );
}
