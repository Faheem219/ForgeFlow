"""Abuse limits: keyed rate limiting + a request-body size cap (phase-47).

Two distinct abuse shapes, two controls:

- **Rate limiting.** Unauthenticated endpoints (`/auth/*`) are limited *per client IP* — there is no
  user yet to attribute a request to. Authenticated **expensive** endpoints are limited *per user*,
  because that is what actually costs money or capacity: an LLM call, a sandbox process, a container
  create, a provider deploy. Per-IP would be both too coarse (shared NAT) and too easy to evade.
- **Body size.** A cap applied before the body is read, so an oversized payload is refused rather
  than buffered. Per-route caps stay tighter (design images, workspace file writes); this is the
  backstop that keeps any single request from exhausting process memory.

Fixed-window counters in process memory: correct and sufficient for a single-instance dev/demo
deployment. Swap in a Redis-backed :class:`RateLimiter` behind these same dependencies if the
control plane is ever scaled horizontally — nothing outside this module would change.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from beanie import PydanticObjectId
from fastapi import Depends, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.auth.deps import get_current_user_id
from app.core.config import get_config
from app.core.errors import RateLimitError

WINDOW_SECONDS = 60.0


class RateLimiter:
    """Fixed-window counter keyed by an arbitrary string (IP, user id, …)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int) -> None:
        """Record a hit for ``key``; raise :class:`RateLimitError` once ``limit`` is exceeded."""
        if limit <= 0:  # 0 or negative disables the limit (used in tests and local dev)
            return
        now = time.monotonic()
        window = self._hits[key]
        while window and now - window[0] > WINDOW_SECONDS:
            window.popleft()
        if len(window) >= limit:
            raise RateLimitError("Too many requests; slow down and retry shortly")
        window.append(now)

    def clear(self) -> None:
        self._hits.clear()


#: Separate buckets so a burst of expensive work cannot lock a user out of logging in.
_ip_limiter = RateLimiter()
_user_limiter = RateLimiter()


def reset_limits() -> None:
    """Clear every counter (test helper)."""
    _ip_limiter.clear()
    _user_limiter.clear()


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def ip_rate_limit(request: Request) -> None:
    """Per-IP limit for unauthenticated endpoints (`/auth/register`, `/auth/login`)."""
    _ip_limiter.check(_client_key(request), int(get_config().get("auth_rate_limit_per_minute")))


async def expensive_rate_limit(
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> None:
    """Per-user limit for endpoints that cost real money or capacity.

    Applied to model-backed work (intent, criteria suggestions, repair), sandbox work (exec, test
    runs, container create) and provider work (deploy, infra analysis). Keyed by user rather than
    IP so the cost lands on whoever actually incurred it.
    """
    _user_limiter.check(f"user:{user_id}", int(get_config().get("expensive_rate_limit_per_minute")))


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Refuse over-sized request bodies with ``413`` before they are buffered.

    Trusts ``Content-Length`` when present (the cheap, common path) and otherwise counts bytes as
    the stream is consumed, so a chunked upload cannot slip past by omitting the header.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if self._max_bytes > 0:
            declared = request.headers.get("content-length")
            if declared is not None:
                try:
                    if int(declared) > self._max_bytes:
                        return self._too_large()
                except ValueError:
                    return self._too_large()  # unparseable length — refuse rather than guess
            elif request.headers.get("transfer-encoding", "").lower() == "chunked":
                body = await request.body()  # bounded read; Starlette caches it for the handler
                if len(body) > self._max_bytes:
                    return self._too_large()
        return await call_next(request)

    def _too_large(self) -> JSONResponse:
        return JSONResponse(
            status_code=413,
            content={
                "error": {
                    "type": "user_error",
                    "message": (f"Request body is too large (max {self._max_bytes} bytes)."),
                }
            },
        )


__all__ = [
    "BodySizeLimitMiddleware",
    "RateLimiter",
    "WINDOW_SECONDS",
    "expensive_rate_limit",
    "ip_rate_limit",
    "reset_limits",
]
