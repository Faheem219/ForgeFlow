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
import base64
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, Field

from app.core.config import get_config
from app.core.errors import ProviderError
from app.design.base import (
    DesignCapabilities,
    DesignCode,
    DesignImage,
    DesignResult,
    DesignScreen,
    DesignScreenRef,
    ProviderHealth,
)
from app.design.stitch_auth import StitchAuth, stitch_credentials_present
from app.design.stitch_errors import PROVIDER_KEY, StitchErrorKind, stitch_error
from app.design.stitch_quota import QuotaStatus, StitchQuota

logger = logging.getLogger(__name__)

Sleeper = Callable[[float], Awaitable[None]]
Headers = Mapping[str, str]
T = TypeVar("T")

#: MCP protocol revision announced in the initialize handshake.
MCP_PROTOCOL_VERSION = "2024-11-05"
#: Streamable-HTTP servers may answer either JSON or an SSE stream; accept both.
MCP_ACCEPT = "application/json, text/event-stream"
#: Header carrying the session id a stateful MCP server hands out at initialize.
SESSION_HEADER = "Mcp-Session-Id"
#: Gap between re-reads while waiting for an asynchronously generated screen to carry its code.
SCREEN_POLL_INTERVAL_S = 3.0

#: Stitch tool names. The server is the authority: :meth:`HttpStitchClient._discover` reads
#: ``tools/list`` and each of these resolves to whichever alias the endpoint actually exposes.
TOOL_CREATE_PROJECT = "create_project"
TOOL_GENERATE_FROM_TEXT = "generate_screen_from_text"
TOOL_EDIT_SCREENS = "edit_screens"
TOOL_GET_SCREEN = "get_screen"
TOOL_LIST_SCREENS = "list_screens"

#: Accepted spellings per tool, most likely first.
_TOOL_ALIASES: dict[str, tuple[str, ...]] = {
    TOOL_CREATE_PROJECT: ("create_project", "createProject", "create_stitch_project"),
    TOOL_GENERATE_FROM_TEXT: (
        "generate_screen_from_text",
        "generateScreenFromText",
        "generate_screen",
        "generate_from_text",
    ),
    TOOL_EDIT_SCREENS: ("edit_screens", "editScreens", "edit_screen", "update_screens"),
    TOOL_GET_SCREEN: ("get_screen", "getScreen"),
    TOOL_LIST_SCREENS: ("list_screens", "listScreens"),
}

#: Accepted spellings per *argument*. We send our canonical name renamed to whatever the tool's
#: advertised ``inputSchema`` calls it, and drop anything the schema does not declare — a strict
#: server answers an unknown field with INVALID_ARGUMENT, which is not worth guessing about.
_ARG_ALIASES: dict[str, tuple[str, ...]] = {
    "projectId": ("projectId", "project_id", "project"),
    "screenId": ("screenId", "screen_id", "screen"),
    # `edit_screens` calls this `selectedScreenIds`.
    "selectedScreenIds": ("selectedScreenIds", "selected_screen_ids", "screenIds", "screens"),
    "prompt": ("prompt", "text", "description", "instruction", "query"),
    "modelId": ("modelId", "model_id", "model"),
    # `create_project` takes a `title`.
    "title": ("title", "name", "displayName", "display_name", "projectName"),
    # `get_screen` takes the full resource `name` (projects/…/screens/…).
    "name": ("name", "resourceName", "resource_name"),
}


class StitchDesignPayload(BaseModel):
    """Normalized Stitch response (what the transport hands back)."""

    external_ref: str = ""
    html: str = ""
    css: str = ""
    preview_image: str | None = None
    #: Populated only when the response carried more than one screen (primary first).
    screens: list[DesignScreen] = Field(default_factory=list)
    assets: dict[str, str] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class StitchClient(Protocol):
    """The Stitch MCP transport. ``headers`` carry the credential; never store or log them."""

    async def text_to_ui(self, prompt: str, *, headers: Headers) -> StitchDesignPayload: ...

    async def refine(
        self, external_ref: str, instruction: str, *, headers: Headers
    ) -> StitchDesignPayload: ...

    async def fetch_code(self, external_ref: str, *, headers: Headers) -> StitchDesignPayload: ...

    async def list_screens(self, *, headers: Headers) -> list[DesignScreenRef]: ...


# --------------------------------------------------------------------------- response parsing


