"""Figma design provider (phase-18) — the alternative backend and Stitch's fallback target (D9/D10).

Implements the same provider-agnostic :class:`DesignProvider` contract as Stitch, so switching to it
is a config change with **no** stage-handler change. Figma's capabilities differ from Stitch's:
it works from text and from existing Figma designs, but has **no screenshot→UI** — so
``generate_from_image`` degrades with a clear message (``capabilities().from_image == False``)
rather than faking a result.

Transport is isolated behind :class:`FigmaClient` (the HTTP impl is the one schema guess); auth is a
static token; there is no per-generation quota. Tokens are never logged or placed in any result.
"""

from __future__ import annotations

import asyncio
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
from app.design.figma_auth import figma_token_present, get_figma_token
from app.design.figma_errors import PROVIDER_KEY, FigmaErrorKind, figma_error

Sleeper = Callable[[float], Awaitable[None]]


class FigmaDesignPayload(BaseModel):
    """Normalized Figma response (what the transport hands back)."""

    external_ref: str = ""
    html: str = ""
    css: str = ""
    preview_image: str | None = None
    assets: dict[str, str] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class FigmaClient(Protocol):
    """The Figma MCP transport. ``token`` is the Figma access token; never store/log it.

    Note there is **no** ``image_to_ui`` — Figma has no screenshot→UI capability, so the provider
    rejects that op before any transport call is made.
    """

    async def text_to_ui(self, prompt: str, *, token: str) -> FigmaDesignPayload: ...

    async def refine(
        self, external_ref: str, instruction: str, *, token: str
    ) -> FigmaDesignPayload: ...

    async def fetch_code(self, external_ref: str, *, token: str) -> FigmaDesignPayload: ...


class HttpFigmaClient:
    """Default transport: JSON over HTTP to the Figma MCP endpoint.

    Assumed contract (isolated so it's the only thing to adjust against a real server): ``POST
    {figma_mcp_url}`` with ``{"method": <tool>, "params": {...}}`` + ``X-Figma-Token: <token>`` →
    ``{external_ref, html, css, preview_image?, assets?, meta?}``. Status maps to the taxonomy
    (401/403→auth, timeout/5xx/429→transient, other 4xx→fatal).
    """

    async def _rpc(self, method: str, params: dict[str, Any], token: str) -> FigmaDesignPayload:
        config = get_config()
        url = str(config.get("figma_mcp_url"))
        if not url:
            raise figma_error(FigmaErrorKind.auth, "Figma MCP URL is not configured")
        try:
            async with httpx.AsyncClient(timeout=float(config.get("figma_timeout_s"))) as client:
                response = await client.post(
                    url,
                    json={"method": method, "params": params},
                    headers={"X-Figma-Token": token},
                )
        except httpx.HTTPError as exc:
            raise figma_error(FigmaErrorKind.transient, f"Figma request failed ({method})") from exc

        self._raise_for_status(response, method)
        try:
            return FigmaDesignPayload.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise figma_error(FigmaErrorKind.fatal, f"Malformed Figma response ({method})") from exc

    @staticmethod
    def _raise_for_status(response: httpx.Response, method: str) -> None:
        code = response.status_code
        if code < 400:
            return
        if code in (401, 403):
            raise figma_error(FigmaErrorKind.auth, f"Figma rejected the token ({code})")
        if code == 429 or code >= 500:
            raise figma_error(FigmaErrorKind.transient, f"Figma is unavailable ({code})")
        raise figma_error(FigmaErrorKind.fatal, f"Figma rejected the request ({code}, {method})")

    async def text_to_ui(self, prompt: str, *, token: str) -> FigmaDesignPayload:
        return await self._rpc("text_to_ui", {"prompt": prompt}, token)

    async def refine(
        self, external_ref: str, instruction: str, *, token: str
    ) -> FigmaDesignPayload:
        return await self._rpc(
            "refine", {"external_ref": external_ref, "instruction": instruction}, token
        )

    async def fetch_code(self, external_ref: str, *, token: str) -> FigmaDesignPayload:
        return await self._rpc("fetch_code", {"external_ref": external_ref}, token)


def _kind_of(error: ProviderError) -> str | None:
    detail = error.detail
    return detail.get("kind") if isinstance(detail, dict) else None


class FigmaDesignProvider:
    key = PROVIDER_KEY

    def __init__(
        self,
        *,
        client: FigmaClient | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        self._client: FigmaClient = client or HttpFigmaClient()
        self._sleep: Sleeper = sleeper or asyncio.sleep

    # -- capabilities / health ------------------------------------------------------------

    def capabilities(self) -> DesignCapabilities:
        # Honest: Figma has no screenshot→UI, so from_image=False and the UI hides that affordance.
        return DesignCapabilities(
            provider=self.key,
            from_image=False,
            from_text=True,
            refine=True,
            fetch_code=True,
            max_images=0,
        )

    async def health(self) -> ProviderHealth:
        # No token → down (not a crash): the stage falls back. There is no quota to degrade on.
        return ProviderHealth.ok if await figma_token_present() else ProviderHealth.down

    # -- flows ----------------------------------------------------------------------------

    async def generate_from_text(
        self,
        prompt: str,
        *,
        workspace: str | None = None,
        workspace_title: str | None = None,
    ) -> DesignResult:
        token = await get_figma_token()
        payload = await self._invoke(lambda t: self._client.text_to_ui(prompt, token=t), token)
        return self._to_result(payload, {"source": "text"})

    async def generate_from_image(
        self,
        images: list[DesignImage],
        prompt: str | None = None,
        *,
        workspace: str | None = None,
        workspace_title: str | None = None,
    ) -> DesignResult:
        # Unsupported capability — degrade clearly, never fake a result.
        raise figma_error(
            FigmaErrorKind.unsupported,
            "Figma cannot generate from a screenshot; use a text prompt or switch to Stitch",
        )

    async def refine(self, design_ref: str, instruction: str) -> DesignResult:
        token = await get_figma_token()
        payload = await self._invoke(
            lambda t: self._client.refine(design_ref, instruction, token=t), token
        )
        return self._to_result(payload, {"source": "refine", "refined_from": design_ref})

    async def fetch_code(self, design_ref: str) -> DesignCode:
        token = await get_figma_token()
        payload = await self._invoke(lambda t: self._client.fetch_code(design_ref, token=t), token)
        return DesignCode(html=payload.html, css=payload.css, assets=payload.assets)

    # -- internals ------------------------------------------------------------------------

    def _to_result(self, payload: FigmaDesignPayload, extra_meta: dict[str, Any]) -> DesignResult:
        return DesignResult(
            provider=self.key,
            external_ref=payload.external_ref,
            html=payload.html,
            css=payload.css,
            preview_image=payload.preview_image,
            meta={**payload.meta, **extra_meta},  # provider-specific meta only — never the token
        )

    async def _invoke(
        self, make_call: Callable[[str], Awaitable[FigmaDesignPayload]], token: str
    ) -> FigmaDesignPayload:
        """Run a transport call with transient backoff/retry (the token is static — no refresh)."""
        max_retries = int(get_config().get("figma_max_retries"))
        attempt = 0
        while True:
            try:
                return await make_call(token)
            except ProviderError as exc:
                if _kind_of(exc) == FigmaErrorKind.transient and attempt < max_retries:
                    attempt += 1
                    await self._sleep(0.5 * 2 ** (attempt - 1))
                    continue
                raise
