import { useEffect, useState } from "react";
import { getCard, updateCard, approveCard, trashCard, restoreCard, parseCiteCreator, populateArticleText } from "./api.js";

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

export default function CardViewer({ cardId, onBack }) {
  const [card, setCard] = useState(null);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({});
  const [citeText, setCiteText] = useState("");
  const [showCiteInput, setShowCiteInput] = useState(false);
  const [showArticleText, setShowArticleText] = useState(false);
  const [articleTextInput, setArticleTextInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [generatingCite, setGeneratingCite] = useState(false);
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

  const handleParseCiteCreator = async () => {
    if (!citeText.trim()) return;
    setGeneratingCite(true);
    try {
      const res = await parseCiteCreator(cardId, citeText);
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
      setGeneratingCite(false);
    }
  };

  const handlePopulateArticleText = async () => {
    if (!articleTextInput.trim()) return;
    setPopulatingText(true);
    try {
      const res = await populateArticleText(cardId, articleTextInput);
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const updated = await res.json();
      setCard(updated);
      setForm(updated);
      setArticleTextInput("");
      setShowArticleText(false);
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

  const cite = [card.initials, card.date?.slice(0, 4), card.author, card.author_qualifications, card.date, card.title ? `"${card.title}"` : "", card.publisher, card.url]
    .filter(Boolean).join(" · ");

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
            className={`btn-secondary ${editing ? "" : ""}`}
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

      {/* Cite Creator */}
      <div className="cv-cite-section">
        <button className="advanced-toggle" onClick={() => setShowCiteInput((v) => !v)}>
          {showCiteInput ? "▲" : "▼"} Populate cite from Cite Creator
        </button>
        {showCiteInput && (
          <div className="cv-cite-input">
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}>
              Paste a cite in Verbatim Cite Creator format — fields will be extracted and populated automatically.
            </div>
            <textarea
              className="prompt-textarea"
              placeholder="Paste Cite Creator formatted cite here…"
              rows={5}
              value={citeText}
              onChange={(e) => setCiteText(e.target.value)}
            />
            <button
              className="btn-primary"
              style={{ fontSize: 13, padding: "6px 16px" }}
              onClick={handleParseCiteCreator}
              disabled={!citeText.trim() || generatingCite}
            >
              {generatingCite ? "Parsing…" : "Populate Fields"}
            </button>
          </div>
        )}
      </div>

      {/* Populate article text */}
      <div className="cv-cite-section">
        <button className="advanced-toggle" onClick={() => setShowArticleText((v) => !v)}>
          {showArticleText ? "▲" : "▼"} Populate article text
          {!card.full_text_fetched && (
            <span style={{ marginLeft: 8, color: "var(--warning)", fontSize: 11 }}>
              ⚠ Full article text not fetched
            </span>
          )}
        </button>
        {showArticleText && (
          <div className="cv-cite-input">
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}>
              Paste the full article text here to use as the card body.
            </div>
            <textarea
              className="prompt-textarea"
              placeholder="Paste article text here…"
              rows={8}
              value={articleTextInput}
              onChange={(e) => setArticleTextInput(e.target.value)}
            />
            <button
              className="btn-primary"
              style={{ fontSize: 13, padding: "6px 16px" }}
              onClick={handlePopulateArticleText}
              disabled={!articleTextInput.trim() || populatingText}
            >
              {populatingText ? "Saving…" : "Set as Card Text"}
            </button>
          </div>
        )}
      </div>

      {/* Low-confidence warning */}
      {(card.low_confidence_fields || []).length > 0 && (
        <div className="cv-low-confidence-warning">
          ⚠ Low-confidence fields (not known with high certainty): {(card.low_confidence_fields || []).join(", ")}
        </div>
      )}

      {/* Card metadata fields */}
      <div className="cv-fields">
        {FIELDS.map(({ key, label, multiline }) => {
          const isLowConfidence = (card.low_confidence_fields || []).includes(key);
          return (
            <div key={key} className={`cv-field-row ${isLowConfidence ? "cv-field-low-confidence" : ""}`}>
              <span className="cv-field-label">
                {label}
                {isLowConfidence && <span className="cv-confidence-flag" title="Low confidence">⚠</span>}
              </span>
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
                  {key === "url" && card[key]
                    ? <a href={card[key]} target="_blank" rel="noreferrer" className="cv-link">{card[key]}</a>
                    : (card[key] != null && card[key] !== ""
                        ? card[key]
                        : <em className={isLowConfidence ? "cv-null-value" : ""} style={{ color: "var(--text-muted)" }}>
                            {isLowConfidence ? "unknown" : "—"}
                          </em>
                      )}
                </span>
              )}
            </div>
          );
        })}
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