def _decode_rpc(response: httpx.Response) -> dict[str, Any]:
    """One JSON-RPC message out of either a plain JSON body or an SSE stream."""
    text = response.text.strip()
    if not text:
        raise stitch_error(StitchErrorKind.contract, "Stitch returned an empty response")

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
        raise stitch_error(StitchErrorKind.contract, "Stitch SSE stream carried no JSON-RPC result")

    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise stitch_error(StitchErrorKind.contract, "Malformed Stitch response") from exc
    if not isinstance(parsed, dict):
        raise stitch_error(StitchErrorKind.contract, "Malformed Stitch response")
    return parsed


def _looks_like_html(text: str) -> bool:
    head = text.lstrip()[:400].lower()
    return head.startswith("<") and ("<html" in head or "<body" in head or "<div" in head)


def _tool_result_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    """The useful object out of an MCP tool result.

    MCP lets a tool answer with ``structuredContent``, JSON inside ``text`` blocks, or **resource**
    blocks (a URI + mime type, optionally with the body inline) — and Stitch's exact field names are
    not contractually documented. All three are harvested into one flat dict that the caller then
    plucks by any of several plausible keys, so we depend on as little of the undocumented surface
    as possible. Reading only ``text`` blocks (the first cut) silently produced empty designs when
    the screen came back as a resource.
    """
    if result.get("isError"):  # normally raised with more context by ``_call_tool``
        raise stitch_error(StitchErrorKind.contract, _error_text(result))

    harvested: dict[str, Any] = {}
    texts: list[str] = []

    for block in result.get("content") or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")

        if kind == "text":
            text = str(block.get("text", ""))
            texts.append(text)
            try:
                parsed = json.loads(text)
            except ValueError:
                if _looks_like_html(text):
                    harvested.setdefault("html", text)
                continue
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    harvested.setdefault(key, value)

        elif kind in ("resource", "resource_link"):
            resource = block.get("resource")
            body = resource if isinstance(resource, dict) else block
            uri = str(body.get("uri") or body.get("url") or "")
            mime = str(body.get("mimeType") or body.get("mime_type") or "")
            inline = body.get("text")
            if mime.startswith("image/") or uri.endswith((".png", ".jpg", ".jpeg", ".webp")):
                if uri:
                    harvested.setdefault("imageUrl", uri)
            elif inline or uri:
                # HTML (or anything textual) — inline body wins, else the URI to download.
                harvested.setdefault("html", str(inline) if inline else uri)

    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        for key, value in structured.items():
            harvested[key] = value  # an explicit structured field is the most reliable source

    if not harvested and texts:
        harvested["text"] = "\n".join(texts)
    return harvested


def _error_text(result: Mapping[str, Any]) -> str:
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return f"Stitch tool error: {str(block.get('text', ''))[:300]}"
    return "Stitch reported a tool error"


#: What Stitch says when the project/screen we referenced isn't there (its NOT_FOUND wording).
_NOT_FOUND_MARKERS = ("requested entity was not found", "not_found", "does not exist")

_MISSING_ENTITY_HINT = (
    "The Stitch project or screen this design points at no longer exists (deleted in Stitch, or "
    "created with different credentials). Generate a new design to get a fresh Stitch project — "
    "the existing design versions are kept."
)


def _is_not_found(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _NOT_FOUND_MARKERS)


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


def _looks_like_stitch_id(value: str) -> bool:
    """Whether a bare id could be a Stitch project/screen id (hex screen ids, numeric projects).

    Used to tell *our* ids apart from another provider's ``external_ref`` before we send one to
    Stitch — a wrong guess there surfaces as an unexplained not-found from the server.
    """
    text = value.strip()
    return len(text) >= 8 and all(char in "0123456789abcdefABCDEF" for char in text)


