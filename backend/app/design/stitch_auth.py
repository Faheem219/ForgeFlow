"""Centralized Stitch OAuth (phase-17).

Stitch access tokens are short-lived, so this holds the platform's client credentials, fetches an
access token via the client-credentials grant, and **refreshes transparently** before expiry. A
single-flight lock ensures concurrent callers trigger at most one refresh (no token stampede).

Credentials come from the layered config today; they move behind the encrypted vault in phase-34
(wire `_fetch_token` to read from the vault then). **Tokens and secrets are never logged.**
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from app.core.config import get_config
from app.design.stitch_errors import StitchErrorKind, stitch_error

# A token fetch returns (access_token, lifetime_seconds).
TokenFetcher = Callable[[], Awaitable[tuple[str, float]]]
Clock = Callable[[], float]


@dataclass
class _CachedToken:
    access_token: str
    expires_at: float  # wall-clock seconds (clock())


def stitch_credentials_present() -> bool:
    """True when the minimum creds to obtain a token are configured."""
    config = get_config()
    return bool(
        config.get("stitch_client_id")
        and config.get("stitch_client_secret")
        and config.get("stitch_token_url")
    )


class StitchAuth:
    def __init__(
        self,
        *,
        token_fetcher: TokenFetcher | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._fetch: TokenFetcher = token_fetcher or self._fetch_token
        self._clock: Clock = clock or time.time
        self._lock = asyncio.Lock()
        self._cached: _CachedToken | None = None

    def _skew(self) -> float:
        return float(get_config().get("stitch_token_skew_s"))

    def _is_valid(self, token: _CachedToken | None) -> bool:
        return token is not None and token.expires_at - self._skew() > self._clock()

    async def get_access_token(self) -> str:
        """Return a valid access token, refreshing (once, single-flight) if needed."""
        if self._is_valid(self._cached):
            assert self._cached is not None
            return self._cached.access_token

        async with self._lock:
            # Double-checked: a concurrent caller may have refreshed while we waited for the lock.
            if self._is_valid(self._cached):
                assert self._cached is not None
                return self._cached.access_token

            access_token, lifetime = await self._fetch()
            self._cached = _CachedToken(
                access_token=access_token,
                expires_at=self._clock() + max(0.0, lifetime),
            )
            return access_token

    def invalidate(self) -> None:
        """Drop the cached token (e.g. after a 401) so the next call refreshes."""
        self._cached = None

    async def _fetch_token(self) -> tuple[str, float]:
        """Default fetcher: OAuth2 client-credentials grant. Never logs the token or secret."""
        config = get_config()
        if not stitch_credentials_present():
            raise stitch_error(StitchErrorKind.auth, "Stitch credentials are not configured")

        token_url = str(config.get("stitch_token_url"))
        data = {
            "grant_type": "client_credentials",
            "client_id": str(config.get("stitch_client_id")),
            "client_secret": str(config.get("stitch_client_secret")),
        }
        scope = str(config.get("stitch_scope"))
        if scope:
            data["scope"] = scope

        try:
            async with httpx.AsyncClient(timeout=float(config.get("stitch_timeout_s"))) as client:
                response = await client.post(token_url, data=data)
        except httpx.HTTPError as exc:
            raise stitch_error(
                StitchErrorKind.transient, "Could not reach the Stitch token endpoint"
            ) from exc

        if response.status_code >= 400:
            # Body may echo the request; do NOT include it (could contain the secret).
            raise stitch_error(
                StitchErrorKind.auth,
                f"Stitch token request rejected ({response.status_code})",
            )

        try:
            body = response.json()
            access_token = str(body["access_token"])
            lifetime = float(body.get("expires_in", 3600))
        except (ValueError, KeyError, TypeError) as exc:
            raise stitch_error(StitchErrorKind.fatal, "Malformed Stitch token response") from exc

        return access_token, lifetime
