import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  AlbumLayout,
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

const STAGE_LABELS: Record<string, string> = {
  queued: "Waiting to start",
  validate: "Checking upload",
  extract: "Unpacking ZIP",
  process: "Processing photos",
  pack: "Building ZIP",
  render_pdf: "Building album PDF",
  finalize: "Album ready",
  cancelled: "Cancelled",
  failed: "Something went wrong",
};

function stageLabel(stage: string | null | undefined, status: JobStatus): string {
  if (status === "queued") return STAGE_LABELS.queued;
  if (status === "cancelled") return STAGE_LABELS.cancelled;
  if (status === "failed") return STAGE_LABELS.failed;
  if (!stage) return status;
  return STAGE_LABELS[stage] ?? stage;
}

function statusLabel(status: JobStatus): string {
  switch (status) {
    case "queued":
      return "Queued";
    case "running":
      return "Processing";
    case "completed":
      return "Ready";
    case "failed":
      return "Failed";
    case "cancelled":
      return "Cancelled";
  }
}

function statusTone(status: JobStatus): string {
  if (status === "completed") return "up";
  if (status === "failed" || status === "cancelled") return "down";
  if (status === "running") return "live";
  return "neutral";
}

function buildChartPath(values: number[], width: number, height: number, pad = 8) {
  if (values.length === 0) return { line: "", area: "" };
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 100);
  const span = Math.max(max - min, 1);
  const innerW = width - pad * 2;
  const innerH = height - pad * 2;
  const pts = values.map((v, i) => {
    const x = pad + (values.length === 1 ? innerW / 2 : (i / (values.length - 1)) * innerW);
    const y = pad + innerH - ((v - min) / span) * innerH;
    return [x, y] as const;
  });
  const line = pts.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const area = `${line} L${pts[pts.length - 1][0].toFixed(1)} ${height - pad} L${pts[0][0].toFixed(1)} ${height - pad} Z`;
  return { line, area };
}

