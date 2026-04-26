import { useCallback, useEffect, useRef, useState } from "react";
import JobList from "./JobList.jsx";
import JobStatus from "./JobStatus.jsx";
import { submitJob, listModels, getStats, getPrompts } from "./api.js";

const HL_COLORS = [
  { value: "yellow", label: "Yellow" },
  { value: "cyan", label: "Cyan" },
  { value: "green", label: "Green" },
  { value: "magenta", label: "Pink" },
];

const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10 MB

function TokenGate({ onSave }) {
  const [val, setVal] = useState("");
  return (
    <div className="token-gate">
      <div className="token-gate-box">
        <h2>Enter API Token</h2>
        <p>Enter the shared access token to use Claw Cutter.</p>
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

function GlobalStats({ refreshKey }) {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    getStats()
      .then((r) => r.ok && r.json())
      .then((data) => data && setStats(data))
      .catch(() => {});
  }, [refreshKey]);

  if (!stats || stats.jobs_completed === 0) return null;

  return (
    <div className="global-stats">
      <div className="global-stats-title">Global Statistics</div>
      <div className="global-stats-grid">
        <div className="stat-item">
          <span className="stat-value">{stats.jobs_completed}</span>
          <span className="stat-label">Jobs</span>
        </div>
        <div className="stat-item">
          <span className="stat-value">{(stats.tokens_input + stats.tokens_output).toLocaleString()}</span>
          <span className="stat-label">Tokens</span>
        </div>
        <div className="stat-item">
          <span className="stat-value">{stats.underlines.toLocaleString()}</span>
          <span className="stat-label">Underlines</span>
        </div>
        <div className="stat-item">
          <span className="stat-value">{stats.highlights.toLocaleString()}</span>
          <span className="stat-label">Highlights</span>
        </div>
        <div className="stat-item">
          <span className="stat-value">{formatBytes(stats.filesize)}</span>
          <span className="stat-label">Processed</span>
        </div>
        <div className="stat-item">
          <span className="stat-value">{formatSecs(stats.processing_secs)}</span>
          <span className="stat-label">CPU Time</span>
        </div>
      </div>
    </div>
  );
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatSecs(secs) {
  if (!secs) return "0s";
  if (secs < 60) return `${secs.toFixed(1)}s`;
  const m = Math.floor(secs / 60);
  const s = Math.round(secs % 60);
  return `${m}m ${s}s`;
}

export { formatBytes, formatSecs };

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

  useEffect(() => {
    getPrompts()
      .then((r) => r.ok && r.json())
      .then((data) => {
        if (data) {
          setDefaultPrompts(data);
          setUnderlinePrompt((p) => p || data.underline);
          setHighlightPrompt((p) => p || data.highlight);
        }
      })
      .catch(() => {});
  }, [token]);

  // Upload / settings state
  const [file, setFile] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [hlColor, setHlColor] = useState("cyan");
  const [mode, setMode] = useState("all");
  const [topic, setTopic] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [underlinePrompt, setUnderlinePrompt] = useState("");
  const [highlightPrompt, setHighlightPrompt] = useState("");
  const [defaultPrompts, setDefaultPrompts] = useState({ underline: "", highlight: "" });
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);

  const fileInputRef = useRef(null);

  const handleSaveToken = (t) => {
    localStorage.setItem("token", t);
    setToken(t);
  };

  const handleFile = (f) => {
    if (!f || !f.name.endsWith(".docx")) {
      setSubmitError("Only .docx files are supported.");
      return;
    }
    if (f.size > MAX_FILE_SIZE) {
      setSubmitError("File exceeds the 10 MB size limit.");
      return;
    }
    setFile(f);
    setSubmitError(null);
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
                <div className="dropzone-hint">or click to browse · max 10 MB</div>
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
                  Cut All
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
                      onClick={() => setUnderlinePrompt(defaultPrompts.underline)}
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
                      onClick={() => setHighlightPrompt(defaultPrompts.highlight)}
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
            {submitting ? "Submitting…" : "Cut Cards"}
          </button>
        </section>

        {/* Job list */}
        <section className="list-panel">
          <GlobalStats refreshKey={refreshKey} />
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
        Claw Cutter
      </div>
      <button className="btn-token-reset" onClick={onTokenReset} title="Change token">
        Token
      </button>
    </header>
  );
}
