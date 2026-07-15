"""Stitch design provider (phase-17) — the default, highest-risk design backend (D9/D10).

Wires three concerns behind the provider-agnostic :class:`DesignProvider` contract:

- **Transport** (:class:`StitchClient`): speaks the Stitch MCP (image→UI, text→UI, refine, fetch).
  The HTTP implementation is the one unavoidable guess (the exact MCP schema isn't public here); it
  is isolated behind the Protocol so tests inject a fake and the rest of the logic is fully covered.
- **Auth** (:class:`~app.design.stitch_auth.StitchAuth`): transparent, single-flight token refresh.
- **Quota** (:class:`~app.design.stitch_quota.StitchQuota`): pre-check + fail-soft at the cap.

Failures are classified (auth/quota/transient/fatal) as :class:`ProviderError` with a fallback hint.
Missing credentials → ``health()==down`` (never a crash) so the stage falls back. Tokens are never
logged or placed in any :class:`DesignResult`/artifact/event.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from app.core.config import get_config
from app.core.errors import ProviderError
from app.design.base import (
    DesignCapabilities,
    DesignCode,
    DesignImage,
    DesignResult,
    ProviderHealth,
)
from app.design.stitch_auth import StitchAuth, stitch_credentials_present
from app.design.stitch_errors import PROVIDER_KEY, StitchErrorKind, stitch_error
from app.design.stitch_quota import QuotaStatus, StitchQuota

MAX_IMAGES = 4
Sleeper = Callable[[float], Awaitable[None]]


class StitchDesignPayload(BaseModel):
    """Normalized Stitch response (what the transport hands back)."""

    external_ref: str = ""
    html: str = ""
    css: str = ""
    preview_image: str | None = None
    assets: dict[str, str] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class StitchClient(Protocol):
    """The Stitch MCP transport. ``token`` is a bearer access token; never store/log it."""

    async def text_to_ui(self, prompt: str, *, token: str) -> StitchDesignPayload: ...

    async def image_to_ui(
        self, images: list[DesignImage], prompt: str | None, *, token: str
    ) -> StitchDesignPayload: ...

    async def refine(
        self, external_ref: str, instruction: str, *, token: str
    ) -> StitchDesignPayload: ...

    async def fetch_code(self, external_ref: str, *, token: str) -> StitchDesignPayload: ...


class HttpStitchClient:
    """Default transport: JSON over HTTP to the Stitch MCP endpoint.

    Assumed contract (isolated here so it is the only thing to adjust against a real server):
    ``POST {stitch_mcp_url}`` with ``{"method": <tool>, "params": {...}}`` + ``Authorization:
    Bearer <token>`` → ``{external_ref, html, css, preview_image?, assets?, meta?}``. HTTP status is
    mapped to the error taxonomy (401/403→auth, 429→quota, timeout/5xx/network→transient, other
    4xx→fatal).
    """

    async def _rpc(self, method: str, params: dict[str, Any], token: str) -> StitchDesignPayload:
        config = get_config()
        url = str(config.get("stitch_mcp_url"))
        if not url:
            raise stitch_error(StitchErrorKind.auth, "Stitch MCP URL is not configured")
        try:
            async with httpx.AsyncClient(timeout=float(config.get("stitch_timeout_s"))) as client:
                response = await client.post(
                    url,
                    json={"method": method, "params": params},
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError as exc:
            raise stitch_error(
                StitchErrorKind.transient, f"Stitch request failed ({method})"
            ) from exc

        self._raise_for_status(response, method)
        try:
            return StitchDesignPayload.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise stitch_error(
                StitchErrorKind.fatal, f"Malformed Stitch response ({method})"
            ) from exc

    @staticmethod
    def _raise_for_status(response: httpx.Response, method: str) -> None:
        code = response.status_code
        if code < 400:
            return
        if code in (401, 403):
            raise stitch_error(StitchErrorKind.auth, f"Stitch rejected the token ({code})")
        if code == 429:
            raise stitch_error(StitchErrorKind.quota, "Stitch reported the quota is exceeded")
        if code >= 500:
            raise stitch_error(StitchErrorKind.transient, f"Stitch server error ({code})")
        raise stitch_error(StitchErrorKind.fatal, f"Stitch rejected the request ({code}, {method})")

    async def text_to_ui(self, prompt: str, *, token: str) -> StitchDesignPayload:
        return await self._rpc("text_to_ui", {"prompt": prompt}, token)

    async def image_to_ui(
        self, images: list[DesignImage], prompt: str | None, *, token: str
    ) -> StitchDesignPayload:
        encoded = [
            {
                "filename": img.filename,
                "media_type": img.media_type,
                "data_base64": base64.b64encode(img.data).decode("ascii"),
            }
            for img in images
        ]
        return await self._rpc("image_to_ui", {"images": encoded, "prompt": prompt}, token)

    async def refine(
        self, external_ref: str, instruction: str, *, token: str
    ) -> StitchDesignPayload:
        return await self._rpc(
            "refine", {"external_ref": external_ref, "instruction": instruction}, token
        )

    async def fetch_code(self, external_ref: str, *, token: str) -> StitchDesignPayload:
        return await self._rpc("fetch_code", {"external_ref": external_ref}, token)


def _kind_of(error: ProviderError) -> str | None:
    detail = error.detail
    return detail.get("kind") if isinstance(detail, dict) else None


class StitchDesignProvider:
    key = PROVIDER_KEY

    def __init__(
        self,
        *,
        client: StitchClient | None = None,
        auth: StitchAuth | None = None,
        quota: StitchQuota | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        self._client: StitchClient = client or HttpStitchClient()
        self._auth = auth or StitchAuth()
        self._quota = quota or StitchQuota()
        self._sleep: Sleeper = sleeper or asyncio.sleep

    # -- capabilities / health ------------------------------------------------------------

    def capabilities(self) -> DesignCapabilities:
        return DesignCapabilities(
            provider=self.key,
            from_image=True,
            from_text=True,
            refine=True,
            fetch_code=True,
            max_images=MAX_IMAGES,
        )

    async def health(self) -> ProviderHealth:
        # No creds → down (not a crash): the stage falls back to figma/fake.
        if not stitch_credentials_present():
            return ProviderHealth.down
        try:
            snap = await self._quota.snapshot()
        except Exception:  # pragma: no cover - health must never raise
            return ProviderHealth.degraded
        if snap.status is QuotaStatus.ok:
            return ProviderHealth.ok
        return ProviderHealth.degraded  # near/at cap: reachable but limited

    # -- generation flows -----------------------------------------------------------------

    async def generate_from_text(self, prompt: str) -> DesignResult:
        self._require_credentials()
        await self._quota.check_available()
        payload = await self._invoke(lambda token: self._client.text_to_ui(prompt, token=token))
        await self._quota.record_usage()
        return self._to_result(payload, {"source": "text"})

    async def generate_from_image(
        self, images: list[DesignImage], prompt: str | None = None
    ) -> DesignResult:
        self._require_credentials()
        if not images:
            raise stitch_error(StitchErrorKind.fatal, "At least one image is required")
        if len(images) > MAX_IMAGES:
            raise stitch_error(
                StitchErrorKind.fatal, f"Stitch accepts at most {MAX_IMAGES} images per request"
            )
        await self._quota.check_available()
        payload = await self._invoke(
            lambda token: self._client.image_to_ui(images, prompt, token=token)
        )
        await self._quota.record_usage()
        return self._to_result(payload, {"source": "image", "images": [i.filename for i in images]})

    async def refine(self, design_ref: str, instruction: str) -> DesignResult:
        self._require_credentials()
        await self._quota.check_available()
        payload = await self._invoke(
            lambda token: self._client.refine(design_ref, instruction, token=token)
        )
        await self._quota.record_usage()
        return self._to_result(payload, {"source": "refine", "refined_from": design_ref})

    async def fetch_code(self, design_ref: str) -> DesignCode:
        # Metadata read — does not consume a generation.
        self._require_credentials()
        payload = await self._invoke(lambda token: self._client.fetch_code(design_ref, token=token))
        return DesignCode(html=payload.html, css=payload.css, assets=payload.assets)

    # -- internals ------------------------------------------------------------------------

    def _require_credentials(self) -> None:
        if not stitch_credentials_present():
            raise stitch_error(StitchErrorKind.auth, "Stitch credentials are not configured")

    def _to_result(self, payload: StitchDesignPayload, extra_meta: dict[str, Any]) -> DesignResult:
        return DesignResult(
            provider=self.key,
            external_ref=payload.external_ref,
            html=payload.html,
            css=payload.css,
            preview_image=payload.preview_image,
            # Provider-specific meta only — no token ever lands here.
            meta={**payload.meta, **extra_meta},
        )

    async def _invoke(
        self, make_call: Callable[[str], Awaitable[StitchDesignPayload]]
    ) -> StitchDesignPayload:
        """Run a transport call with transparent token refresh + transient backoff/retry."""
        max_retries = int(get_config().get("stitch_max_retries"))
        refreshed = False
        attempt = 0
        while True:
            token = await self._auth.get_access_token()
            try:
                return await make_call(token)
            except ProviderError as exc:
                kind = _kind_of(exc)
                if kind == StitchErrorKind.auth and not refreshed:
                    # Token may have been revoked mid-flight — force one refresh and retry.
                    self._auth.invalidate()
                    refreshed = True
                    continue
                if kind == StitchErrorKind.transient and attempt < max_retries:
                    attempt += 1
                    await self._sleep(0.5 * 2 ** (attempt - 1))
                    continue
                raise
