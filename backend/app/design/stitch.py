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

**Conversational replies:** Stitch answers a broad brief with a scope proposal and a question
("shall I proceed with these five screens?") as often as it answers with a design. A proposal only
needs a yes, so :meth:`HttpStitchClient._generate` says it — bounded by
``STITCH_CLARIFY_MAX_REPLIES`` — rather than letting a usable brief collapse onto the figma/fake
fallback. A question that survives that budget wants a decision nobody here can make, so it is
raised as ``clarification`` and the design stage parks it for the user to answer (phase-19,
``app/design/questions.py``).

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

#: Free, side-effect-free reads: repeating one costs nothing and spends no generation quota.
_IDEMPOTENT_TOOLS: frozenset[str] = frozenset({"get_screen", "list_screens"})
#: Extra attempts for an idempotent read that came back with the flake below.
_FLAKE_RETRIES = 3
#: Stitch intermittently answers a byte-identical, schema-valid read with INVALID_ARGUMENT —
#: measured at roughly 1 call in 4 against the live endpoint, with the *same* arguments succeeding
#: on either side of it. It is a server-side flake, not a real contract violation, so idempotent
#: reads retry instead of collapsing the whole design onto the fallback provider. A genuinely
#: malformed request simply fails the same way a few hundred milliseconds later.
_FLAKY_ERROR_MARKER = "invalid argument"

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

    #: The Stitch project the screen lives in — carried up so the ForgeFlow project can record it
    #: and keep designing into the same one.
    workspace: str = ""
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

    async def text_to_ui(
        self,
        prompt: str,
        *,
        headers: Headers,
        workspace: str | None = None,
        title: str | None = None,
    ) -> StitchDesignPayload: ...

    async def refine(
        self, external_ref: str, instruction: str, *, headers: Headers
    ) -> StitchDesignPayload: ...

    async def fetch_code(self, external_ref: str, *, headers: Headers) -> StitchDesignPayload: ...

    async def list_screens(
        self, *, headers: Headers, workspace: str | None = None
    ) -> list[DesignScreenRef]: ...


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


def _is_flaky(error: ProviderError) -> bool:
    """Whether a failure is the intermittent INVALID_ARGUMENT (see ``_FLAKY_ERROR_MARKER``)."""
    detail = error.detail
    kind = detail.get("kind") if isinstance(detail, dict) else None
    if str(kind) != StitchErrorKind.contract:
        return False
    return _FLAKY_ERROR_MARKER in error.message.lower()


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


def _clarifying_question(payload: Mapping[str, Any]) -> str | None:
    """The text of a clarifying question, if Stitch answered conversationally instead of designing.

    Given an under-specified prompt, Stitch's model sometimes replies with plain text (e.g. "Who
    is this app for?") rather than generating a screen — every ``outputComponents`` entry carries
    a ``text`` and no ``design`` at all. That is a distinct, actionable case from a genuinely
    broken or unrecognised response shape, so it gets its own message instead of a raw JSON dump.
    """
    components = payload.get("outputComponents")
    if not isinstance(components, list) or not components:
        return None
    texts: list[str] = []
    for component in components:
        if not isinstance(component, Mapping) or "design" in component:
            return None  # not this pattern — a real (or differently-shaped) design is present
        text = component.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return " ".join(texts) or None


#: Keys a quick-reply list has been seen under. The Stitch web app offers the model's own
#: suggested replies as chips; the MCP payload is not documented to carry them, so this reads
#: defensively and simply finds nothing when they are absent.
_SUGGESTION_KEYS = ("suggestions", "suggestedReplies", "suggested_replies", "quickReplies")


def _suggested_replies(payload: Mapping[str, Any]) -> list[str]:
    """Quick replies offered alongside a clarifying question, if the response carried any."""
    found: list[str] = []
    sources: list[Any] = [payload, *(payload.get("outputComponents") or [])]
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        for key in _SUGGESTION_KEYS:
            for item in source.get(key) or []:
                text = item if isinstance(item, str) else ""
                if isinstance(item, Mapping):
                    text = str(item.get("text") or item.get("label") or "")
                text = text.strip()
                if text and text not in found:
                    found.append(text)
    return found[:6]


