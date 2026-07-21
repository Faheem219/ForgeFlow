"""Per-IP rate limiting for the unauthenticated auth endpoints.

The counters themselves live in :mod:`app.core.limits` (phase-47) so that the auth limiter and the
per-user expensive-endpoint limiter share one implementation. This module stays as the auth-facing
name that :mod:`app.auth.router` depends on.
"""

from __future__ import annotations

from fastapi import Request

from app.core.limits import ip_rate_limit, reset_limits


async def rate_limit(request: Request) -> None:
    """Per-client-IP fixed-window limit on `/auth/register` and `/auth/login`."""
    await ip_rate_limit(request)


def reset_rate_limits() -> None:
    """Clear all counters (test helper)."""
    reset_limits()


__all__ = ["rate_limit", "reset_rate_limits"]