def _summarize(payload: Mapping[str, Any]) -> str:
    """A short, safe dump of what Stitch actually sent — so a shape mismatch is self-diagnosing."""
    try:
        raw = json.dumps(payload, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        raw = repr(payload)
    return f" [returned keys: {sorted(payload)}; raw: {raw[:400]}]"


_SCREEN_KEYS = ("screenId", "screen_id", "id", "name")
_PROJECT_KEYS = ("projectId", "project_id", "id", "name")
_HTML_KEYS = ("html", "htmlUrl", "html_url", "htmlDownloadUrl", "downloadUrl", "code")
_IMAGE_KEYS = ("imageUrl", "image_url", "screenshotUrl", "previewImage", "image")


def _screen_objects(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """**Every** screen in a tool result, in order.

    One generate/edit can produce a whole flow (a light screen and its dark variant, say), and a
    refine adds screens to the Stitch project rather than replacing them — so keeping only the
    first would quietly discard designs the user can see in Stitch.
    """
    for component in payload.get("outputComponents") or []:
        if not isinstance(component, Mapping):
            continue
        design = component.get("design")
        screens = design.get("screens") if isinstance(design, Mapping) else None
        if isinstance(screens, list):
            found = [s for s in screens if isinstance(s, Mapping)]
            if found:
                return found
    return [payload]


def _screen_object(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """The **primary** screen inside a Stitch tool result, whichever tool produced it.

    ``generate_screen_from_text`` / ``edit_screens`` answer with a *session*::

        {"projectId": …, "sessionId": …,
         "outputComponents": [{"design": {"screens": [ <screen>, … ]}}, …]}

    while ``get_screen`` answers with the screen itself. A screen carries ``id``/``name`` plus
    ``htmlCode`` and ``screenshot`` File objects. Anything unrecognised falls through as-is, so the
    generic key search still gets a chance.
    """
    return _screen_objects(payload)[0]


def _file_parts(file_obj: Any) -> tuple[str, str]:
    """``(inline base64, download url)`` out of a Stitch ``File``; either half may be empty."""
    if isinstance(file_obj, Mapping):
        inline = str(file_obj.get("fileContentBase64") or "")
        url = str(file_obj.get("downloadUrl") or file_obj.get("uri") or "")
        return inline, url
    return "", str(file_obj or "")


def _screen_title(screen: Mapping[str, Any], fallback_id: str) -> str:
    """A human label for a screen tab: its title, else its id."""
    title = str(screen.get("title") or screen.get("label") or "").strip()
    return title or fallback_id


def _decode_inline(base64_text: str) -> str:
    try:
        return base64.b64decode(base64_text).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


class HttpStitchClient:
    """Default transport: the official Stitch MCP endpoint, spoken properly.

    Protocol (MCP streamable HTTP): ``POST {stitch_mcp_url}`` with a JSON-RPC 2.0 envelope. The
    first call performs the ``initialize`` handshake, keeps any ``Mcp-Session-Id`` the server
    returns, and sends ``notifications/initialized``; subsequent calls are
    ``{"method": "tools/call", "params": {"name": …, "arguments": {…}}}``.

    **The tool contract is discovered, not assumed.** After the handshake the client reads
    ``tools/list`` and uses each tool's advertised ``inputSchema`` to pick the real tool name and
    the real argument names, dropping any argument the schema does not declare. Google's API
    answers an unknown or missing field with ``INVALID_ARGUMENT``, so guessing the spelling is a
    liability; asking is one extra call per session and survives the API renaming things.

    Stitch is project-scoped, so a screen always belongs to a project. ``STITCH_PROJECT_ID`` pins
    an existing one; otherwise a project is created on first use and reused for the process. A
    design's ``external_ref`` is therefore ``"{projectId}/{screenId}"``.

    HTTP status maps to the error taxonomy (401/403→auth, 429→quota, timeout/5xx/network→transient,
    other 4xx→contract); a JSON-RPC ``error`` or a tool ``isError`` is likewise ``contract``, which
    falls back to another provider rather than dead-ending the stage.
    """

    def __init__(self, *, sleeper: Sleeper | None = None) -> None:
        self._lock = asyncio.Lock()
        self._session_id: str | None = None
        self._initialized = False
        self._project_id: str | None = None
        self._rpc_id = 0
        #: tool name → its advertised JSON Schema, from ``tools/list``. Empty when undiscoverable.
        self._schemas: dict[str, dict[str, Any]] = {}
        self._sleep: Sleeper = sleeper or asyncio.sleep

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
        raise stitch_error(
            StitchErrorKind.contract, f"Stitch rejected the request ({code}, {method})"
        )

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
                StitchErrorKind.contract,
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
            await self._discover(headers)

    async def _discover(self, headers: Headers) -> None:
        """Read ``tools/list`` so calls use the server's real tool and argument names.

        Best-effort: a server that refuses the listing just means no adaptation happens, which is
        no worse than not asking. Never raises.
        """
        try:
            result = await self._rpc("tools/list", {}, headers)
        except ProviderError:
            logger.warning("stitch: tools/list unavailable; calling with default argument names")
            return
        schemas: dict[str, dict[str, Any]] = {}
        for tool in result.get("tools") or []:
            if not isinstance(tool, dict):
                continue
            name = str(tool.get("name", ""))
            schema = tool.get("inputSchema") or tool.get("input_schema") or {}
            if name and isinstance(schema, dict):
                schemas[name] = schema
        self._schemas = schemas
        logger.info("stitch: discovered %d tools", len(schemas))

    def _tool_name(self, canonical: str) -> str:
        """The name this server exposes for ``canonical`` (unchanged if nothing was discovered)."""
        if not self._schemas:
            return canonical
        for alias in _TOOL_ALIASES.get(canonical, (canonical,)):
            if alias in self._schemas:
                return alias
        return canonical

    def _adapt(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Rename our canonical arguments to the tool's own, dropping any it doesn't declare."""
        schema = self._schemas.get(tool_name)
        properties = schema.get("properties") if schema else None
        if not isinstance(properties, dict):
            return arguments  # nothing advertised — send what we have
        # An advertised-but-empty property map means the tool takes no arguments at all.

        adapted: dict[str, Any] = {}
        for canonical, value in arguments.items():
            for alias in _ARG_ALIASES.get(canonical, (canonical,)):
                if alias in properties:
                    adapted[alias] = value
                    break
        return adapted

    def _schema_hint(self, tool_name: str) -> str:
        """What the tool says it wants — appended to a rejection so the next fix is obvious."""
        schema = self._schemas.get(tool_name)
        if not schema:
            return ""
        properties = schema.get("properties")
        accepted = sorted(properties) if isinstance(properties, dict) else []
        raw_required = schema.get("required")
        required = sorted(str(r) for r in raw_required) if isinstance(raw_required, list) else []
        if not accepted and not required:
            return ""
        return f" [{tool_name} accepts {accepted}; requires {required}]"

    async def _call_tool(
        self, canonical: str, arguments: dict[str, Any], headers: Headers
    ) -> dict[str, Any]:
        """Invoke one MCP tool, re-handshaking once if the session was dropped server-side."""
        await self._ensure_session(headers)
        name = self._tool_name(canonical)
        params = {"name": name, "arguments": self._adapt(name, arguments)}
        try:
            result = await self._rpc("tools/call", params, headers)
        except ProviderError as exc:
            if _kind_of(exc) != StitchErrorKind.contract or not self._session_id:
                raise
            # An expired/unknown session reads as a plain 4xx; re-handshake once, then give up.
            self._session_id = None
            self._initialized = False
            await self._ensure_session(headers)
            name = self._tool_name(canonical)
            params = {"name": name, "arguments": self._adapt(name, arguments)}
            result = await self._rpc("tools/call", params, headers)

        if result.get("isError"):
            # Name the tool and what it wanted — an argument mismatch should be one read away.
            text = _error_text(result)
            raise stitch_error(
                StitchErrorKind.contract,
                f"{text} (tool: {name}, sent: {sorted(params['arguments'])})"
                f"{self._schema_hint(name)}",
                # A NOT_FOUND is not an argument problem: the project or screen we referenced is
                # gone from Stitch (deleted there, or created under different credentials). Say
                # that, instead of pointing the user at a schema that is perfectly correct.
                hint=_MISSING_ENTITY_HINT if _is_not_found(text) else None,
            )
        return _tool_result_payload(result)

    # -- Stitch domain -----------------------------------------------------------------------

    async def _ensure_project(self, headers: Headers) -> str:
        configured = str(get_config().get("stitch_project_id")).strip()
        if configured:
            return _resource_id(configured)
        if self._project_id:
            return self._project_id

        # `create_project` takes a `title` and answers with the project's resource `name`.
        payload = await self._call_tool(TOOL_CREATE_PROJECT, {"title": "ForgeFlow"}, headers)
        project_id = _resource_id(_pluck(payload, _PROJECT_KEYS))
        if not project_id:
            raise stitch_error(StitchErrorKind.contract, "Stitch did not return a project id")
        self._project_id = project_id
        return project_id

    @staticmethod
    def _split_ref(external_ref: str, fallback_project: str | None = None) -> tuple[str, str]:
        """``"{projectId}/{screenId}"`` → both halves; a bare Stitch screen id keeps the project.

        A ref that is not Stitch's own shape belongs to **another provider** (``fake-text-…``, a
        Figma file key, …). Pairing such an id with whatever project this client happens to have
        cached is what turned a provider mix-up into an opaque *"Requested entity was not found"*
        from the far end, so it is rejected here — ``fatal``, because no provider fallback can
        refine another provider's design either. :func:`app.design.resilient.provider_for_refine`
        is what keeps refines from arriving here in the first place.
        """
        ref = external_ref.strip()
        if "/" in ref:
            project, screen = ref.rsplit("/", 1)
            return _resource_id(project), screen
        if not _looks_like_stitch_id(ref):
            raise stitch_error(
                StitchErrorKind.fatal,
                f"Design reference {ref!r} was not produced by Stitch, so Stitch cannot refine it",
                hint=(
                    "This design came from a different provider (a fallback, or an import). "
                    "Refine it with that provider, or generate a new design with Stitch active."
                ),
            )
        if not fallback_project:
            raise stitch_error(StitchErrorKind.fatal, "This design has no Stitch project reference")
        return fallback_project, ref

    @staticmethod
    def _screen_parts(payload: Mapping[str, Any]) -> tuple[str, str, str, str]:
        """``(screen id, inline html, html url, image url)`` from any Stitch tool result."""
        screen = _screen_object(payload)
        screen_id = str(screen.get("id") or "") or _resource_id(screen.get("name"))
        if not screen_id:
            screen_id = _resource_id(_pluck(payload, _SCREEN_KEYS))

        inline, html_url = _file_parts(screen.get("htmlCode"))
        if not (inline or html_url):  # unknown shape — fall back to a generic key search
            html_url = str(_pluck(screen, _HTML_KEYS) or _pluck(payload, _HTML_KEYS) or "")

        _, image_url = _file_parts(screen.get("screenshot"))
        if not image_url:
            image_url = str(_pluck(screen, _IMAGE_KEYS) or _pluck(payload, _IMAGE_KEYS) or "")

        return screen_id, inline, html_url, image_url

    def _screen_args(self, project_id: str, screen_id: str) -> dict[str, Any]:
        """``get_screen`` wants the resource ``name``; the id pair is its deprecated form."""
        return {
            "name": f"projects/{project_id}/screens/{screen_id}",
            "projectId": project_id,
            "screenId": screen_id,
        }

    async def _hydrate(
        self, project_id: str, payload: Mapping[str, Any], headers: Headers
    ) -> StitchDesignPayload:
        """Turn a tool result into a design payload, fetching the screen and its HTML as needed.

        Generation is asynchronous: the tool can answer as soon as the screen exists, before its
        code does. So when the screen carries no HTML yet, re-read it until it does or
        ``STITCH_READY_TIMEOUT_S`` elapses — an empty design is worse than a slow one.
        """
        screen_id, inline, html_url, image_url = self._screen_parts(payload)
        last: Mapping[str, Any] = payload

        if screen_id and not (inline or html_url):
            deadline = time.monotonic() + float(get_config().get("stitch_ready_timeout_s"))
            while True:
                screen = await self._call_tool(
                    TOOL_GET_SCREEN, self._screen_args(project_id, screen_id), headers
                )
                last = screen
                _, inline, html_url, found_image = self._screen_parts(screen)
                image_url = image_url or found_image
                if inline or html_url or time.monotonic() >= deadline:
                    break
                logger.info("stitch: screen %s has no code yet; re-reading", screen_id)
                await self._sleep(SCREEN_POLL_INTERVAL_S)

        html = _decode_inline(inline) if inline else await self._resolve_html(html_url, headers)
        if not html.strip():
            # Never save a blank design: the stage would show "nothing to preview" and the build
            # would have no context. Fail as `contract` so the fallback chain serves something.
            raise stitch_error(
                StitchErrorKind.contract,
                f"Stitch returned no HTML for the generated screen{_summarize(last)}",
            )

        screens = await self._collect_screens(
            payload, headers, primary=(screen_id, html, image_url)
        )
        return StitchDesignPayload(
            external_ref=f"{project_id}/{screen_id}" if screen_id else "",
            html=html,
            # Stitch emits a single self-contained HTML document (utility classes inline), so
            # there is no separate stylesheet to carry.
            css="",
            preview_image=image_url or None,
            screens=screens,
            meta={"stitch_project": project_id, "stitch_screen": screen_id},
        )

    async def _collect_screens(
        self,
        payload: Mapping[str, Any],
        headers: Headers,
        *,
        primary: tuple[str, str, str],
    ) -> list[DesignScreen]:
        """Every screen in the response, primary first — for the UI's screen switcher.

        Only returned when there is genuinely more than one: a single-screen design leaves this
        empty so nothing downstream has to care. A secondary screen that fails to download is
        skipped rather than failing the whole design — the primary is what must be right.
        """
        objects = _screen_objects(payload)
        if len(objects) < 2:
            return []

        primary_id, primary_html, primary_image = primary
        screens: list[DesignScreen] = [
            DesignScreen(
                id=primary_id,
                title=_screen_title(_screen_object(payload), primary_id),
                html=primary_html,
                preview_image=primary_image or None,
            )
        ]
        for obj in objects[1:]:
            screen_id, inline, html_url, image_url = self._screen_parts(obj)
            try:
                html = (
                    _decode_inline(inline)
                    if inline
                    else await self._resolve_html(html_url, headers)
                )
            except ProviderError:
                logger.warning("stitch: could not load secondary screen %s", screen_id)
                continue
            if not html.strip():
                continue
            screens.append(
                DesignScreen(
                    id=screen_id,
                    title=_screen_title(obj, screen_id),
                    html=html,
                    preview_image=image_url or None,
                )
            )
        return screens if len(screens) > 1 else []

    async def _resolve_html(self, html_ref: Any, headers: Headers) -> str:
        """Stitch returns HTML as a download URL; literal markup is passed through unchanged.

        The URL is usually pre-signed and needs no credential, but a Google-hosted one may want the
        caller's — so an unauthenticated 401/403 is retried once with the request headers.
        """
        text = str(html_ref or "")
        if not text.startswith(("http://", "https://")):
            return text

        timeout = float(get_config().get("stitch_timeout_s"))
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(text)
                if response.status_code in (401, 403):
                    response = await client.get(text, headers=dict(headers))
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
            {
                "projectId": project_id,
                "selectedScreenIds": [screen_id],
                "prompt": instruction,
            },
            headers,
        )
        # An edit may answer with the edited screen, or with nothing but a status; either way the
        # screen id is the one we edited unless the server names a new one.
        if not _pluck(_screen_object(payload), _SCREEN_KEYS):
            payload = {**payload, "screenId": screen_id}
        return await self._hydrate(project_id, payload, headers)

    async def list_screens(self, *, headers: Headers) -> list[DesignScreenRef]:
        """Every screen in the Stitch project — the whole app, not just this turn's output."""
        project_id = str(get_config().get("stitch_project_id")).strip() or self._project_id
        if not project_id:
            return []  # nothing generated yet, so there is no project to enumerate
        project_id = _resource_id(project_id)

        payload = await self._call_tool(TOOL_LIST_SCREENS, {"projectId": project_id}, headers)
        raw = payload.get("screens") or payload.get("screenInstances") or []
        refs: list[DesignScreenRef] = []
        for entry in raw:
            if not isinstance(entry, Mapping):
                continue
            screen_id = str(entry.get("id") or "") or _resource_id(entry.get("name"))
            if not screen_id:
                continue
            _, image_url = _file_parts(entry.get("screenshot"))
            refs.append(
                DesignScreenRef(
                    ref=f"{project_id}/{screen_id}",
                    title=_screen_title(entry, screen_id),
                    preview_image=image_url or None,
                )
            )
        return refs

    async def fetch_code(self, external_ref: str, *, headers: Headers) -> StitchDesignPayload:
        project_id, screen_id = self._split_ref(external_ref, self._project_id)
        payload = await self._call_tool(
            TOOL_GET_SCREEN, self._screen_args(project_id, screen_id), headers
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
            list_screens=True,
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

    async def list_screens(self) -> list[DesignScreenRef]:
        """Every screen in the project (free — a metadata read, no generation spent)."""
        await self._require_credentials()
        return await self._invoke(lambda h: self._client.list_screens(headers=h))

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
            screens=payload.screens,
            # Provider-specific meta only — no credential ever lands here.
            meta={**payload.meta, **extra_meta},
        )

    async def _invoke(self, make_call: Callable[[Headers], Awaitable[T]]) -> T:
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
