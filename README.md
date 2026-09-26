# Async Report Engine

A distributed PDF report pipeline you can run locally and watch in real time.

Submit a job from a React console; FastAPI enqueues it; Celery workers generate the PDF; Redis pub/sub pushes progress; the UI streams updates over SSE. Built as a portfolio showcase of async job design—not a production SaaS.

## Why this exists

Long-running work does not belong on the request path. This project separates **accept the job** from **do the work**, keeps durable state in Postgres, and surfaces live progress without polling.

**Demonstrates:** API/worker decoupling · Redis as broker + progress bus · horizontal worker scaling · rate limiting · retries & failed jobs · SSE for one-way live updates

## Architecture

```
React UI  ──POST /api/jobs──►  FastAPI
   │                              │
   │ SSE /events                  ├──► PostgreSQL (job metadata)
   │                              │
   └◄── Redis pub/sub ◄──┐        └──► Celery enqueue
                         │                   │
                  progress events            ▼
                                      Celery workers (scalable)
                                             │
                                             ├──► PDF files (/data/pdfs)
                                             └──► status updates in Postgres
```

| Layer | Role |
|-------|------|
| **API** | Validate, persist, enqueue, stream SSE, rate-limit |
| **Workers** | Multi-stage PDF generation with retries & cancel checks |
| **Redis** | Celery broker/results + pub/sub progress channel |
| **Postgres** | Durable job records and status |
| **UI** | Submit jobs, live progress, download/cancel |

## Quick start

Requires [Docker](https://docs.docker.com/get-docker/) with the Compose plugin.

```bash
git clone <repo-url> async-report-engine
cd async-report-engine
docker compose up --build
```

| Service | URL |
|---------|-----|
| UI | http://localhost:5173 |
| API docs | http://localhost:8000/docs |
| Health | http://localhost:8000/health |

Submit a few jobs from the UI, then scale workers and watch them share the queue:

```bash
docker compose up --build --scale worker=3
```

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/jobs` | Enqueue a PDF report (rate-limited) |
| `GET` | `/api/jobs` | List recent jobs |
| `GET` | `/api/jobs/{id}` | Job status snapshot |
| `GET` | `/api/jobs/{id}/events` | SSE progress stream |
| `GET` | `/api/jobs/{id}/download` | Download completed PDF |
| `POST` | `/api/jobs/{id}/cancel` | Best-effort cancel (Celery revoke) |

```bash
curl -X POST http://localhost:8000/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"title":"Ops brief","row_count":50}'
```

## Job lifecycle

1. **`validate`** (~5%) — payload checks  
2. **`build_document`** (~40%) — structure report content  
3. **`render_pdf`** (~80%) — ReportLab write  
4. **`finalize`** (100%) — mark completed, ready to download  

Failures retry (Celery `max_retries=2`). After exhaustion the job is `failed` with an error message. Cancel is best-effort via revoke plus in-worker status checks.

## Stack

| Area | Choice |
|------|--------|
| API | FastAPI + Uvicorn |
| Queue / realtime | Celery + Redis |
| Persistence | PostgreSQL + SQLAlchemy |
| PDF | ReportLab |
| Frontend | React 19, Vite, TypeScript |
| Ops | Docker Compose (`api`, `worker`, `redis`, `db`, `frontend`) |

## Project layout

```
backend/
  app/
    api/          # HTTP routes (jobs + SSE)
    tasks/        # Celery PDF pipeline
    services/     # progress pub/sub, rate limit
    models/       # Job ORM
frontend/
  src/            # React job console
data/pdfs/        # Generated PDFs (Compose volume)
docker-compose.yml
```

## Local dev (without full Compose)

Point `.env` at reachable Redis and Postgres (see `.env.example`), then:

```bash
cp .env.example .env

# API
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload

# Worker (separate terminal)
celery -A app.celery_app.celery worker --loglevel=info

# UI
cd frontend
npm install
npm run dev
```

## Out of scope

Auth, multi-tenant billing, Kubernetes, RabbitMQ, and production TLS are intentionally omitted so the demo stays focused.
