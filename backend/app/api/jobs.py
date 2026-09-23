import asyncio
import json
from pathlib import Path
from uuid import UUID

import redis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.job import Job, JobStatus
from app.schemas import JobCreate, JobOut
from app.services.progress import channel_for, get_cached_progress
from app.services.rate_limit import enforce_rate_limit
from app.tasks.pdf_report import generate_pdf_report

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
settings = get_settings()


def _sample_rows(count: int) -> list[dict]:
    return [
        {
            "id": i + 1,
            "account": f"ACC-{1000 + i}",
            "region": ["North", "South", "East", "West"][i % 4],
            "amount": round(100 + (i * 17.3) % 900, 2),
            "status": ["open", "closed", "pending"][i % 3],
        }
        for i in range(count)
    ]


def _to_out(job: Job) -> JobOut:
    return JobOut(
        id=job.id,
        title=job.title,
        status=job.status.value if hasattr(job.status, "value") else str(job.status),
        progress=job.progress,
        stage=job.stage,
        message=job.message,
        error=job.error,
        result_path=job.result_path,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
def create_job(
    body: JobCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> JobOut:
    enforce_rate_limit(request)

    rows = body.rows
    if body.row_count is not None:
        rows = _sample_rows(body.row_count)
    if not rows and not body.sections:
        rows = _sample_rows(25)
        sections = [
            {
                "heading": "Executive summary",
                "body": "Auto-generated sample report for the async processing demo.",
            }
        ]
    else:
        sections = [s.model_dump() for s in body.sections]

    job = Job(
        title=body.title,
        status=JobStatus.queued,
        progress=0,
        stage="queued",
        message="Waiting for a worker",
        payload={"sections": sections, "rows": rows},
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    async_result = generate_pdf_report.delay(str(job.id))
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
def download_job(job_id: UUID, db: Session = Depends(get_db)) -> FileResponse:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.completed or not job.result_path:
        raise HTTPException(status_code=409, detail="PDF not ready")
    path = Path(job.result_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF file missing")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{job.title.replace(' ', '_')}_{job_id}.pdf",
    )


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: UUID, db: Session = Depends(get_db)) -> JobOut:
    from app.celery_app import celery

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

    from app.services.progress import publish_progress

    publish_progress(
        job.id,
        progress=job.progress,
        stage="cancelled",
        message="Cancelled by user",
        status=JobStatus.cancelled.value,
    )
    return _to_out(job)
