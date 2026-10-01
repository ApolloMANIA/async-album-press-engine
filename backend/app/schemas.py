from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class JobOut(BaseModel):
    id: UUID
    title: str
    status: str
    progress: int
    stage: str | None = None
    message: str | None = None
    error: str | None = None
    result_path: str | None = None
    image_count: int | None = None
    has_zip: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
