export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface Job {
  id: string;
  title: string;
  status: JobStatus;
  progress: number;
  stage: string | null;
  message: string | null;
  error: string | null;
  result_path: string | null;
  image_count: number | null;
  has_zip: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProgressEvent {
  job_id: string;
  progress: number;
  stage: string;
  message: string;
  status: JobStatus;
}

export type AlbumLayout = "page" | "grid";

export interface CreateAlbumJobInput {
  files: File[];
  title?: string;
  maxWidth: number;
  quality: number;
  stripExif: boolean;
  layout: AlbumLayout;
  gridCols: number;
}

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export async function listJobs(): Promise<Job[]> {
  const res = await fetch(`${API_BASE}/api/jobs`);
  if (!res.ok) throw new Error("Failed to load jobs");
  return res.json();
}

export async function createJob(input: CreateAlbumJobInput): Promise<Job> {
  const form = new FormData();
  for (const file of input.files) {
    form.append("files", file);
  }
  if (input.title?.trim()) form.append("title", input.title.trim());
  form.append("max_width", String(input.maxWidth));
  form.append("quality", String(input.quality));
  form.append("strip_exif", input.stripExif ? "true" : "false");
  form.append("layout", input.layout);
  form.append("grid_cols", String(input.gridCols));

  const res = await fetch(`${API_BASE}/api/jobs`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join(", ")
          : "Failed to create job";
    throw new Error(message);
  }
  return res.json();
}

export async function cancelJob(id: string): Promise<Job> {
  const res = await fetch(`${API_BASE}/api/jobs/${id}/cancel`, { method: "POST" });
  if (!res.ok) throw new Error("Failed to cancel job");
  return res.json();
}

export function downloadUrl(id: string, kind: "pdf" | "zip" = "pdf"): string {
  return `${API_BASE}/api/jobs/${id}/download?kind=${kind}`;
}

export function subscribeJobEvents(
  id: string,
  onEvent: (event: ProgressEvent) => void,
  onError?: (err: Event) => void,
): () => void {
  const source = new EventSource(`${API_BASE}/api/jobs/${id}/events`);
  source.onmessage = (msg) => {
    try {
      onEvent(JSON.parse(msg.data) as ProgressEvent);
    } catch {
      /* ignore malformed */
    }
  };
  source.onerror = (err) => {
    onError?.(err);
  };
  return () => source.close();
}