export default function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [title, setTitle] = useState("Vacation album");
  const [files, setFiles] = useState<File[]>([]);
  const [maxWidth, setMaxWidth] = useState(1600);
  const [quality, setQuality] = useState(85);
  const [stripExif, setStripExif] = useState(true);
  const [layout, setLayout] = useState<AlbumLayout>("page");
  const [gridCols, setGridCols] = useState(2);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
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

  const attachStream = useCallback((id: string) => {
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
        // refresh to pick up has_zip / image_count
        listJobs()
          .then(setJobs)
          .catch(() => undefined);
      }
    });
  }, []);

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
    if (files.length === 0) {
      setError("Add a ZIP or one or more photos");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const job = await createJob({
        files,
        title: title.trim() || undefined,
        maxWidth,
        quality,
        stripExif,
        layout,
        gridCols,
      });
      upsertJob(job);
      attachStream(job.id);
      setPanelOpen(false);
      setFiles([]);
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

  const stats = useMemo(() => {
    const ready = jobs.filter((j) => j.status === "completed").length;
    const failed = jobs.filter((j) => j.status === "failed" || j.status === "cancelled").length;
    const working = jobs.filter((j) => j.status === "running" || j.status === "queued").length;
    const photos = jobs.reduce((sum, j) => sum + (j.image_count ?? 0), 0);
    const latest = jobs[0] ?? null;
    return { ready, failed, working, photos, latest, total: jobs.length };
  }, [jobs]);

  const chartValues = useMemo(() => {
    const recent = [...jobs].slice(0, 12).reverse();
    if (recent.length === 0) {
      return [10, 22, 18, 35, 48, 42, 60, 72, 65, 82, 90, 96];
    }
    return recent.map((j) => Math.max(j.progress, 4));
  }, [jobs]);

  const chart = useMemo(() => buildChartPath(chartValues, 720, 220), [chartValues]);
  const isWorking = stats.working > 0;
  const fileLabel =
    files.length === 0
      ? "Drop a ZIP or photos"
      : files.length === 1
        ? files[0].name
        : `${files.length} files selected`;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden>
            A
          </span>
          <div>
            <div className="brand-row">
              <strong>Album Press</strong>
              <span className="pill">Batch</span>
            </div>
            <p className="brand-sub">Photo batch → ZIP + PDF</p>
          </div>
        </div>
        <div className="top-actions">
          <button type="button" className="chip accent-chip" onClick={() => setPanelOpen(true)}>
            New album
          </button>
        </div>
      </header>

      <main className="dashboard">
        <section className="intro">
          <div className="intro-copy">
            <p className="eyebrow">Celery-backed image pipeline</p>
            <h1 className="intro-title">Batch your photos without blocking the API.</h1>
            <p className="intro-lede">
              Drop a ZIP or a stack of images. Workers resize, strip EXIF, pack a compressed ZIP, and
              build a printable album PDF — with live progress the whole way.
            </p>
            <div className="feature-row">
              <span>Resize &amp; compress</span>
              <span>EXIF strip</span>
              <span>Album PDF</span>
              <span>Download ZIP</span>
            </div>
          </div>
          <button type="button" className="primary intro-cta" onClick={() => setPanelOpen(true)}>
            Upload photos
          </button>
        </section>

        <section className="hero-card">
          <div className="hero-head">
            <div>
              <p className="asset-label">Latest job</p>
              <div className="price-row">
                <h2 className="price">{stats.latest ? `${stats.latest.progress}%` : "Ready"}</h2>
                <div className="deltas">
                  <span className={`delta ${stats.ready ? "up" : "neutral"}`}>{stats.ready} ready</span>
                  <span className={`delta ${isWorking ? "live" : "neutral"}`}>
                    {stats.working} running
                  </span>
                </div>
              </div>
              <p className="hero-meta">
                {stats.latest
                  ? `${stats.latest.title} · ${stageLabel(stats.latest.stage, stats.latest.status)}`
                  : "Upload a ZIP or photos to start your first album"}
              </p>
            </div>
            <div className="hero-side">
              <span className={`live-dot ${isWorking ? "on" : ""}`} />
              <span className="muted">{isWorking ? "Workers busy" : "Workers idle"}</span>
            </div>
          </div>

          <div className="chart-wrap" aria-hidden>
            <svg viewBox="0 0 720 220" className="chart" preserveAspectRatio="none">
              <defs>
                <linearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="rgba(61, 214, 140, 0.28)" />
                  <stop offset="100%" stopColor="rgba(61, 214, 140, 0)" />
                </linearGradient>
              </defs>
              {[0.25, 0.5, 0.75].map((t) => (
                <line key={t} x1="0" x2="720" y1={220 * t} y2={220 * t} className="grid-line" />
              ))}
              <path d={chart.area} fill="url(#areaFill)" />
              <path d={chart.line} className="chart-line" />
            </svg>
            <p className="chart-caption">Job progress across recent albums</p>
          </div>

          <div className="kpi-ribbon">
            <div className="kpi">
              <span className="kpi-label">Albums</span>
              <strong className="kpi-value">{stats.total}</strong>
            </div>
            <div className="kpi">
              <span className="kpi-label">In progress</span>
              <strong className="kpi-value accent">{stats.working}</strong>
            </div>
            <div className="kpi">
              <span className="kpi-label">Ready</span>
              <strong className="kpi-value up">{stats.ready}</strong>
            </div>
            <div className="kpi">
              <span className="kpi-label">Photos processed</span>
              <strong className="kpi-value">{stats.photos}</strong>
            </div>
            <div className="kpi">
              <span className="kpi-label">Issues</span>
              <strong className="kpi-value down">{stats.failed}</strong>
            </div>
            <div className="kpi">
              <span className="kpi-label">Outputs</span>
              <strong className="kpi-value">PDF + ZIP</strong>
            </div>
          </div>
        </section>

        <section className="jobs-panel">
          <div className="section-head">
            <h2>Your albums</h2>
            <span className="muted">{jobs.length} total</span>
          </div>

          {jobs.length === 0 ? (
            <div className="empty-state">
              <p>No albums yet</p>
              <span>
                Upload a vacation ZIP or a handful of JPEGs. Workers handle resize, EXIF strip, ZIP,
                and PDF off the request path.
              </span>
              <button type="button" className="primary" onClick={() => setPanelOpen(true)}>
                Create album
              </button>
            </div>
          ) : (
            <ul className="job-list">
              {jobs.map((job) => (
                <li key={job.id} className="job-row">
                  <div className="job-main">
                    <div className="job-title-row">
                      <strong>{job.title}</strong>
                      <span className={`badge tone-${statusTone(job.status)}`}>
                        {statusLabel(job.status)}
                      </span>
                    </div>
                    <div className="bar-track" aria-hidden>
                      <div className="bar-fill" style={{ width: `${job.progress}%` }} />
                    </div>
                    <div className="job-meta">
                      <span>
                        {job.progress}% · {stageLabel(job.stage, job.status)}
                        {job.image_count != null ? ` · ${job.image_count} photos` : ""}
                      </span>
                      {job.message && job.status === "running" && (
                        <span className="muted">{job.message}</span>
                      )}
                    </div>
                  </div>
                  <div className="job-actions">
                    {ACTIVE.includes(job.status) && (
                      <button type="button" className="ghost" onClick={() => onCancel(job.id)}>
                        Stop
                      </button>
                    )}
                    {job.status === "completed" && (
                      <>
                        <a className="primary-link" href={downloadUrl(job.id, "pdf")}>
                          PDF
                        </a>
                        {job.has_zip && (
                          <a className="primary-link" href={downloadUrl(job.id, "zip")}>
                            ZIP
                          </a>
                        )}
                      </>
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

        <section className="howto">
          <h2>How it works</h2>
          <ol>
            <li>
              <strong>Upload</strong> a ZIP or multiple images (jpg, png, webp, …)
            </li>
            <li>
              <strong>Choose</strong> max width, JPEG quality, EXIF strip, page or grid PDF
            </li>
            <li>
              <strong>Workers</strong> process each photo with live progress over SSE
            </li>
            <li>
              <strong>Download</strong> a compressed ZIP and a printable album PDF
            </li>
          </ol>
        </section>
      </main>

      {panelOpen && (
        <div className="drawer-backdrop" onClick={() => setPanelOpen(false)} role="presentation">
          <aside
            className="drawer"
            onClick={(e) => e.stopPropagation()}
            aria-label="Create album"
          >
            <div className="drawer-head">
              <h2>New album</h2>
              <button
                type="button"
                className="icon-btn"
                onClick={() => setPanelOpen(false)}
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <p className="drawer-hint">
              Up to 100&nbsp;MB · ZIP or images · workers resize, strip EXIF, pack ZIP + PDF
            </p>
            <form onSubmit={onSubmit} className="drawer-form">
              <label className="dropzone">
                <input
                  type="file"
                  accept=".zip,image/*,.jpg,.jpeg,.png,.webp,.gif"
                  multiple
                  onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
                  required={files.length === 0}
                />
                <span className="drop-title">{fileLabel}</span>
                <span className="muted">or click to browse</span>
              </label>

              <label>
                Album title
                <input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="e.g. Portugal 2026"
                />
              </label>

              <div className="date-grid">
                <label>
                  Max width (px)
                  <input
                    type="number"
                    min={400}
                    max={4000}
                    value={maxWidth}
                    onChange={(e) => setMaxWidth(Number(e.target.value))}
                  />
                </label>
                <label>
                  JPEG quality
                  <input
                    type="number"
                    min={40}
                    max={95}
                    value={quality}
                    onChange={(e) => setQuality(Number(e.target.value))}
                  />
                </label>
              </div>

              <label className="check-row">
                <input
                  type="checkbox"
                  checked={stripExif}
                  onChange={(e) => setStripExif(e.target.checked)}
                />
                Strip EXIF metadata
              </label>

              <div className="date-grid">
                <label>
                  PDF layout
                  <select
                    value={layout}
                    onChange={(e) => setLayout(e.target.value as AlbumLayout)}
                  >
                    <option value="page">One photo per page</option>
                    <option value="grid">Contact sheet grid</option>
                  </select>
                </label>
                <label>
                  Grid columns
                  <input
                    type="number"
                    min={1}
                    max={4}
                    value={gridCols}
                    disabled={layout !== "grid"}
                    onChange={(e) => setGridCols(Number(e.target.value))}
                  />
                </label>
              </div>

              {error && <p className="error">{error}</p>}

              <div className="drawer-actions">
                <button type="button" className="ghost" onClick={() => setPanelOpen(false)}>
                  Cancel
                </button>
                <button type="submit" className="primary" disabled={submitting || files.length === 0}>
                  {submitting ? "Starting…" : "Start batch"}
                </button>
              </div>
            </form>
          </aside>
        </div>
      )}

      <nav className="dock" aria-label="Quick actions">
        <button
          type="button"
          className="dock-btn active"
          title="Home"
          onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
        >
          <span />
        </button>
        <button
          type="button"
          className="dock-btn"
          title="New album"
          onClick={() => setPanelOpen(true)}
        >
          <span className="plus" />
        </button>
        <button
          type="button"
          className="dock-btn"
          title="Your albums"
          onClick={() =>
            document.querySelector(".jobs-panel")?.scrollIntoView({ behavior: "smooth" })
          }
        >
          <span className="bars" />
        </button>
      </nav>
    </div>
  );
}
