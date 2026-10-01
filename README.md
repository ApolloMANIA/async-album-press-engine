# Album Press

Async photo batch pipeline: upload a ZIP or images → Celery workers resize, strip EXIF, pack a compressed ZIP, and build a printable album PDF — with live progress over SSE.

This is a real Celery use case: many CPU-bound image jobs that should not run on the HTTP request path.

## What you get

- **Resize** to a max width (default 1600px)
- **JPEG compress** with configurable quality
- **EXIF strip** (optional)
- **ZIP** of processed photos
- **PDF album** — one photo per page, or a contact-sheet grid
- Live **progress** (per photo) + cancel

## Quick start

```bash
docker compose up --build
```

| Service | URL |
|---------|-----|
| UI | http://localhost:5173 |
| API docs | http://localhost:8000/docs |
| Health | http://localhost:8000/health |

Scale workers while a large ZIP runs:

```bash
docker compose up --build --scale worker=3
```

## Architecture

```
Browser ──multipart upload──► FastAPI
   │                              │
   │ SSE /events                  ├──► Postgres (job metadata)
   │                              ├──► /data/uploads/{job_id}/
   │                              └──► Celery enqueue
   └◄── Redis pub/sub ◄──┐              │
                         │              ▼
                  progress events   Celery workers
                                         │
                                         ├──► Pillow per image
                                         ├──► /data/pdfs/{id}.zip
                                         └──► /data/pdfs/{id}.pdf
```

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/jobs` | Multipart: `files` (ZIP and/or images), `title`, `max_width`, `quality`, `strip_exif`, `layout`, `grid_cols` |
| `GET` | `/api/jobs` | List jobs |
| `GET` | `/api/jobs/{id}/events` | SSE progress |
| `GET` | `/api/jobs/{id}/download?kind=pdf\|zip` | Download outputs |
| `POST` | `/api/jobs/{id}/cancel` | Cancel running job |

Limits: **100 MB** upload, **200** files, formats `jpg/png/webp/gif/bmp/tiff` (+ ZIP).

## Job stages

1. `validate` — load workspace  
2. `extract` — unpack ZIP if present  
3. `process` — resize/compress each photo  
4. `pack` — build ZIP  
5. `render_pdf` — album PDF  
6. `finalize` — ready to download  

## Layout

```
backend/     FastAPI + Celery + Pillow
frontend/    React console
data/uploads/ per-job workspaces
data/pdfs/    PDF + ZIP outputs
```
