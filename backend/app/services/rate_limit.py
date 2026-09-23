import time

import redis
from fastapi import HTTPException, Request, status

from app.config import get_settings

settings = get_settings()


def _client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def enforce_rate_limit(request: Request) -> None:
    """Sliding-window rate limit keyed by client IP."""
    ip = request.client.host if request.client else "unknown"
    key = f"ratelimit:jobs:{ip}"
    now = time.time()
    window = 60.0
    limit = settings.rate_limit_per_minute

    client = _client()
    pipe = client.pipeline()
    pipe.zremrangebyscore(key, 0, now - window)
    pipe.zadd(key, {str(now): now})
    pipe.zcard(key)
    pipe.expire(key, int(window) + 1)
    _, _, count, _ = pipe.execute()

    if count > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: max {limit} job submissions per minute",
        )
