"""Minimal in-memory per-IP fixed-window rate limiter for the auth endpoints.

Sufficient for a single-instance dev/demo. Swap for a Redis-backed limiter behind the same
``rate_limit`` dependency if horizontal scale is ever needed.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Request

from app.core.config import get_config
from app.core.errors import RateLimitError

_hits: dict[str, deque[float]] = defaultdict(deque)


def reset_rate_limits() -> None:
    """Clear all counters (test helper)."""
    _hits.clear()


async def rate_limit(request: Request) -> None:
    limit = int(get_config().get("auth_rate_limit_per_minute"))
    if limit <= 0:
        return
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window = _hits[key]
    while window and now - window[0] > 60.0:
        window.popleft()
    if len(window) >= limit:
        raise RateLimitError("Too many requests; slow down and retry shortly")
    window.append(now)
