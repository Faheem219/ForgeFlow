"""Stitch authentication (phase-17, corrected against the shipped Stitch API).

The official endpoint (``https://stitch.googleapis.com/mcp``) accepts **three** credential shapes,
tried in this order:

1. **API key** — ``X-Goog-Api-Key: <key>``. Created in Stitch → Settings → API keys. This is the
   five-minute path and the one to use for a demo.
2. **Google access token** — ``Authorization: Bearer <token>``, typically from
   ``gcloud auth application-default print-access-token`` after enabling ``stitch.googleapis.com``
   on a Cloud project. Tokens last ~1 hour, so this suits short-lived/CI use.
3. **OAuth2 client-credentials** — the platform exchanges ``client_id``/``client_secret`` at
   ``STITCH_TOKEN_URL`` for a bearer token, refreshing transparently before expiry. Google's own
   token endpoint does **not** implement this grant; it is kept for a self-hosted proxy or a
   partner endpoint that fronts Stitch and does.

``STITCH_GCP_PROJECT``, when set, is sent as ``X-Goog-User-Project`` — the quota/billing project
Google attributes the call to.

Since phase-34 the **client secret** is read through the encrypted vault (a platform-scoped vault
entry wins over the plaintext ``STITCH_CLIENT_SECRET`` in config) and is decrypted only at call
time, inside ``_fetch_token``. **Tokens, keys and secrets are never logged.**
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from beanie import PydanticObjectId

from app.core.config import get_config
from app.db.models.enums import CredentialKind
from app.deploy.secrets import resolve_secret
from app.design.stitch_errors import StitchErrorKind, stitch_error

# A token fetch returns (access_token, lifetime_seconds).
TokenFetcher = Callable[[], Awaitable[tuple[str, float]]]
Clock = Callable[[], float]

#: Google's API-key header. The Stitch MCP endpoint reads the key from here, not from a query param.
API_KEY_HEADER = "X-Goog-Api-Key"
#: Quota/billing attribution project, sent when ``STITCH_GCP_PROJECT`` is configured.
USER_PROJECT_HEADER = "X-Goog-User-Project"


@dataclass
class _CachedToken:
    access_token: str
    expires_at: float  # wall-clock seconds (clock())


def _configured(key: str) -> str:
    return str(get_config().get(key)).strip()


async def stitch_client_secret(user_id: PydanticObjectId | None = None) -> str | None:
    """The effective client secret: BYO > platform vault > config (phase-34)."""
    return await resolve_secret(CredentialKind.stitch, user_id)


async def stitch_credentials_present(user_id: PydanticObjectId | None = None) -> bool:
    """True when *any* of the three supported credential shapes is fully configured."""
    if _configured("stitch_api_key") or _configured("stitch_access_token"):
        return True
    if not (_configured("stitch_client_id") and _configured("stitch_token_url")):
        return False
    return bool(await stitch_client_secret(user_id))


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

    # -- what the transport actually needs ---------------------------------------------------

    async def headers(self) -> dict[str, str]:
        """The auth headers for one Stitch call, resolved fresh (never cached in plaintext).

        An API key short-circuits the token machinery entirely — there is nothing to refresh.
        """
        headers: dict[str, str] = {}
        project = _configured("stitch_gcp_project")
        if project:
            headers[USER_PROJECT_HEADER] = project

        api_key = _configured("stitch_api_key")
        if api_key:
            headers[API_KEY_HEADER] = api_key
            return headers

        headers["Authorization"] = f"Bearer {await self.get_access_token()}"
        return headers

    async def get_access_token(self) -> str:
        """Return a valid bearer token, refreshing (once, single-flight) if needed.

        A statically configured ``STITCH_ACCESS_TOKEN`` is returned as-is: it is externally
        managed (gcloud), so ForgeFlow neither caches nor tries to refresh it.
        """
        static = _configured("stitch_access_token")
        if static:
            return static

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
        # Decrypt at call time only — the secret lives in this frame and nowhere else.
        client_secret = await stitch_client_secret()
        if not (config.get("stitch_client_id") and config.get("stitch_token_url")):
            raise stitch_error(StitchErrorKind.auth, "Stitch credentials are not configured")
        if not client_secret:
            raise stitch_error(StitchErrorKind.auth, "Stitch credentials are not configured")

        token_url = str(config.get("stitch_token_url"))
        data = {
            "grant_type": "client_credentials",
            "client_id": str(config.get("stitch_client_id")),
            "client_secret": client_secret,
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
