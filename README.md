# Async Report Engine

Distributed real-time PDF report processing showcase: FastAPI accepts jobs, Celery workers render PDFs, Redis pub/sub streams progress, and a React console follows along over SSE.

## Architecture

```
React UI  --POST /api/jobs-->  FastAPI
   |                              |
   | SSE /events                  +--> PostgreSQL (job metadata)
   |                              |
   +<-- Redis pub/sub <--+        +--> Celery enqueue
                         |              |
                    progress events     v
                                   Celery workers (Docker, scalable)
                                         |
                                         +--> PDF files (/data/pdfs)
                                         +--> status updates in Postgres
```

**Concepts demonstrated:** decoupled API/workers, async pipelines, Redis broker + pub/sub, horizontal worker scaling, rate limiting, retries/failed-job handling, SSE for live progress.

## Quick start

Requires Docker Engine with the Compose plugin (`docker compose`).

```bash
cd ~/repos/async-report-engine
docker compose up --build
```

- UI: http://localhost:5173  
- API docs: http://localhost:8000/docs  
- Health: http://localhost:8000/health  

### Scale workers

```bash
docker compose up --build --scale worker=3
```

Watch multiple workers consume jobs from the shared Redis queue.

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/jobs` | Enqueue a PDF report (rate-limited) |
| `GET` | `/api/jobs` | List recent jobs |
| `GET` | `/api/jobs/{id}` | Job status snapshot |
| `GET` | `/api/jobs/{id}/events` | SSE progress stream |
| `GET` | `/api/jobs/{id}/download` | Download completed PDF |
| `POST` | `/api/jobs/{id}/cancel` | Best-effort cancel (Celery revoke) |

Example submit:

```bash
curl -X POST http://localhost:8000/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"title":"Ops brief","row_count":50}'
```

## Job stages

1. `validate` (~5%) — payload checks  
2. `build_document` (~40%) — structure report content  
3. `render_pdf` (~80%) — ReportLab write  
4. `finalize` (100%) — mark completed, ready to download  

Failures retry (Celery `max_retries=2`); after exhaustion the job is `failed` with an error message.

## Stack

- **FastAPI** — gateway + SSE  
- **Celery + Redis** — queue, results, progress pub/sub, rate-limit window  
- **PostgreSQL** — durable job records  
- **ReportLab** — PDF generation  
- **React + Vite + TypeScript** — job console  
- **Docker Compose** — `api`, `worker`, `redis`, `db`, `frontend`

## Local backend (without full Compose)

Requires Redis and Postgres reachable with the URLs in `.env.example`.

```bash
cp .env.example .env
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
# other terminal:
celery -A app.celery_app.celery worker --loglevel=info
```

```bash
cd frontend
npm install
npm run dev
```

## Resume talking points

- Why long-running work leaves the request path (timeouts, UX, scale)  
- How Redis serves as broker **and** real-time progress bus  
- Trade-offs of SSE vs WebSockets for one-way progress  
- Horizontal scale via identical stateless workers + shared queue  
- Rate limits at the edge to protect the queue from stampede  

## Out of scope

Auth, multi-tenant billing, Kubernetes, RabbitMQ, and production TLS — intentionally omitted so the demo stays focused.
