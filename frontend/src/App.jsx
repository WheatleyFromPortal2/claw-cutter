import { useCallback, useEffect, useRef, useState } from "react";
import JobList from "./JobList.jsx";
import JobStatus from "./JobStatus.jsx";
import { submitJob, listModels } from "./api.js";
import { DEFAULT_UNDERLINE_PROMPT, DEFAULT_HIGHLIGHT_PROMPT } from "./prompts.js";

const HL_COLORS = [
  { value: "yellow", label: "Yellow" },
  { value: "cyan", label: "Cyan" },
  { value: "green", label: "Green" },
  { value: "magenta", label: "Pink" },
];

function TokenGate({ onSave }) {
  const [val, setVal] = useState("");
  return (
    <div className="token-gate">
      <div className="token-gate-box">
        <h2>Enter API Token</h2>
        <p>Enter the shared access token to use Card Tracer.</p>
        <input
          type="password"
          placeholder="Bearer token"
          value={val}
          onChange={(e) => setVal(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && val && onSave(val)}
          autoFocus
        />
        <button
          className="btn-primary"
          disabled={!val}
          onClick={() => onSave(val)}
        >
          Continue
        </button>
      </div>
    </div>
  );
}

export default function App() {
  const [token, setToken] = useState(() => localStorage.getItem("token") || "");
  const [activeJobId, setActiveJobId] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const [models, setModels] = useState([]);

  useEffect(() => {
    listModels()
      .then((r) => r.ok && r.json())
      .then((data) => data && setModels(data))
      .catch(() => {});
  }, [token]);

  // Upload / settings state
  const [file, setFile] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [hlColor, setHlColor] = useState("cyan");
  const [mode, setMode] = useState("all");
  const [topic, setTopic] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [underlinePrompt, setUnderlinePrompt] = useState(DEFAULT_UNDERLINE_PROMPT);
  const [highlightPrompt, setHighlightPrompt] = useState(DEFAULT_HIGHLIGHT_PROMPT);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);

  const fileInputRef = useRef(null);

  const handleSaveToken = (t) => {
    localStorage.setItem("token", t);
    setToken(t);
  };

  const handleFile = (f) => {
    if (f && f.name.endsWith(".docx")) {
      setFile(f);
      setSubmitError(null);
    } else {
      setSubmitError("Only .docx files are supported.");
    }
  };

  const onDragOver = useCallback((e) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const onDragLeave = useCallback(() => setDragging(false), []);

  const onDrop = useCallback((e) => {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files[0];
    if (f) handleFile(f);
  }, []);

  const handleSubmit = async () => {
    if (!file) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const settings = {
        hl_color: hlColor,
        mode,
        topic: mode === "topic_only" ? topic : "",
        underline_prompt: underlinePrompt,
        highlight_prompt: highlightPrompt,
      };
      const data = await submitJob(file, settings);
      setActiveJobId(data.job_id);
      setFile(null);
      setRefreshKey((k) => k + 1);
    } catch (e) {
      setSubmitError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  if (!token) return <TokenGate onSave={handleSaveToken} />;

  if (activeJobId) {
    return (
      <div className="app">
        <Header onTokenReset={() => { localStorage.removeItem("token"); setToken(""); }} />
        <main className="main">
          <JobStatus jobId={activeJobId} onBack={() => { setActiveJobId(null); setRefreshKey((k) => k + 1); }} />
        </main>
      </div>
    );
  }

  return (
    <div className="app">
      <Header onTokenReset={() => { localStorage.removeItem("token"); setToken(""); }} />
      <main className="main two-col">
        {/* Upload panel */}
        <section className="upload-panel">
          <h2 className="section-title">New Job</h2>

          <div
            className={`dropzone ${dragging ? "dragging" : ""} ${file ? "has-file" : ""}`}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            onClick={() => fileInputRef.current?.click()}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".docx"
              style={{ display: "none" }}
              onChange={(e) => e.target.files[0] && handleFile(e.target.files[0])}
            />
            {file ? (
              <>
                <div className="dropzone-icon">📄</div>
                <div className="dropzone-filename">{file.name}</div>
                <div className="dropzone-hint">Click to change file</div>
              </>
            ) : (
              <>
                <div className="dropzone-icon">⬆</div>
                <div className="dropzone-label">Drop a .docx file here</div>
                <div className="dropzone-hint">or click to browse</div>
              </>
            )}
          </div>

          <div className="settings">
            <div className="settings-row">
              <label className="settings-label">Models</label>
              <div className="model-status-list">
                {models.length === 0 ? (
                  <span className="model-status-empty">No models loaded</span>
                ) : (
                  models.map((m) => (
                    <span
                      key={m.id}
                      className={`model-pill ${m.enabled ? "enabled" : "disabled"}`}
                      title={`${m.provider} · preference ${m.preference} · ${m.timeout_secs}s timeout`}
                    >
                      <span className="model-pill-dot" />
                      {m.name}
                      {m.enabled && m.preference === Math.min(...models.filter(x => x.enabled).map(x => x.preference))
                        ? " ★" : ""}
                    </span>
                  ))
                )}
              </div>
            </div>

            <div className="settings-row">
              <label className="settings-label">Highlight Color</label>
              <div className="toggle-group">
                {HL_COLORS.map((c) => (
                  <button
                    key={c.value}
                    className={`toggle-btn color-btn ${hlColor === c.value ? "active" : ""}`}
                    onClick={() => setHlColor(c.value)}
                    style={{ "--swatch": `var(--color-${c.value})` }}
                  >
                    <span className="color-swatch" />
                    {c.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="settings-row">
              <label className="settings-label">Mode</label>
              <div className="toggle-group">
                <button
                  className={`toggle-btn ${mode === "all" ? "active" : ""}`}
                  onClick={() => setMode("all")}
                >
                  Trace All
                </button>
                <button
                  className={`toggle-btn ${mode === "topic_only" ? "active" : ""}`}
                  onClick={() => setMode("topic_only")}
                >
                  Topic Only
                </button>
              </div>
            </div>

            {mode === "topic_only" && (
              <div className="settings-row col">
                <label className="settings-label">Topic</label>
                <textarea
                  className="topic-input"
                  placeholder="Describe the debate topic or resolution…"
                  value={topic}
                  onChange={(e) => setTopic(e.target.value)}
                  rows={3}
                />
              </div>
            )}

            <button
              className="advanced-toggle"
              onClick={() => setShowAdvanced((v) => !v)}
            >
              {showAdvanced ? "▲" : "▼"} Advanced — Edit Prompts
            </button>

            {showAdvanced && (
              <div className="advanced-section">
                <div className="prompt-block">
                  <div className="prompt-block-header">
                    <label>Underline Prompt</label>
                    <button
                      className="btn-reset"
                      onClick={() => setUnderlinePrompt(DEFAULT_UNDERLINE_PROMPT)}
                    >
                      Reset
                    </button>
                  </div>
                  <textarea
                    className="prompt-textarea"
                    value={underlinePrompt}
                    onChange={(e) => setUnderlinePrompt(e.target.value)}
                    rows={12}
                  />
                </div>
                <div className="prompt-block">
                  <div className="prompt-block-header">
                    <label>Highlight Prompt</label>
                    <button
                      className="btn-reset"
                      onClick={() => setHighlightPrompt(DEFAULT_HIGHLIGHT_PROMPT)}
                    >
                      Reset
                    </button>
                  </div>
                  <textarea
                    className="prompt-textarea"
                    value={highlightPrompt}
                    onChange={(e) => setHighlightPrompt(e.target.value)}
                    rows={12}
                  />
                </div>
              </div>
            )}
          </div>

          {submitError && <div className="error-box">{submitError}</div>}

          <button
            className="btn-primary btn-submit"
            disabled={!file || submitting}
            onClick={handleSubmit}
          >
            {submitting ? "Submitting…" : "Trace Cards"}
          </button>
        </section>

        {/* Job list */}
        <section className="list-panel">
          <JobList onSelectJob={setActiveJobId} refreshKey={refreshKey} />
        </section>
      </main>
    </div>
  );
}

function Header({ onTokenReset }) {
  return (
    <header className="header">
      <div className="header-brand">
        <span className="header-logo">✦</span>
        Card Tracer
      </div>
      <button className="btn-token-reset" onClick={onTokenReset} title="Change token">
        Token
      </button>
    </header>
  );
}
