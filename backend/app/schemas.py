from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ReportSection(BaseModel):
    heading: str
    body: str = ""


class JobCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    sections: list[ReportSection] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int | None = Field(
        default=None,
        ge=1,
        le=500,
        description="If set, generate this many sample rows (overrides rows).",
    )


class JobOut(BaseModel):
    id: UUID
    title: str
    status: str
    progress: int
    stage: str | None = None
    message: str | None = None
    error: str | None = None
    result_path: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
