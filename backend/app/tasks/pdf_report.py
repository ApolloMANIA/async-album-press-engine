import os
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from celery.exceptions import MaxRetriesExceededError
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.celery_app import celery
from app.config import get_settings
from app.database import SessionLocal
from app.models.job import Job, JobStatus
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


def _build_pdf(path: Path, title: str, sections: list[dict], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(title, styles["Title"]),
        Spacer(1, 0.25 * inch),
        Paragraph(
            f"Generated at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            styles["Normal"],
        ),
        Spacer(1, 0.3 * inch),
    ]

    for section in sections:
        story.append(Paragraph(section.get("heading", "Section"), styles["Heading2"]))
        body = section.get("body") or ""
        if body:
            story.append(Paragraph(body.replace("\n", "<br/>"), styles["BodyText"]))
        story.append(Spacer(1, 0.15 * inch))

    if rows:
        story.append(Paragraph("Data table", styles["Heading2"]))
        keys = list(rows[0].keys())
        table_data = [keys] + [[str(row.get(k, "")) for k in keys] for row in rows]
        table = Table(table_data, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a3a4a")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef4f6")]),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(table)

    doc.build(story)


@celery.task(bind=True, name="app.tasks.pdf_report.generate_pdf_report", max_retries=2)
def generate_pdf_report(self, job_id: str) -> dict:
    uid = UUID(job_id)

    try:
        if _is_cancelled(uid):
            return {"status": "cancelled"}

        _emit(uid, 5, "validate", "Validating report payload", JobStatus.running.value)
        time.sleep(0.4)

        if _is_cancelled(uid):
            _emit(uid, 5, "validate", "Cancelled", JobStatus.cancelled.value)
            return {"status": "cancelled"}

        db = SessionLocal()
        try:
            job = db.get(Job, uid)
            if not job:
                raise ValueError(f"Job {job_id} not found")
            payload = job.payload or {}
            title = job.title
        finally:
            db.close()

        sections = payload.get("sections") or []
        rows = payload.get("rows") or []
        if not isinstance(sections, list) or not isinstance(rows, list):
            raise ValueError("Invalid payload: sections and rows must be lists")

        _emit(uid, 40, "build_document", "Building document structure", JobStatus.running.value)
        time.sleep(0.6)

        if _is_cancelled(uid):
            _emit(uid, 40, "build_document", "Cancelled", JobStatus.cancelled.value)
            return {"status": "cancelled"}

        out_path = Path(settings.pdf_output_dir) / f"{job_id}.pdf"
        _emit(uid, 80, "render_pdf", "Rendering PDF with ReportLab", JobStatus.running.value)
        time.sleep(0.5)
        _build_pdf(out_path, title, sections, rows)

        if _is_cancelled(uid):
            if out_path.exists():
                os.remove(out_path)
            _emit(uid, 80, "render_pdf", "Cancelled", JobStatus.cancelled.value)
            return {"status": "cancelled"}

        _emit(
            uid,
            100,
            "finalize",
            "Report ready for download",
            JobStatus.completed.value,
        )
        _update_job(uid, result_path=str(out_path), error=None)
        return {"status": "completed", "path": str(out_path)}

    except MaxRetriesExceededError:
        raise
    except Exception as exc:
        try:
            raise self.retry(exc=exc, countdown=2)
        except MaxRetriesExceededError:
            _emit(uid, 0, "failed", str(exc), JobStatus.failed.value)
            _update_job(uid, error=str(exc), status=JobStatus.failed)
            raise