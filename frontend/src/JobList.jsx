import { useEffect, useState } from "react";
import { listJobs, deleteJob } from "./api.js";

const STATUS_LABEL = {
  queued: "Queued",
  running: "Running",
  done: "Done",
  error: "Error",
};

function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso + "Z");
  return d.toLocaleString();
}

export default function JobList({ onSelectJob, refreshKey, role }) {
  const [jobs, setJobs] = useState([]);
  const [error, setError] = useState(null);

  const fetchJobs = async () => {
    try {
      const res = await listJobs();
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setJobs(await res.json());
    } catch (e) {
      setError(e.message);
    }
  };

  useEffect(() => {
    fetchJobs();
  }, [refreshKey]);

  const handleDelete = async (e, id) => {
    e.stopPropagation();
    if (!confirm("Delete this job?")) return;
    try {
      await deleteJob(id);
      setJobs((prev) => prev.filter((j) => j.id !== id));
    } catch (e) {
      alert("Delete failed: " + e.message);
    }
  };

  if (error) return <div className="error-box">{error}</div>;
  if (!jobs.length)
    return <div className="empty-state">No jobs yet. Upload a .docx file to get started.</div>;

  return (
    <div className="job-list">
      <div className="job-list-title">Recent Jobs</div>
      <table className="job-table">
        <thead>
          <tr>
            <th>File</th>
            <th>Status</th>
            <th>Progress</th>
            <th>Created</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr
              key={job.id}
              className="job-row"
              onClick={() => onSelectJob(job.id)}
            >
              <td className="job-filename-cell">{job.filename}</td>
              <td>
                <span className={`status-badge status-${job.status}`}>
                  {STATUS_LABEL[job.status] || job.status}
                </span>
              </td>
              <td>
                <div className="mini-progress-track">
                  <div
                    className="mini-progress-fill"
                    style={{ width: `${job.progress}%` }}
                  />
                </div>
              </td>
              <td className="date-cell">{formatDate(job.created_at)}</td>
              <td>
                {role === "admin" && (
                  <button
                    className="btn-delete"
                    onClick={(e) => handleDelete(e, job.id)}
                  >
                    ✕
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
