import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from celery.exceptions import MaxRetriesExceededError

from app.celery_app import celery
from app.config import get_settings
from app.database import SessionLocal
from app.models.job import Job, JobStatus
from app.services.album_images import AlbumError, discover_images, extract_zip, process_image
from app.services.album_pdf import build_album_pdf
from app.services.progress import publish_progress

settings = get_settings()


def _update_job(job_id: UUID, **fields) -> Job | None:
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if not job:
            return None
        for key, value in fields.items():
            setattr(job, key, value)
        job.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(job)
        return job
    finally:
        db.close()


def _is_cancelled(job_id: UUID) -> bool:
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        return bool(job and job.status == JobStatus.cancelled)
    finally:
        db.close()


def _emit(job_id: UUID, progress: int, stage: str, message: str, status: str) -> None:
    _update_job(job_id, progress=progress, stage=stage, message=message, status=JobStatus(status))
    publish_progress(job_id, progress=progress, stage=stage, message=message, status=status)


def _build_zip(files: list[Path], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, arcname=path.name)
    return out_path


@celery.task(bind=True, name="app.tasks.album_press.process_album", max_retries=1)
def process_album(self, job_id: str) -> dict:
    uid = UUID(job_id)

    try:
        if _is_cancelled(uid):
            return {"status": "cancelled"}

        _emit(uid, 3, "validate", "Loading album job", JobStatus.running.value)

        db = SessionLocal()
        try:
            job = db.get(Job, uid)
            if not job:
                raise ValueError(f"Job {job_id} not found")
            payload = dict(job.payload or {})
            title = job.title
        finally:
            db.close()

        work_dir = Path(payload.get("work_dir", ""))
        if not work_dir.exists():
            raise FileNotFoundError("Upload workspace missing")

        raw_dir = work_dir / "raw"
        processed_dir = work_dir / "processed"
        processed_dir.mkdir(parents=True, exist_ok=True)

        max_width = int(payload.get("max_width") or 1600)
        quality = int(payload.get("quality") or 85)
        strip_exif = bool(payload.get("strip_exif", True))
        layout = str(payload.get("layout") or "page")
        grid_cols = int(payload.get("grid_cols") or 2)

        # Expand ZIP if present
        zip_uploads = list(raw_dir.glob("*.zip"))
        if zip_uploads:
            _emit(uid, 8, "extract", "Unpacking ZIP", JobStatus.running.value)
            extract_dir = raw_dir / "unzipped"
            extract_zip(zip_uploads[0], extract_dir)

        if _is_cancelled(uid):
            _emit(uid, 8, "extract", "Cancelled", JobStatus.cancelled.value)
            return {"status": "cancelled"}

        images = discover_images(raw_dir)
        if not images:
            raise AlbumError("No images found in upload (jpg, png, webp, gif, …)")

        payload["image_count"] = len(images)
        _update_job(uid, payload=payload)

        _emit(
            uid,
            12,
            "process",
            f"Processing 0/{len(images)} photos",
            JobStatus.running.value,
        )

        processed: list[Path] = []
        for i, src in enumerate(images):
            if _is_cancelled(uid):
                _emit(uid, int(12 + (i / len(images)) * 70), "process", "Cancelled", JobStatus.cancelled.value)
                return {"status": "cancelled"}

            dest = processed_dir / f"{i:04d}_{src.stem}.jpg"
            out = process_image(
                src,
                dest,
                max_width=max_width,
                quality=quality,
                strip_exif=strip_exif,
            )
            processed.append(out)
            pct = 12 + int(((i + 1) / len(images)) * 70)
            _emit(
                uid,
                pct,
                "process",
                f"Processing {i + 1}/{len(images)} photos",
                JobStatus.running.value,
            )

        if _is_cancelled(uid):
            _emit(uid, 82, "process", "Cancelled", JobStatus.cancelled.value)
            return {"status": "cancelled"}

        pdf_path = Path(settings.pdf_output_dir) / f"{job_id}.pdf"
        zip_path = Path(settings.pdf_output_dir) / f"{job_id}.zip"

        _emit(uid, 86, "pack", "Building compressed ZIP", JobStatus.running.value)
        _build_zip(processed, zip_path)

        if _is_cancelled(uid):
            for p in (pdf_path, zip_path):
                if p.exists():
                    os.remove(p)
            _emit(uid, 86, "pack", "Cancelled", JobStatus.cancelled.value)
            return {"status": "cancelled"}

        _emit(uid, 92, "render_pdf", "Building album PDF", JobStatus.running.value)
        build_album_pdf(
            processed,
            pdf_path,
            title=title,
            layout=layout,
            grid_cols=grid_cols,
        )

        if _is_cancelled(uid):
            for p in (pdf_path, zip_path):
                if p.exists():
                    os.remove(p)
            _emit(uid, 92, "render_pdf", "Cancelled", JobStatus.cancelled.value)
            return {"status": "cancelled"}

        payload["result_zip"] = str(zip_path)
        payload["image_count"] = len(processed)
        _emit(uid, 100, "finalize", "Album ready to download", JobStatus.completed.value)
        _update_job(uid, result_path=str(pdf_path), error=None, payload=payload)
        return {
            "status": "completed",
            "pdf": str(pdf_path),
            "zip": str(zip_path),
            "images": len(processed),
        }

    except MaxRetriesExceededError:
        raise
    except Exception as exc:
        try:
            raise self.retry(exc=exc, countdown=2)
        except MaxRetriesExceededError:
            _emit(uid, 0, "failed", str(exc), JobStatus.failed.value)
            _update_job(uid, error=str(exc), status=JobStatus.failed)
            raise