def _decision_directive() -> str:
    """The standing instruction that keeps a generate from turning into a conversation.

    ForgeFlow has no one at the keyboard when the design stage runs, and a question spends a round
    trip that produces no screens, so the model is told up front to decide rather than ask.
    """
    return (
        "Generate the screens now. Do not ask questions and do not wait for confirmation — "
        "make sensible design decisions for anything the brief leaves open."
    )


def _design_prompt(brief: str) -> str:
    """A generate/edit prompt: the caller's brief plus the decide-don't-ask directive."""
    return f"{brief.strip()}\n\n{_decision_directive()}"


def _confirmation_prompt(brief: str, question: str) -> str:
    """The answer to a clarifying question — "yes, proceed" — as a self-contained prompt.

    Stitch's ``generate_screen_from_text``/``edit_screens`` advertise no session handle (their
    schemas are ``projectId``/``prompt``/…), so a reply cannot be threaded onto the conversation
    that asked: it lands as a brand-new prompt and has to carry the proposal being confirmed *and*
    the original brief with it. The question is quoted back because it is where the model put the
    scope it proposed ("a cohesive 5-screen experience covering …") — dropping it would let the
    retry come back with a narrower design than the one just offered.
    """
    return (
        "Yes — proceed exactly as you proposed, covering every screen you listed, and "
        "generate the designs now.\n\n"
        f"Your proposal was:\n{question.strip()[:2000]}\n\n"
        f"The request it answered was:\n{brief.strip()}\n\n{_decision_directive()}"
    )


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
        self, body: dict[str, Any], headers: Headers, *, timeout: float | None = None
    ) -> tuple[dict[str, Any] | None, httpx.Response]:
        request_headers = {
            **headers,
            "Content-Type": "application/json",
            "Accept": MCP_ACCEPT,
        }
        if self._session_id:
            request_headers[SESSION_HEADER] = self._session_id

        if timeout is None:
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

    async def _rpc(
        self,
        method: str,
        params: dict[str, Any],
        headers: Headers,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        body = {"jsonrpc": "2.0", "id": self._next_id(), "method": method, "params": params}
        message, response = await self._post(body, headers, timeout=timeout)
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
        self,
        canonical: str,
        arguments: dict[str, Any],
        headers: Headers,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Invoke one MCP tool, retrying an idempotent read the server spuriously rejected.

        Only reads are retried: repeating a generate/edit would spend another generation from the
        monthly quota and add duplicate screens to the Stitch project, which is a far worse outcome
        than surfacing the error.
        """
        retries = _FLAKE_RETRIES if canonical in _IDEMPOTENT_TOOLS else 0
        for attempt in range(retries + 1):
            try:
                return await self._call_tool_once(canonical, arguments, headers, timeout=timeout)
            except ProviderError as exc:
                if attempt >= retries or not _is_flaky(exc):
                    raise
                logger.info(
                    "stitch: %s came back with a spurious INVALID_ARGUMENT; retrying (%d/%d)",
                    canonical,
                    attempt + 1,
                    retries,
                )
                await self._sleep(0.5 * 2**attempt)
        raise AssertionError("unreachable")  # pragma: no cover - the loop always returns or raises

    async def _call_tool_once(
        self,
        canonical: str,
        arguments: dict[str, Any],
        headers: Headers,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Invoke one MCP tool, re-handshaking once if the session was dropped server-side."""
        await self._ensure_session(headers)
        name = self._tool_name(canonical)
        params = {"name": name, "arguments": self._adapt(name, arguments)}
        try:
            result = await self._rpc("tools/call", params, headers, timeout=timeout)
        except ProviderError as exc:
            if _kind_of(exc) != StitchErrorKind.contract or not self._session_id:
                raise
            # An expired/unknown session reads as a plain 4xx; re-handshake once, then give up.
            self._session_id = None
            self._initialized = False
            await self._ensure_session(headers)
            name = self._tool_name(canonical)
            params = {"name": name, "arguments": self._adapt(name, arguments)}
            result = await self._rpc("tools/call", params, headers, timeout=timeout)

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

    async def _ensure_project(
        self,
        headers: Headers,
        workspace: str | None = None,
        title: str | None = None,
    ) -> str:
        """The Stitch project this call designs into.

        ``workspace`` is the caller's own Stitch project, carried forward from its previous design,
        so successive generates build up one coherent app. When it is absent this is that caller's
        **first** Stitch design and a fresh project is created.

        There is deliberately no per-instance reuse here. The registry holds a single
        :class:`StitchDesignProvider` for the whole process, so caching "the last project I made"
        on the client handed every ForgeFlow project the same Stitch project — a brand-new project
        opened showing the *previous* one's screens, before its owner had designed anything.
        ``STITCH_PROJECT_ID`` still pins one project globally, but that is now an explicit operator
        choice rather than the default behaviour.
        """
        if workspace:
            return _resource_id(workspace)
        configured = str(get_config().get("stitch_project_id")).strip()
        if configured:
            return _resource_id(configured)

        # `create_project` takes a `title` and answers with the project's resource `name`.
        payload = await self._call_tool(
            TOOL_CREATE_PROJECT, {"title": (title or "").strip() or "ForgeFlow"}, headers
        )
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
        self,
        project_id: str,
        payload: Mapping[str, Any],
        headers: Headers,
        *,
        brief: str = "",
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
            # would have no context. Which failure this is matters, though: a question Stitch is
            # waiting on goes to the *user* (`clarification`, below), while an unreadable response
            # is `contract` — recoverable, so figma/fake still get a shot at it, unlike `fatal`,
            # which skips the chain entirely (see resilient.py's `_NON_RECOVERABLE`).
            question = _clarifying_question(last)
            if question:
                # Not a failure and not something another provider can answer either: the model
                # wants a decision. `clarification` short-circuits the fallback chain so the design
                # stage can park it as a DesignQuestion and put it to the user, instead of quietly
                # designing something nobody asked for. Everything needed to resume the call rides
                # on the detail, because this turn produced no artifact to read it back from.
                raise stitch_error(
                    StitchErrorKind.clarification,
                    f"Stitch asked for more detail instead of generating a design: "
                    f'"{question[:300]}"',
                    detail={
                        "provider": PROVIDER_KEY,
                        "question": question,
                        "suggestions": _suggested_replies(last),
                        "prompt": brief,
                        "workspace": project_id,
                    },
                )
            raise stitch_error(
                StitchErrorKind.contract,
                f"Stitch returned no HTML for the generated screen{_summarize(last)}",
            )

        screens = await self._collect_screens(
            payload, headers, primary=(screen_id, html, image_url)
        )
        return StitchDesignPayload(
            workspace=project_id,
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

    # -- surviving a lost generation -----------------------------------------------------------

    @staticmethod
    def _generate_timeout() -> float:
        return float(get_config().get("stitch_generate_timeout_s"))

    async def _known_screen_ids(self, project_id: str, headers: Headers) -> set[str] | None:
        """The project's screen ids before a generation, or ``None`` if they can't be read.

        Snapshotting is what makes :meth:`_recover_generation` able to tell a screen this call
        produced from one that was already there. Best effort — a project we cannot enumerate
        simply forfeits recovery rather than failing the generation that has not happened yet.
        """
        try:
            payload = await self._call_tool(TOOL_LIST_SCREENS, {"projectId": project_id}, headers)
        except ProviderError:
            logger.warning("stitch: could not snapshot project %s before generating", project_id)
            return None
        ids: set[str] = set()
        for entry in payload.get("screens") or []:
            if not isinstance(entry, Mapping):
                continue
            screen_id = str(entry.get("id") or "") or _resource_id(entry.get("name"))
            if screen_id:
                ids.add(screen_id)
        return ids

    async def _recover_generation(
        self,
        project_id: str,
        known: set[str] | None,
        headers: Headers,
        error: ProviderError,
    ) -> dict[str, Any]:
        """Find screens a *seemingly* failed generation actually produced, or re-raise.

        Generation regularly outlives the HTTP call: a multi-screen prompt takes minutes, and a
        timeout (or a dropped connection) leaves us with no response even though Stitch has
        finished the work and already billed a generation against the monthly quota. Throwing that
        away means the user watches Stitch build their design and then sees ForgeFlow serve a fake
        one — so poll the project for screens that were not there beforehand and carry on with
        those. The result is re-shaped as a normal generate response so the usual parsing applies.

        Only a **lost response** is recovered — ``transient`` covers exactly that (timeout, dropped
        connection, 5xx), and it is the case where Stitch keeps working after we stop listening.
        Every other kind means the call never ran: a tool rejection, a bad credential, or a request
        we refused locally generated nothing, so waiting for screens that will never appear would
        just stall the stage before it falls back. Those re-raise untouched.
        """
        if known is None or _kind_of(error) != StitchErrorKind.transient:
            raise error

        deadline = time.monotonic() + float(get_config().get("stitch_ready_timeout_s"))
        while True:
            try:
                payload = await self._call_tool(
                    TOOL_LIST_SCREENS, {"projectId": project_id}, headers
                )
            except ProviderError:
                payload = {}
            fresh = [
                entry
                for entry in (payload.get("screens") or [])
                if isinstance(entry, Mapping)
                and (str(entry.get("id") or "") or _resource_id(entry.get("name"))) not in known
            ]
            if fresh:
                logger.info(
                    "stitch: recovered %d screen(s) from a generation that lost its response",
                    len(fresh),
                )
                return {"outputComponents": [{"design": {"screens": fresh}}]}
            if time.monotonic() >= deadline:
                raise error
            await self._sleep(SCREEN_POLL_INTERVAL_S)

    # -- answering a conversational reply --------------------------------------------------------

    async def _generate(
        self,
        canonical: str,
        arguments: dict[str, Any],
        headers: Headers,
        *,
        brief: str,
    ) -> dict[str, Any]:
        """Run a generate/edit, confirming a proposal rather than surrendering the design.

        Given a broad brief, Stitch's model may answer conversationally — a scope proposal plus
        "shall I proceed?" — with no screen attached (:func:`_clarifying_question`). Nobody
        downstream can answer that: the design stage runs unattended, so the reply used to end as a
        ``contract`` error and the design fell through to the figma/fake chain, which is how a
        perfectly good brief came back as a placeholder design.

        The confirmation is therefore sent from here, bounded by ``STITCH_CLARIFY_MAX_REPLIES``
        (each one is another tool call, so the budget is deliberately small). An exhausted budget
        returns the last conversational payload untouched and :meth:`_hydrate` raises the same
        actionable "asked for more detail" error as before, so the fallback chain still works.
        """
        payload = await self._call_tool(
            canonical, arguments, headers, timeout=self._generate_timeout()
        )
        budget = max(0, int(get_config().get("stitch_clarify_max_replies")))
        for attempt in range(budget):
            question = _clarifying_question(payload)
            if question is None:
                break
            logger.info(
                "stitch: %s asked a question instead of designing; confirming (%d/%d)",
                canonical,
                attempt + 1,
                budget,
            )
            payload = await self._call_tool(
                canonical,
                {**arguments, "prompt": _confirmation_prompt(brief, question)},
                headers,
                timeout=self._generate_timeout(),
            )
        return payload

    # -- the StitchClient Protocol -------------------------------------------------------------

    async def text_to_ui(
        self,
        prompt: str,
        *,
        headers: Headers,
        workspace: str | None = None,
        title: str | None = None,
    ) -> StitchDesignPayload:
        project_id = await self._ensure_project(headers, workspace, title)
        arguments: dict[str, Any] = {"projectId": project_id, "prompt": _design_prompt(prompt)}
        model_id = str(get_config().get("stitch_model_id")).strip()
        if model_id:
            arguments["modelId"] = model_id
        known = await self._known_screen_ids(project_id, headers)
        try:
            payload = await self._generate(
                TOOL_GENERATE_FROM_TEXT, arguments, headers, brief=prompt
            )
        except ProviderError as exc:
            payload = await self._recover_generation(project_id, known, headers, exc)
        return await self._hydrate(project_id, payload, headers, brief=prompt)

    async def refine(
        self, external_ref: str, instruction: str, *, headers: Headers
    ) -> StitchDesignPayload:
        project_id, screen_id = self._split_ref(external_ref, self._project_id)
        known = await self._known_screen_ids(project_id, headers)
        try:
            payload = await self._generate(
                TOOL_EDIT_SCREENS,
                {
                    "projectId": project_id,
                    "selectedScreenIds": [screen_id],
                    "prompt": _design_prompt(instruction),
                },
                headers,
                brief=instruction,
            )
        except ProviderError as exc:
            # An edit is as slow as a generate and likewise adds screens to the project, so the
            # same recovery applies when its response is lost.
            payload = await self._recover_generation(project_id, known, headers, exc)
        # An edit may answer with the edited screen, or with nothing but a status; either way the
        # screen id is the one we edited unless the server names a new one.
        if not _pluck(_screen_object(payload), _SCREEN_KEYS):
            payload = {**payload, "screenId": screen_id}
        return await self._hydrate(project_id, payload, headers, brief=instruction)

    async def list_screens(
        self, *, headers: Headers, workspace: str | None = None
    ) -> list[DesignScreenRef]:
        """Every screen in ``workspace`` — the whole app, not just this turn's output.

        Without a workspace there is nothing safe to list: falling back to the client's last
        project (or the global pin) is what showed one ForgeFlow project the screens of another.
        """
        project_id = workspace or str(get_config().get("stitch_project_id")).strip()
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

    async def generate_from_text(
        self,
        prompt: str,
        *,
        workspace: str | None = None,
        workspace_title: str | None = None,
    ) -> DesignResult:
        await self._require_credentials()
        await self._quota.check_available()
        payload = await self._invoke(
            lambda h: self._client.text_to_ui(
                prompt, headers=h, workspace=workspace, title=workspace_title
            ),
            spends_quota=True,
        )
        await self._quota.record_usage()
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
        raise stitch_error(
            StitchErrorKind.unsupported,
            "Stitch's API generates from a text prompt only; describe the UI in text, "
            "or switch the design provider to 'fake' to use screenshots",
        )

    async def refine(self, design_ref: str, instruction: str) -> DesignResult:
        await self._require_credentials()
        await self._quota.check_available()
        payload = await self._invoke(
            lambda h: self._client.refine(design_ref, instruction, headers=h), spends_quota=True
        )
        await self._quota.record_usage()
        return self._to_result(payload, {"source": "refine", "refined_from": design_ref})

    async def fetch_code(self, design_ref: str) -> DesignCode:
        # Metadata read — does not consume a generation.
        await self._require_credentials()
        payload = await self._invoke(lambda h: self._client.fetch_code(design_ref, headers=h))
        return DesignCode(html=payload.html, css=payload.css, assets=payload.assets)

    async def list_screens(self, workspace: str | None = None) -> list[DesignScreenRef]:
        """Every screen in ``workspace`` (free — a metadata read, no generation spent)."""
        await self._require_credentials()
        return await self._invoke(
            lambda h: self._client.list_screens(headers=h, workspace=workspace)
        )

    # -- internals ------------------------------------------------------------------------

    async def _require_credentials(self) -> None:
        if not await stitch_credentials_present():
            raise stitch_error(StitchErrorKind.auth, "Stitch credentials are not configured")

    def _to_result(self, payload: StitchDesignPayload, extra_meta: dict[str, Any]) -> DesignResult:
        return DesignResult(
            provider=self.key,
            workspace=payload.workspace,
            external_ref=payload.external_ref,
            html=payload.html,
            css=payload.css,
            preview_image=payload.preview_image,
            screens=payload.screens,
            # Provider-specific meta only — no credential ever lands here.
            meta={**payload.meta, **extra_meta},
        )

    async def _invoke(
        self, make_call: Callable[[Headers], Awaitable[T]], *, spends_quota: bool = False
    ) -> T:
        """Run a transport call with transparent credential refresh + transient backoff/retry.

        ``spends_quota`` disables the transient retry. Re-running a generate/edit that timed out
        does not re-do the *same* work: Stitch has very likely already produced the screens, so a
        retry spends a second generation from the monthly quota and leaves duplicate screens in the
        project. Those calls recover in the transport instead (``_recover_generation``), which
        reuses the work already paid for rather than ordering it twice.
        """
        max_retries = 0 if spends_quota else int(get_config().get("stitch_max_retries"))
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
