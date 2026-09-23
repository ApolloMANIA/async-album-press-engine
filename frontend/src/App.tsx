import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import {
  Job,
  JobStatus,
  cancelJob,
  createJob,
  downloadUrl,
  listJobs,
  subscribeJobEvents,
} from "./api";
import "./App.css";

const ACTIVE: JobStatus[] = ["queued", "running"];

export default function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [title, setTitle] = useState("Quarterly sales report");
  const [rowCount, setRowCount] = useState(40);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const subscribed = useRef(new Set<string>());

  const upsertJob = useCallback((next: Job) => {
    setJobs((prev) => {
      const idx = prev.findIndex((j) => j.id === next.id);
      if (idx === -1) return [next, ...prev];
      const copy = [...prev];
      copy[idx] = { ...copy[idx], ...next };
      return copy;
    });
  }, []);

  const attachStream = useCallback(
    (id: string) => {
      if (subscribed.current.has(id)) return;
      subscribed.current.add(id);
      const stop = subscribeJobEvents(id, (event) => {
        setJobs((prev) =>
          prev.map((j) =>
            j.id === id
              ? {
                  ...j,
                  progress: event.progress,
                  stage: event.stage,
                  message: event.message,
                  status: event.status,
                }
              : j,
          ),
        );
        if (!ACTIVE.includes(event.status)) {
          subscribed.current.delete(id);
          stop();
        }
      });
    },
    [],
  );

  useEffect(() => {
    listJobs()
      .then((data) => {
        setJobs(data);
        data.filter((j) => ACTIVE.includes(j.status)).forEach((j) => attachStream(j.id));
      })
      .catch((err) => setError(String(err.message ?? err)));
  }, [attachStream]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const job = await createJob(title.trim() || "Untitled report", rowCount);
      upsertJob(job);
      attachStream(job.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function onCancel(id: string) {
    try {
      const job = await cancelJob(id);
      upsertJob(job);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="shell">
      <header className="header">
        <div>
          <p className="eyebrow">async-report-engine</p>
          <h1>PDF job console</h1>
        </div>
        <p className="lede">
          Submit a report job. Celery workers pick it up, stream stage progress over Redis pub/sub,
          and this UI follows along via SSE.
        </p>
      </header>

      <section className="submit-panel">
        <form onSubmit={onSubmit} className="submit-form">
          <label>
            Report title
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Monthly operations brief"
              required
            />
          </label>
          <label>
            Sample rows
            <input
              type="number"
              min={1}
              max={500}
              value={rowCount}
              onChange={(e) => setRowCount(Number(e.target.value))}
            />
          </label>
          <button type="submit" disabled={submitting}>
            {submitting ? "Enqueueing…" : "Enqueue PDF job"}
          </button>
        </form>
        {error && <p className="error">{error}</p>}
      </section>

      <section className="jobs">
        <div className="jobs-head">
          <h2>Jobs</h2>
          <span className="muted">{jobs.length} total</span>
        </div>
        {jobs.length === 0 ? (
          <p className="empty">No jobs yet. Enqueue one above.</p>
        ) : (
          <ul className="job-list">
            {jobs.map((job) => (
              <li key={job.id} className="job-row">
                <div className="job-top">
                  <div>
                    <strong>{job.title}</strong>
                    <code className="id">{job.id.slice(0, 8)}</code>
                  </div>
                  <span className={`badge badge-${job.status}`}>{job.status}</span>
                </div>
                <div className="bar-track" aria-hidden>
                  <div className="bar-fill" style={{ width: `${job.progress}%` }} />
                </div>
                <div className="job-meta">
                  <span>
                    {job.progress}% · {job.stage ?? "—"}
                  </span>
                  <span className="muted">{job.message ?? ""}</span>
                </div>
                <div className="job-actions">
                  {ACTIVE.includes(job.status) && (
                    <button type="button" className="ghost" onClick={() => onCancel(job.id)}>
                      Cancel
                    </button>
                  )}
                  {job.status === "completed" && (
                    <a className="download" href={downloadUrl(job.id)}>
                      Download PDF
                    </a>
                  )}
                  {job.status === "failed" && job.error && (
                    <span className="error-inline">{job.error}</span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
