"""Stitch design provider (phase-17) — the default, highest-risk design backend (D9/D10).

Wires three concerns behind the provider-agnostic :class:`DesignProvider` contract:

- **Transport** (:class:`StitchClient`): speaks the **Stitch MCP API** — JSON-RPC 2.0 over HTTP to
  ``https://stitch.googleapis.com/mcp``. :class:`HttpStitchClient` implements the real protocol
  (initialize handshake → ``tools/call``), and stays behind the Protocol so tests inject a fake.
- **Auth** (:class:`~app.design.stitch_auth.StitchAuth`): API key, Google bearer token, or a
  client-credentials exchange — resolved per call into headers.
- **Quota** (:class:`~app.design.stitch_quota.StitchQuota`): pre-check + fail-soft at the cap.

**Capability note:** Stitch's API surface is *text-prompt only* — ``generate_screen_from_text``,
``edit_screens``, ``generate_variants`` and the project/screen reads. Screenshot→UI exists in the
Stitch web app but has no tool on the API, so :meth:`StitchDesignProvider.capabilities` reports
``from_image=False`` and screenshot intake degrades to the next provider in the fallback chain
(phase-48) instead of silently producing a design that ignored the screenshots.

Failures are classified (auth/quota/transient/fatal/unsupported) as :class:`ProviderError` with a
fallback hint. Missing credentials → ``health()==down`` (never a crash) so the stage falls back.
Keys and tokens are never logged or placed in any :class:`DesignResult`/artifact/event.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
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

Sleeper = Callable[[float], Awaitable[None]]
Headers = Mapping[str, str]

#: MCP protocol revision announced in the initialize handshake.
MCP_PROTOCOL_VERSION = "2024-11-05"
#: Streamable-HTTP servers may answer either JSON or an SSE stream; accept both.
MCP_ACCEPT = "application/json, text/event-stream"
#: Header carrying the session id a stateful MCP server hands out at initialize.
SESSION_HEADER = "Mcp-Session-Id"

#: Stitch tool names (camelCase arguments, as the API's proto-derived schema declares them).
TOOL_CREATE_PROJECT = "create_project"
TOOL_GENERATE_FROM_TEXT = "generate_screen_from_text"
TOOL_EDIT_SCREENS = "edit_screens"
TOOL_GET_SCREEN = "get_screen"


class StitchDesignPayload(BaseModel):
    """Normalized Stitch response (what the transport hands back)."""

    external_ref: str = ""
    html: str = ""
    css: str = ""
    preview_image: str | None = None
    assets: dict[str, str] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class StitchClient(Protocol):
    """The Stitch MCP transport. ``headers`` carry the credential; never store or log them."""

    async def text_to_ui(self, prompt: str, *, headers: Headers) -> StitchDesignPayload: ...

    async def refine(
        self, external_ref: str, instruction: str, *, headers: Headers
    ) -> StitchDesignPayload: ...

    async def fetch_code(self, external_ref: str, *, headers: Headers) -> StitchDesignPayload: ...


# --------------------------------------------------------------------------- response parsing


def _decode_rpc(response: httpx.Response) -> dict[str, Any]:
    """One JSON-RPC message out of either a plain JSON body or an SSE stream."""
    text = response.text.strip()
    if not text:
        raise stitch_error(StitchErrorKind.fatal, "Stitch returned an empty response")

    if "text/event-stream" in response.headers.get("content-type", ""):
        for line in reversed(text.splitlines()):
            if not line.startswith("data:"):
                continue
            try:
                message = json.loads(line[len("data:") :].strip())
            except ValueError:
                continue
            if isinstance(message, dict) and ("result" in message or "error" in message):
                return message
        raise stitch_error(StitchErrorKind.fatal, "Stitch SSE stream carried no JSON-RPC result")

    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise stitch_error(StitchErrorKind.fatal, "Malformed Stitch response") from exc
    if not isinstance(parsed, dict):
        raise stitch_error(StitchErrorKind.fatal, "Malformed Stitch response")
    return parsed


def _tool_result_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    """The useful object out of an MCP tool result.

    MCP allows a tool to answer with ``structuredContent`` and/or ``content`` blocks; Stitch's
    exact field names are not contractually documented, so this normalizes both shapes and the
    caller then plucks by any of several plausible keys. That keeps us dependent on as little of
    the undocumented surface as possible.
    """
    if result.get("isError"):
        raise stitch_error(StitchErrorKind.fatal, _error_text(result))

    structured = result.get("structuredContent")
    if isinstance(structured, dict) and structured:
        return structured

    texts: list[str] = []
    for block in result.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = str(block.get("text", ""))
        texts.append(text)
        try:
            parsed = json.loads(text)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {"text": "\n".join(texts)}


def _error_text(result: Mapping[str, Any]) -> str:
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return f"Stitch tool error: {str(block.get('text', ''))[:300]}"
    return "Stitch reported a tool error"


def _pluck(data: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    """First present value for ``keys``, looking one level into nested objects too."""
    for key in keys:
        if data.get(key) not in (None, ""):
            return data[key]
    for value in data.values():
        if isinstance(value, dict):
            for key in keys:
                if value.get(key) not in (None, ""):
                    return value[key]
    return None


def _resource_id(value: Any) -> str:
    """``projects/123/screens/456`` → ``456``; a bare id passes through."""
    text = str(value or "").strip()
    return text.rsplit("/", 1)[-1] if "/" in text else text


_SCREEN_KEYS = ("screenId", "screen_id", "id", "name")
_PROJECT_KEYS = ("projectId", "project_id", "id", "name")
_HTML_KEYS = ("html", "htmlUrl", "html_url", "htmlDownloadUrl", "downloadUrl", "code")
_IMAGE_KEYS = ("imageUrl", "image_url", "screenshotUrl", "previewImage", "image")


class HttpStitchClient:
    """Default transport: the official Stitch MCP endpoint, spoken properly.

    Protocol (MCP streamable HTTP): ``POST {stitch_mcp_url}`` with a JSON-RPC 2.0 envelope. The
    first call performs the ``initialize`` handshake, keeps any ``Mcp-Session-Id`` the server
    returns, and sends ``notifications/initialized``; subsequent calls are
    ``{"method": "tools/call", "params": {"name": …, "arguments": {…}}}``.

    Stitch is project-scoped, so a screen always belongs to a project. ``STITCH_PROJECT_ID`` pins
    an existing one; otherwise a project is created on first use and reused for the process. A
    design's ``external_ref`` is therefore ``"{projectId}/{screenId}"``.

    HTTP status maps to the error taxonomy (401/403→auth, 429→quota, timeout/5xx/network→transient,
    other 4xx→fatal); a JSON-RPC ``error`` or a tool ``isError`` is fatal.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._session_id: str | None = None
        self._initialized = False
        self._project_id: str | None = None
        self._rpc_id = 0

    # -- transport ---------------------------------------------------------------------------

    def _url(self) -> str:
        url = str(get_config().get("stitch_mcp_url")).strip()
        if not url:
            raise stitch_error(StitchErrorKind.auth, "Stitch MCP URL is not configured")
        return url

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    async def _post(
        self, body: dict[str, Any], headers: Headers
    ) -> tuple[dict[str, Any] | None, httpx.Response]:
        request_headers = {
            **headers,
            "Content-Type": "application/json",
            "Accept": MCP_ACCEPT,
        }
        if self._session_id:
            request_headers[SESSION_HEADER] = self._session_id

        timeout = float(get_config().get("stitch_timeout_s"))
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(self._url(), json=body, headers=request_headers)
        except httpx.HTTPError as exc:
            raise stitch_error(
                StitchErrorKind.transient, f"Stitch request failed ({body.get('method')})"
            ) from exc

        self._raise_for_status(response, str(body.get("method")))
        if response.status_code == 202 or not response.content:  # a notification was accepted
            return None, response
        return _decode_rpc(response), response

    @staticmethod
    def _raise_for_status(response: httpx.Response, method: str) -> None:
        code = response.status_code
        if code < 400:
            return
        if code in (401, 403):
            raise stitch_error(StitchErrorKind.auth, f"Stitch rejected the credential ({code})")
        if code == 429:
            raise stitch_error(StitchErrorKind.quota, "Stitch reported the quota is exceeded")
        if code >= 500:
            raise stitch_error(StitchErrorKind.transient, f"Stitch server error ({code})")
        raise stitch_error(StitchErrorKind.fatal, f"Stitch rejected the request ({code}, {method})")

    async def _rpc(self, method: str, params: dict[str, Any], headers: Headers) -> dict[str, Any]:
        body = {"jsonrpc": "2.0", "id": self._next_id(), "method": method, "params": params}
        message, response = await self._post(body, headers)
        if self._session_id is None and response.headers.get(SESSION_HEADER):
            self._session_id = response.headers[SESSION_HEADER]
        if message is None:
            return {}
        if "error" in message:
            error = message["error"] if isinstance(message["error"], dict) else {}
            raise stitch_error(
                StitchErrorKind.fatal,
                f"Stitch rejected {method}: {str(error.get('message', ''))[:200]}",
            )
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    async def _ensure_session(self, headers: Headers) -> None:
        """One initialize handshake per client instance (single-flight)."""
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            await self._rpc(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "forgeflow", "version": "1"},
                },
                headers,
            )
            await self._post(
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, headers
            )
            self._initialized = True

    async def _call_tool(
        self, name: str, arguments: dict[str, Any], headers: Headers
    ) -> dict[str, Any]:
        """Invoke one MCP tool, re-handshaking once if the session was dropped server-side."""
        await self._ensure_session(headers)
        params = {"name": name, "arguments": arguments}
        try:
            result = await self._rpc("tools/call", params, headers)
        except ProviderError as exc:
            if _kind_of(exc) != StitchErrorKind.fatal or not self._session_id:
                raise
            # A expired/unknown session reads as a plain 4xx; re-handshake once, then give up.
            self._session_id = None
            self._initialized = False
            await self._ensure_session(headers)
            result = await self._rpc("tools/call", params, headers)
        return _tool_result_payload(result)

    # -- Stitch domain -----------------------------------------------------------------------

    async def _ensure_project(self, headers: Headers) -> str:
        configured = str(get_config().get("stitch_project_id")).strip()
        if configured:
            return _resource_id(configured)
        if self._project_id:
            return self._project_id

        payload = await self._call_tool(
            TOOL_CREATE_PROJECT, {"name": "ForgeFlow", "title": "ForgeFlow"}, headers
        )
        project_id = _resource_id(_pluck(payload, _PROJECT_KEYS))
        if not project_id:
            raise stitch_error(StitchErrorKind.fatal, "Stitch did not return a project id")
        self._project_id = project_id
        return project_id

    @staticmethod
    def _split_ref(external_ref: str, fallback_project: str | None = None) -> tuple[str, str]:
        """``"{projectId}/{screenId}"`` → both halves; a bare screen id keeps the known project."""
        ref = external_ref.strip()
        if "/" in ref:
            project, screen = ref.rsplit("/", 1)
            return _resource_id(project), screen
        if not fallback_project:
            raise stitch_error(StitchErrorKind.fatal, "This design has no Stitch project reference")
        return fallback_project, ref

    async def _hydrate(
        self, project_id: str, payload: Mapping[str, Any], headers: Headers
    ) -> StitchDesignPayload:
        """Turn a tool result into a design payload, fetching the screen and its HTML as needed."""
        screen_id = _resource_id(_pluck(payload, _SCREEN_KEYS))
        html_ref = _pluck(payload, _HTML_KEYS)
        image_ref = _pluck(payload, _IMAGE_KEYS)

        if screen_id and html_ref is None:
            # A generate/edit result may only carry the screen id; read the screen for its code.
            screen = await self._call_tool(
                TOOL_GET_SCREEN, {"projectId": project_id, "screenId": screen_id}, headers
            )
            html_ref = _pluck(screen, _HTML_KEYS)
            image_ref = image_ref or _pluck(screen, _IMAGE_KEYS)

        return StitchDesignPayload(
            external_ref=f"{project_id}/{screen_id}" if screen_id else "",
            html=await self._resolve_html(html_ref),
            # Stitch emits a single self-contained HTML document (utility classes inline), so
            # there is no separate stylesheet to carry.
            css="",
            preview_image=str(image_ref) if image_ref else None,
            meta={"stitch_project": project_id, "stitch_screen": screen_id},
        )

    async def _resolve_html(self, html_ref: Any) -> str:
        """Stitch returns HTML as a download URL; literal markup is passed through unchanged."""
        text = str(html_ref or "")
        if not text.startswith(("http://", "https://")):
            return text
        try:
            async with httpx.AsyncClient(
                timeout=float(get_config().get("stitch_timeout_s")), follow_redirects=True
            ) as client:
                response = await client.get(text)
        except httpx.HTTPError as exc:
            raise stitch_error(
                StitchErrorKind.transient, "Could not download the Stitch screen HTML"
            ) from exc
        if response.status_code >= 400:
            raise stitch_error(
                StitchErrorKind.transient,
                f"Stitch HTML download failed ({response.status_code})",
            )
        return response.text

    # -- the StitchClient Protocol -------------------------------------------------------------

    async def text_to_ui(self, prompt: str, *, headers: Headers) -> StitchDesignPayload:
        project_id = await self._ensure_project(headers)
        arguments: dict[str, Any] = {"projectId": project_id, "prompt": prompt}
        model_id = str(get_config().get("stitch_model_id")).strip()
        if model_id:
            arguments["modelId"] = model_id
        payload = await self._call_tool(TOOL_GENERATE_FROM_TEXT, arguments, headers)
        return await self._hydrate(project_id, payload, headers)

    async def refine(
        self, external_ref: str, instruction: str, *, headers: Headers
    ) -> StitchDesignPayload:
        project_id, screen_id = self._split_ref(external_ref, self._project_id)
        payload = await self._call_tool(
            TOOL_EDIT_SCREENS,
            {"projectId": project_id, "screenIds": [screen_id], "prompt": instruction},
            headers,
        )
        # An edit may answer with the edited screen, or with nothing but a status; either way the
        # screen id is the one we edited unless the server names a new one.
        if not _pluck(payload, _SCREEN_KEYS):
            payload = {**payload, "screenId": screen_id}
        return await self._hydrate(project_id, payload, headers)

    async def fetch_code(self, external_ref: str, *, headers: Headers) -> StitchDesignPayload:
        project_id, screen_id = self._split_ref(external_ref, self._project_id)
        payload = await self._call_tool(
            TOOL_GET_SCREEN, {"projectId": project_id, "screenId": screen_id}, headers
        )
        return await self._hydrate(project_id, {**payload, "screenId": screen_id}, headers)


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
        # Honest: the Stitch API generates from a text prompt only (screenshot→UI is web-app
        # only), so screenshot intake routes to another provider instead of quietly ignoring
        # the images.
        return DesignCapabilities(
            provider=self.key,
            from_image=False,
            from_text=True,
            refine=True,
            fetch_code=True,
            max_images=0,
        )

    async def health(self) -> ProviderHealth:
        # No creds → down (not a crash): the stage falls back to figma/fake.
        if not await stitch_credentials_present():
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
        await self._require_credentials()
        await self._quota.check_available()
        payload = await self._invoke(lambda h: self._client.text_to_ui(prompt, headers=h))
        await self._quota.record_usage()
        return self._to_result(payload, {"source": "text"})

    async def generate_from_image(
        self, images: list[DesignImage], prompt: str | None = None
    ) -> DesignResult:
        # Unsupported capability — degrade clearly, never fake a result.
        raise stitch_error(
            StitchErrorKind.unsupported,
            "Stitch's API generates from a text prompt only; describe the UI in text, "
            "or switch the design provider to 'fake' to use screenshots",
        )

    async def refine(self, design_ref: str, instruction: str) -> DesignResult:
        await self._require_credentials()
        await self._quota.check_available()
        payload = await self._invoke(
            lambda h: self._client.refine(design_ref, instruction, headers=h)
        )
        await self._quota.record_usage()
        return self._to_result(payload, {"source": "refine", "refined_from": design_ref})

    async def fetch_code(self, design_ref: str) -> DesignCode:
        # Metadata read — does not consume a generation.
        await self._require_credentials()
        payload = await self._invoke(lambda h: self._client.fetch_code(design_ref, headers=h))
        return DesignCode(html=payload.html, css=payload.css, assets=payload.assets)

    # -- internals ------------------------------------------------------------------------

    async def _require_credentials(self) -> None:
        if not await stitch_credentials_present():
            raise stitch_error(StitchErrorKind.auth, "Stitch credentials are not configured")

    def _to_result(self, payload: StitchDesignPayload, extra_meta: dict[str, Any]) -> DesignResult:
        return DesignResult(
            provider=self.key,
            external_ref=payload.external_ref,
            html=payload.html,
            css=payload.css,
            preview_image=payload.preview_image,
            # Provider-specific meta only — no credential ever lands here.
            meta={**payload.meta, **extra_meta},
        )

    async def _invoke(
        self, make_call: Callable[[Headers], Awaitable[StitchDesignPayload]]
    ) -> StitchDesignPayload:
        """Run a transport call with transparent credential refresh + transient backoff/retry."""
        max_retries = int(get_config().get("stitch_max_retries"))
        refreshed = False
        attempt = 0
        while True:
            headers = await self._auth.headers()
            try:
                return await make_call(headers)
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
