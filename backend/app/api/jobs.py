import asyncio
import json
import shutil
import uuid
from pathlib import Path
from uuid import UUID

import redis
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.job import Job, JobStatus
from app.schemas import JobOut
from app.services.album_images import IMAGE_SUFFIXES, is_image_path
from app.services.progress import channel_for, get_cached_progress
from app.services.rate_limit import enforce_rate_limit
from app.tasks.album_press import process_album

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
settings = get_settings()

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
MAX_FILES = 200


def _to_out(job: Job) -> JobOut:
    payload = job.payload or {}
    return JobOut(
        id=job.id,
        title=job.title,
        status=job.status.value if hasattr(job.status, "value") else str(job.status),
        progress=job.progress,
        stage=job.stage,
        message=job.message,
        error=job.error,
        result_path=job.result_path,
        image_count=payload.get("image_count"),
        has_zip=bool(payload.get("result_zip")),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(
    request: Request,
    db: Session = Depends(get_db),
    files: list[UploadFile] = File(..., description="ZIP or image files"),
    title: str | None = Form(None),
    max_width: int = Form(1600),
    quality: int = Form(85),
    strip_exif: str | None = Form("true"),
    layout: str = Form("page"),
    grid_cols: int = Form(2),
) -> JobOut:
    enforce_rate_limit(request)

    if not files:
        raise HTTPException(status_code=400, detail="Upload a ZIP or one or more images")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Too many files (max {MAX_FILES})")

    max_width = max(400, min(int(max_width), 4000))
    quality = max(40, min(int(quality), 95))
    grid_cols = max(1, min(int(grid_cols), 4))
    layout = layout if layout in {"page", "grid"} else "page"
    do_strip = _parse_bool(strip_exif, True)

    job_id = uuid.uuid4()
    work_dir = Path(settings.upload_dir) / str(job_id)
    raw_dir = work_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    total_size = 0
    saved = 0
    try:
        for upload in files:
            name = Path(upload.filename or "upload.bin").name
            lower = name.lower()
            is_zip = lower.endswith(".zip")
            is_img = Path(lower).suffix in IMAGE_SUFFIXES
            if not is_zip and not is_img:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported file type: {name}. Use ZIP or jpg/png/webp/gif.",
                )
            dest = raw_dir / name
            with dest.open("wb") as out:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=400, detail="Upload exceeds 100 MB limit")
                    out.write(chunk)
            if dest.stat().st_size == 0:
                dest.unlink(missing_ok=True)
                continue
            saved += 1
            await upload.close()
    except HTTPException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise

    if saved == 0:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="Uploaded files were empty")

    # Quick sanity: if no zip, ensure at least one image exists
    has_zip = any(p.suffix.lower() == ".zip" for p in raw_dir.iterdir())
    has_img = any(is_image_path(p) for p in raw_dir.iterdir() if p.is_file())
    if not has_zip and not has_img:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="No images found in upload")

    report_title = (title or "").strip() or "Album Press"

    job = Job(
        id=job_id,
        title=report_title,
        status=JobStatus.queued,
        progress=0,
        stage="queued",
        message="Waiting for a worker",
        payload={
            "work_dir": str(work_dir),
            "max_width": max_width,
            "quality": quality,
            "strip_exif": do_strip,
            "layout": layout,
            "grid_cols": grid_cols,
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    async_result = process_album.delay(str(job.id))
    job.celery_task_id = async_result.id
    db.commit()
    db.refresh(job)
    return _to_out(job)


@router.get("", response_model=list[JobOut])
def list_jobs(db: Session = Depends(get_db), limit: int = 50) -> list[JobOut]:
    jobs = db.query(Job).order_by(Job.created_at.desc()).limit(min(limit, 100)).all()
    return [_to_out(j) for j in jobs]


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: UUID, db: Session = Depends(get_db)) -> JobOut:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _to_out(job)


@router.get("/{job_id}/events")
async def job_events(job_id: UUID, db: Session = Depends(get_db)) -> StreamingResponse:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    channel = channel_for(job_id)

    async def event_stream():
        terminal = {"completed", "failed", "cancelled"}
        cached = get_cached_progress(job_id)
        snapshot = cached or {
            "job_id": str(job.id),
            "progress": job.progress,
            "stage": job.stage,
            "message": job.message,
            "status": job.status.value if hasattr(job.status, "value") else str(job.status),
        }
        yield f"data: {json.dumps(snapshot)}\n\n"
        if snapshot.get("status") in terminal:
            return

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        pubsub = client.pubsub()
        pubsub.subscribe(channel)
        try:
            while True:
                message = await asyncio.to_thread(
                    pubsub.get_message,
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message and message.get("type") == "message":
                    data = message["data"]
                    yield f"data: {data}\n\n"
                    try:
                        payload = json.loads(data)
                        if payload.get("status") in terminal:
                            break
                    except json.JSONDecodeError:
                        pass
                else:
                    yield ": keepalive\n\n"
                    await asyncio.sleep(0.05)
        finally:
            pubsub.unsubscribe(channel)
            pubsub.close()
            client.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{job_id}/download")
def download_job(
    job_id: UUID,
    kind: str = "pdf",
    db: Session = Depends(get_db),
) -> FileResponse:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.completed:
        raise HTTPException(status_code=409, detail="Album not ready")

    safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in job.title)[:80]
    payload = job.payload or {}

    if kind == "zip":
        zip_path = payload.get("result_zip")
        if not zip_path or not Path(zip_path).exists():
            raise HTTPException(status_code=404, detail="ZIP not available")
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=f"{safe_title}_{job_id}.zip",
        )

    if not job.result_path or not Path(job.result_path).exists():
        raise HTTPException(status_code=404, detail="PDF not available")
    return FileResponse(
        job.result_path,
        media_type="application/pdf",
        filename=f"{safe_title}_{job_id}.pdf",
    )


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: UUID, db: Session = Depends(get_db)) -> JobOut:
    from app.celery_app import celery
    from app.services.progress import publish_progress

    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in (JobStatus.completed, JobStatus.failed, JobStatus.cancelled):
        return _to_out(job)

    if job.celery_task_id:
        celery.control.revoke(job.celery_task_id, terminate=True)

    job.status = JobStatus.cancelled
    job.message = "Cancelled by user"
    job.stage = "cancelled"
    db.commit()
    db.refresh(job)

    publish_progress(
        job.id,
        progress=job.progress,
        stage="cancelled",
        message="Cancelled by user",
        status=JobStatus.cancelled.value,
    )
    return _to_out(job)
