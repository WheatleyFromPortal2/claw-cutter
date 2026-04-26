import { useEffect, useRef, useState } from "react";
import { getJob, downloadJob } from "./api.js";
import { formatBytes, formatSecs } from "./App.jsx";

const STATUS_LABEL = {
  queued: "Queued",
  running: "Running",
  done: "Done",
  error: "Error",
};

export default function JobStatus({ jobId, onBack }) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const [downloading, setDownloading] = useState(false);
  const intervalRef = useRef(null);
  const logEndRef = useRef(null);

  const fetchJob = async () => {
    try {
      const res = await getJob(jobId);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setJob(data);
      if (data.status === "done" || data.status === "error") {
        clearInterval(intervalRef.current);
      }
    } catch (e) {
      setError(e.message);
      clearInterval(intervalRef.current);
    }
  };

  useEffect(() => {
    fetchJob();
    intervalRef.current = setInterval(fetchJob, 2000);
    return () => clearInterval(intervalRef.current);
  }, [jobId]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [job?.card_log?.length]);

  const handleDownload = async () => {
    setDownloading(true);
    try {
      await downloadJob(jobId, job.filename);
    } catch (e) {
      setError(e.message);
    } finally {
      setDownloading(false);
    }
  };

  if (error) {
    return (
      <div className="job-status">
        <button className="btn-back" onClick={onBack}>← Back</button>
        <div className="error-box">Error: {error}</div>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="job-status">
        <button className="btn-back" onClick={onBack}>← Back</button>
        <div className="loading">Loading…</div>
      </div>
    );
  }

  const isActive = job.status === "queued" || job.status === "running";
  const statusClass = `status-badge status-${job.status}`;

  return (
    <div className="job-status">
      <div className="job-status-header">
        <button className="btn-back" onClick={onBack}>← Back</button>
        <div className="job-meta">
          <span className="job-filename">{job.filename}</span>
          <span className={statusClass}>{STATUS_LABEL[job.status] || job.status}</span>
        </div>
      </div>

      <div className="progress-section">
        <div className="progress-label">
          {job.cards_total > 0
            ? `${job.cards_done} / ${job.cards_total} cards`
            : isActive
            ? "Parsing document…"
            : ""}
        </div>
        <div className="progress-bar-track">
          <div
            className="progress-bar-fill"
            style={{ width: `${job.progress}%` }}
          />
        </div>
        <div className="progress-pct">{job.progress}%</div>
      </div>

      {job.status === "error" && job.error && (
        <div className="error-box">{job.error}</div>
      )}

      {job.status === "done" && (
        <>
          <div className="done-section">
            <button
              className="btn-primary btn-download"
              onClick={handleDownload}
              disabled={downloading}
            >
              {downloading ? "Downloading…" : `Download ${job.filename.replace(/\.docx$/i, "")}_CUT.docx`}
            </button>
          </div>

          <div className="job-stats">
            <div className="job-stats-title">Statistics</div>
            <div className="job-stats-grid">
              <div className="stat-item">
                <span className="stat-value">{(job.tokens_input + job.tokens_output).toLocaleString()}</span>
                <span className="stat-label">Tokens ({job.tokens_input.toLocaleString()} in / {job.tokens_output.toLocaleString()} out)</span>
              </div>
              <div className="stat-item">
                <span className="stat-value">{formatSecs(job.processing_secs)}</span>
                <span className="stat-label">Processing time</span>
              </div>
              <div className="stat-item">
                <span className="stat-value">{job.underlines}</span>
                <span className="stat-label">Underlines</span>
              </div>
              <div className="stat-item">
                <span className="stat-value">{job.highlights}</span>
                <span className="stat-label">Highlights</span>
              </div>
              <div className="stat-item">
                <span className="stat-value">{formatBytes(job.filesize)}</span>
                <span className="stat-label">File size</span>
              </div>
            </div>
          </div>
        </>
      )}

      {job.card_log && job.card_log.length > 0 && (
        <div className="card-log">
          <div className="card-log-title">Card Log</div>
          <div className="card-log-list">
            {job.card_log.map((entry, i) => (
              <div
                key={i}
                className={`card-log-entry ${entry.skipped ? "skipped" : "cut"}`}
              >
                <span className="card-log-index">
                  [{i + 1}/{job.cards_total}]
                </span>
                <span className="card-log-tag">{entry.tag}</span>
                {entry.skipped ? (
                  <span className="card-log-result skipped-label">skipped</span>
                ) : (
                  <span className="card-log-result">
                    ✓ {entry.ul_count} UL · {entry.hl_count} HL
                  </span>
                )}
              </div>
            ))}
            <div ref={logEndRef} />
          </div>
        </div>
      )}
    </div>
  );
}
