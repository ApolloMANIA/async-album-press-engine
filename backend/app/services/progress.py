import json
from typing import Any
from uuid import UUID

import redis

from app.config import get_settings

settings = get_settings()


def _client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def channel_for(job_id: UUID | str) -> str:
    return f"job:{job_id}"


def publish_progress(
    job_id: UUID | str,
    *,
    progress: int,
    stage: str,
    message: str,
    status: str,
) -> dict[str, Any]:
    event = {
        "job_id": str(job_id),
        "progress": progress,
        "stage": stage,
        "message": message,
        "status": status,
    }
    client = _client()
    client.publish(channel_for(job_id), json.dumps(event))
    client.setex(f"job:cache:{job_id}", 3600, json.dumps(event))
    return event


def get_cached_progress(job_id: UUID | str) -> dict[str, Any] | None:
    raw = _client().get(f"job:cache:{job_id}")
    if not raw:
        return None
    return json.loads(raw)
