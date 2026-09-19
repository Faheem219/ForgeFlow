"""The real Stitch MCP transport: JSON-RPC 2.0 over HTTP to ``stitch.googleapis.com/mcp``.

These exercise :class:`HttpStitchClient` against a scripted ASGI-free HTTP mock, so the protocol
itself is covered: the initialize handshake, the ``tools/call`` envelope, SSE framing, the session
header, tool-error handling, project bootstrapping, and the HTML download that turns Stitch's
download URL into the markup ForgeFlow stores.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from app.core.config import reset_config
from app.core.errors import ProviderError
from app.design.stitch import HttpStitchClient
from app.design.stitch_auth import StitchAuth

MCP_URL = "https://stitch.example/mcp"
HTML_URL = "https://storage.example/screen.html"
IMAGE_URL = "https://storage.example/screen.png"

#: The tool catalog the real endpoint advertises (mirrored from the official @google/stitch-sdk
#: generated tool definitions), so these tests fail if we drift from the shipped contract.
DEFAULT_TOOLS: dict[str, dict[str, Any]] = {
    "create_project": {"properties": {"title": {"type": "string"}}},
    "generate_screen_from_text": {
        "properties": {
            "projectId": {},
            "prompt": {},
            "designSystem": {},
            "deviceType": {},
            "modelId": {},
        },
        "required": ["projectId", "prompt"],
    },
    "edit_screens": {
        "properties": {"projectId": {}, "selectedScreenIds": {}, "prompt": {}, "deviceType": {}},
        "required": ["projectId", "selectedScreenIds", "prompt"],
    },
    "get_screen": {
        "properties": {"name": {}, "projectId": {}, "screenId": {}},
        "required": ["name", "projectId", "screenId"],
    },
    "list_screens": {"properties": {"projectId": {}}, "required": ["projectId"]},
}


def screen_doc(*, with_code: bool = True) -> dict[str, Any]:
    """A Stitch ``ScreenInput``: ids plus ``htmlCode``/``screenshot`` File objects."""
    screen: dict[str, Any] = {"id": "screen-1", "name": "projects/proj-9/screens/screen-1"}
    if with_code:
        screen["htmlCode"] = {"downloadUrl": HTML_URL, "mimeType": "text/html"}
        screen["screenshot"] = {"downloadUrl": IMAGE_URL, "mimeType": "image/png"}
    return screen


def session_doc(
    screen: dict[str, Any], extra: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """A generate/edit response: the screens are buried in ``outputComponents[].design.screens``."""
    return {
        "projectId": "proj-9",
        "sessionId": "sess-1",
        "outputComponents": [
            {"text": "Here you go"},
            {"design": {"screens": [screen, *(extra or [])]}},
        ],
    }


class McpServer:
    """A scripted Stitch MCP endpoint: records requests, answers by tool name.

    ``tools`` is the advertised catalog (name → inputSchema); a server that declares different
    names or argument spellings is how the discovery behaviour is exercised.
    """

    def __init__(
        self,
        *,
        sse: bool = False,
        session: str | None = "sess-1",
        tools: dict[str, dict[str, Any]] | None = None,
        strict: bool = False,
    ) -> None:
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self.sse = sse
        self.session = session
        self.tools = DEFAULT_TOOLS if tools is None else tools
        #: When set, generation answers with a screen that has no code yet (async generation).
        self.generate_without_code = False
        #: Extra screens returned alongside the primary one (a multi-screen design).
        self.extra_screens: list[dict[str, Any]] = []
        #: What ``list_screens`` reports for the project.
        self.project_screens: list[dict[str, Any]] = []
        #: When set, an argument the schema doesn't declare is rejected — like the real API.
        self.strict = strict
        #: Override the whole ``get_screen`` tool result, to script an unusual response shape.
        self.screen_result: dict[str, Any] | None = None
        #: Override the whole ``generate_screen_from_text`` result — a conversational, non-design
        #: reply (Stitch asking a clarifying question) rather than the usual screen_doc/session_doc.
        self.generate_result: dict[str, Any] | None = None
        #: Conversational replies answered *before* the design, one per generate/edit call — how
        #: the live API behaves when it proposes a scope and waits for a yes.
        self.clarifications: list[dict[str, Any]] = []
        #: How many ``get_screen`` reads answer without code before one carries it (async gen).
        self.screen_ready_after = 0
        self._screen_reads = 0
        #: When set, the generate/edit call never answers — the real 76s-generation timeout.
        self.generate_times_out = False
        #: Screens the project reports *after* a generation was attempted (recovery source).
        self.screens_after_generate: list[dict[str, Any]] | None = None
        #: tool name → how many times it answers INVALID_ARGUMENT before working (server flake).
        self.flaky_tools: dict[str, int] = {}
        self._generate_seen = False

    # -- wiring ---------------------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == HTML_URL:
            return httpx.Response(200, text="<main>generated</main>")
        if request.method == "GET":  # any other download URL is dead
            return httpx.Response(404)

        body = json.loads(request.content)
        self.requests.append(body)
        self.headers.append(request.headers)

        if body.get("method") == "notifications/initialized":
            return httpx.Response(202)
        if body.get("method") == "initialize":
            headers = {"Mcp-Session-Id": self.session} if self.session else {}
            return self._rpc(body["id"], {"protocolVersion": "2024-11-05"}, headers)
        if body.get("method") == "tools/list":
            tools = [{"name": n, "inputSchema": s} for n, s in self.tools.items()]
            return self._rpc(body["id"], {"tools": tools})
        if body.get("method") == "tools/call":
            name = body["params"]["name"]
            args = body["params"].get("arguments", {})
            if self.strict:
                schema = self.tools.get(name) or {}
                declared = set(schema.get("properties") or {})
                required = set(schema.get("required") or [])
                # Exactly what Google does: an unknown field OR a missing required one is
                # INVALID_ARGUMENT.
                if (set(args) - declared) or (required - set(args)):
                    return self._rpc(
                        body["id"],
                        {
                            "isError": True,
                            "content": [
                                {"type": "text", "text": "Request contains an invalid argument."}
                            ],
                        },
                    )
            if self.flaky_tools.get(name, 0) > 0:
                # Exactly what the live endpoint intermittently does to a perfectly valid call.
                self.flaky_tools[name] -= 1
                return self._rpc(
                    body["id"],
                    {
                        "isError": True,
                        "content": [
                            {"type": "text", "text": "Request contains an invalid argument."}
                        ],
                    },
                )
            if name in ("generate_screen_from_text", "edit_screens"):
                self._generate_seen = True
                if self.generate_times_out:
                    raise httpx.ReadTimeout("generation outlived the request")
            if name == "get_screen" and self.screen_result is not None:
                return self._rpc(body["id"], self.screen_result)
            return self._rpc(body["id"], {"content": [self._tool(name, args)]})
        return httpx.Response(400, json={"error": "unexpected"})

    def _tool(self, name: str, args: dict[str, Any]) -> dict[str, str]:
        if name in ("create_project", "createProject"):
            # The real response is the project resource, keyed by `name`.
            return self._text({"name": "projects/proj-9", "title": "ForgeFlow"})
        if name in ("generate_screen_from_text", "generateScreenFromText"):
            if self.clarifications:
                return self._text(self.clarifications.pop(0))
            if self.generate_result is not None:
                return self._text(self.generate_result)
            primary = screen_doc(with_code=not self.generate_without_code)
            return self._text(session_doc(primary, self.extra_screens))
        if name == "edit_screens":
            if self.clarifications:
                return self._text(self.clarifications.pop(0))
            return self._text(session_doc(screen_doc(), self.extra_screens))
        if name == "list_screens":
            if self._generate_seen and self.screens_after_generate is not None:
                return self._text({"screens": self.screens_after_generate})
            return self._text({"screens": self.project_screens})
        if name == "get_screen":
            self._screen_reads += 1
            ready = self._screen_reads > self.screen_ready_after
            return self._text(screen_doc(with_code=ready))
        return self._text({})

    @staticmethod
    def _text(payload: dict[str, Any]) -> dict[str, str]:
        return {"type": "text", "text": json.dumps(payload)}

    def _rpc(
        self, rpc_id: int, result: dict[str, Any], headers: dict[str, str] | None = None
    ) -> httpx.Response:
        message = {"jsonrpc": "2.0", "id": rpc_id, "result": result}
        if not self.sse:
            return httpx.Response(200, json=message, headers=headers)
        return httpx.Response(
            200,
            text=f"event: message\ndata: {json.dumps(message)}\n\n",
            headers={**(headers or {}), "content-type": "text/event-stream"},
        )

    # -- assertions helpers ---------------------------------------------------------------

    def tool_calls(self) -> list[str]:
        return [r["params"]["name"] for r in self.requests if r.get("method") == "tools/call"]

    def arguments_for(self, tool: str) -> dict[str, Any]:
        for r in self.requests:
            if r.get("method") == "tools/call" and r["params"]["name"] == tool:
                return dict(r["params"].get("arguments", {}))
        raise AssertionError(f"{tool} was never called")


@pytest.fixture(autouse=True)
def _mcp_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STITCH_MCP_URL", MCP_URL)
    reset_config()


@pytest.fixture
def patched_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route every httpx.AsyncClient in the transport at the scripted server."""
    original = httpx.AsyncClient

    def factory(server: McpServer) -> None:
        def build(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = server.transport()
            return original(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", build)

    return factory  # type: ignore[return-value]


async def _headers() -> dict[str, str]:
    return await StitchAuth(token_fetcher=lambda: _token()).headers()


async def _token() -> tuple[str, float]:
    return "tok", 3600.0


# ---------------------------------------------------------------- protocol


async def test_a_generation_performs_the_handshake_then_calls_the_tool(patched_httpx: Any) -> None:
    server = McpServer()
    patched_httpx(server)

    payload = await HttpStitchClient().text_to_ui("a todo app", headers=await _headers())

    methods = [r["method"] for r in server.requests]
    assert methods[:3] == ["initialize", "notifications/initialized", "tools/list"]
    # The project's screens are snapshotted first so a generation whose response is lost can
    # still be recovered; the generate response then carries the files, so no extra read follows.
    assert server.tool_calls() == ["create_project", "list_screens", "generate_screen_from_text"]

    body = server.requests[0]
    assert body["jsonrpc"] == "2.0" and "id" in body  # a real JSON-RPC envelope, not a bare call
    assert server.arguments_for("create_project") == {"title": "ForgeFlow"}
    generate = server.arguments_for("generate_screen_from_text")
    assert set(generate) == {"projectId", "prompt"}
    assert generate["projectId"] == "proj-9"
    # The caller's brief leads the prompt verbatim; the standing "decide, don't ask" directive
    # (which stops Stitch answering with a question instead of a design) follows it.
    assert generate["prompt"].startswith("a todo app\n\n")
    assert "Do not ask questions" in generate["prompt"]
    # The screen is dug out of outputComponents[].design.screens, and its htmlCode.downloadUrl
    # fetched into the markup the preview renders.
    assert payload.html == "<main>generated</main>"
    assert payload.external_ref == "proj-9/screen-1"
    assert payload.preview_image == IMAGE_URL


async def test_the_session_header_is_echoed_on_every_later_call(patched_httpx: Any) -> None:
    server = McpServer(session="sess-42")
    patched_httpx(server)

    await HttpStitchClient().text_to_ui("x", headers=await _headers())

    assert server.headers[0].get("mcp-session-id") is None  # nothing to send yet
    assert all(h.get("mcp-session-id") == "sess-42" for h in server.headers[1:])


async def test_an_sse_framed_response_is_understood(patched_httpx: Any) -> None:
    server = McpServer(sse=True)
    patched_httpx(server)

    payload = await HttpStitchClient().text_to_ui("x", headers=await _headers())
    assert payload.external_ref == "proj-9/screen-1"


async def test_the_handshake_runs_once_per_client(patched_httpx: Any) -> None:
    server = McpServer()
    patched_httpx(server)
    client = HttpStitchClient()

    await client.text_to_ui("one", headers=await _headers())
    await client.text_to_ui("two", headers=await _headers())

    assert [r["method"] for r in server.requests].count("initialize") == 1
    assert [r["method"] for r in server.requests].count("tools/list") == 1
    # ...and the project is created once, then reused.
    # …but each generate makes its own Stitch project: with no workspace passed, every call is a
    # different ForgeFlow project's first design, and reusing one container across them is exactly
    # the leak that showed a new project someone else's screens.
    assert server.tool_calls().count("create_project") == 2


# ---------------------------------------------------------------- schema discovery


async def test_arguments_are_renamed_to_whatever_the_schema_declares(patched_httpx: Any) -> None:
    """The server is the authority on argument spelling — a snake_case API gets snake_case."""
    server = McpServer(
        tools={
            "create_project": {"properties": {"title": {}}},
            "generate_screen_from_text": {"properties": {"project_id": {}, "text": {}}},
            "get_screen": {"properties": {"project_id": {}, "screen_id": {}}},
        },
        strict=True,
    )
    patched_httpx(server)

    await HttpStitchClient().text_to_ui("a todo app", headers=await _headers())

    assert server.arguments_for("create_project") == {"title": "ForgeFlow"}
    generate = server.arguments_for("generate_screen_from_text")
    assert set(generate) == {"project_id", "text"}
    assert generate["project_id"] == "proj-9"
    assert generate["text"].startswith("a todo app")


async def test_an_argument_the_tool_does_not_declare_is_dropped(
    patched_httpx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live failure this fixes: sending a field Google doesn't know → INVALID_ARGUMENT."""
    monkeypatch.setenv("STITCH_MODEL_ID", "GEMINI_3_PRO")
    reset_config()
    server = McpServer(strict=True)  # its generate schema has no `modelId`… it does, so remove it
    server.tools = {
        **DEFAULT_TOOLS,
        "generate_screen_from_text": {"properties": {"projectId": {}, "prompt": {}}},
    }
    patched_httpx(server)

    await HttpStitchClient().text_to_ui("x", headers=await _headers())

    assert "modelId" not in server.arguments_for("generate_screen_from_text")


async def test_a_renamed_tool_is_still_found(patched_httpx: Any) -> None:
    server = McpServer(
        tools={
            "createProject": {"properties": {"name": {}}},
            "generateScreenFromText": {"properties": {"projectId": {}, "prompt": {}}},
            "get_screen": {"properties": {"projectId": {}, "screenId": {}}},
        }
    )
    patched_httpx(server)

    payload = await HttpStitchClient().text_to_ui("x", headers=await _headers())

    assert server.tool_calls()[0] == "createProject"
    assert "generateScreenFromText" in server.tool_calls()
    assert payload.external_ref == "proj-9/screen-1"


async def test_a_server_without_tools_list_still_works(patched_httpx: Any) -> None:
    """Discovery is best-effort: no listing → send the default names, don't fail the design."""
    server = McpServer(tools={})
    patched_httpx(server)

    payload = await HttpStitchClient().text_to_ui("x", headers=await _headers())

    assert server.tool_calls()[0] == "create_project"
    assert payload.external_ref == "proj-9/screen-1"


async def test_a_rejected_argument_names_the_tool_and_its_schema(patched_httpx: Any) -> None:
    """The message an operator sees must make the mismatch obvious, not just 'invalid argument'."""
    # A required field none of our aliases map onto — the case the aliases cannot repair.
    server = McpServer(
        tools={
            **DEFAULT_TOOLS,
            "create_project": {
                "properties": {"somethingElse": {}},
                "required": ["somethingElse"],
            },
        },
        strict=True,
    )
    patched_httpx(server)

    with pytest.raises(ProviderError) as exc:
        await HttpStitchClient().text_to_ui("x", headers=await _headers())

    assert exc.value.detail == {"kind": "contract"}
    assert "create_project" in exc.value.message
    assert "somethingElse" in exc.value.message  # what the tool actually accepts


# ---------------------------------------------------------------- response shapes
#
# The live failure these cover: a design generated fine in Stitch but arrived with empty HTML, so
# the stage saved a blank artifact and the preview said "nothing to preview".


async def _generate_with(server: McpServer, patched: Any) -> Any:
    server.generate_without_code = True  # force the get_screen read these cases script
    patched(server)
    return await HttpStitchClient(sleeper=_no_sleep).text_to_ui("x", headers=await _headers())


async def _no_sleep(_seconds: float) -> None:
    return None


async def test_html_delivered_as_a_resource_block_is_read(patched_httpx: Any) -> None:
    """MCP tools may answer with a resource, not a JSON text block — that is not an empty design."""
    server = McpServer()
    server.screen_result = {
        "content": [
            {
                "type": "resource",
                "resource": {
                    "uri": "stitch://s1",
                    "mimeType": "text/html",
                    "text": "<div>hi</div>",
                },
            }
        ]
    }
    payload = await _generate_with(server, patched_httpx)
    assert payload.html == "<div>hi</div>"


async def test_a_resource_uri_is_downloaded(patched_httpx: Any) -> None:
    server = McpServer()
    server.screen_result = {
        "content": [{"type": "resource", "resource": {"uri": HTML_URL, "mimeType": "text/html"}}]
    }
    payload = await _generate_with(server, patched_httpx)
    assert payload.html == "<main>generated</main>"


async def test_raw_html_in_a_text_block_is_read(patched_httpx: Any) -> None:
    """Not every text block is JSON; markup is the payload itself."""
    server = McpServer()
    server.screen_result = {"content": [{"type": "text", "text": "<html><body>raw</body></html>"}]}
    payload = await _generate_with(server, patched_httpx)
    assert "raw" in payload.html


async def test_structured_content_is_preferred(patched_httpx: Any) -> None:
    server = McpServer()
    server.screen_result = {
        "content": [{"type": "text", "text": json.dumps({"htmlUrl": "https://wrong.example/x"})}],
        "structuredContent": {"html": "<p>structured</p>"},
    }
    payload = await _generate_with(server, patched_httpx)
    assert payload.html == "<p>structured</p>"


async def test_a_screen_without_code_yet_is_polled_until_it_has_some(patched_httpx: Any) -> None:
    """Generation is async: answering before the code exists must not yield a blank design."""
    server = McpServer()
    server.screen_ready_after = 2  # the first two get_screen calls carry no html
    payload = await _generate_with(server, patched_httpx)

    assert server.tool_calls().count("get_screen") == 3
    assert payload.html == "<main>generated</main>"


async def test_every_screen_in_a_response_is_captured(patched_httpx: Any) -> None:
    """One generate can return a whole flow (light + dark); keeping only the first would discard
    designs the user can see in their Stitch project."""
    server = McpServer()
    dark = {
        "id": "screen-2",
        "title": "Personal Todo App (Dark)",
        "htmlCode": {"fileContentBase64": base64.b64encode(b"<p>dark</p>").decode("ascii")},
        "screenshot": {"downloadUrl": IMAGE_URL},
    }
    server.extra_screens = [dark]
    patched_httpx(server)

    payload = await HttpStitchClient(sleeper=_no_sleep).text_to_ui("x", headers=await _headers())

    assert payload.html == "<main>generated</main>"  # the primary is unchanged
    assert [s.id for s in payload.screens] == ["screen-1", "screen-2"]
    assert payload.screens[1].title == "Personal Todo App (Dark)"
    # An inline base64 body is decoded rather than downloaded.
    assert payload.screens[1].html == "<p>dark</p>"


async def test_a_single_screen_design_carries_no_screen_list(patched_httpx: Any) -> None:
    """`screens` feeds the switcher; a lone screen leaves it empty so nothing downstream cares."""
    server = McpServer()
    patched_httpx(server)

    payload = await HttpStitchClient(sleeper=_no_sleep).text_to_ui("x", headers=await _headers())
    assert payload.screens == []


async def test_a_broken_secondary_screen_does_not_fail_the_design(patched_httpx: Any) -> None:
    server = McpServer()
    server.extra_screens = [
        {"id": "screen-2", "htmlCode": {"downloadUrl": "https://dead.example/x"}}
    ]
    patched_httpx(server)

    payload = await HttpStitchClient(sleeper=_no_sleep).text_to_ui("x", headers=await _headers())

    assert payload.html == "<main>generated</main>"  # the primary still lands
    assert payload.screens == []  # …and the unusable extra is dropped, not fatal


async def test_list_screens_enumerates_the_whole_project(patched_httpx: Any) -> None:
    """An app is a set of screens built over many turns; the picker needs all of them."""
    server = McpServer()
    server.project_screens = [
        {"id": "landing", "title": "Landing", "screenshot": {"downloadUrl": IMAGE_URL}},
        {"name": "projects/proj-9/screens/login", "title": "Login"},
    ]
    patched_httpx(server)
    client = HttpStitchClient(sleeper=_no_sleep)
    await client.text_to_ui("x", headers=await _headers())  # establishes the project

    refs = await client.list_screens(headers=await _headers(), workspace="proj-9")

    assert [r.ref for r in refs] == ["proj-9/landing", "proj-9/login"]
    assert [r.title for r in refs] == ["Landing", "Login"]
    assert refs[0].preview_image == IMAGE_URL
    assert server.arguments_for("list_screens") == {"projectId": "proj-9"}


async def test_list_screens_is_empty_without_a_workspace(patched_httpx: Any) -> None:
    """No container yet → nothing to enumerate, and definitely not an error.

    This is also the guard against cross-project leakage: a shared client that fell back to "the
    last project I made" is what showed a brand-new ForgeFlow project another project's screens.
    """
    server = McpServer()
    patched_httpx(server)
    client = HttpStitchClient(sleeper=_no_sleep)
    await client.text_to_ui("x", headers=await _headers())  # this client HAS made a project
    before = len(server.tool_calls())

    assert await client.list_screens(headers=await _headers()) == []
    assert server.tool_calls()[before:] == []  # not even a call is made


async def test_a_screen_that_never_produces_html_fails_loudly(
    patched_httpx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Better a clear error that falls back than a saved design with nothing to preview."""
    monkeypatch.setenv("STITCH_READY_TIMEOUT_S", "5")
    reset_config()
    server = McpServer()
    server.generate_without_code = True
    server.screen_result = {
        "content": [{"type": "text", "text": json.dumps({"status": "PENDING"})}]
    }
    patched_httpx(server)

    with pytest.raises(ProviderError) as exc:
        await HttpStitchClient(sleeper=_no_sleep).text_to_ui("x", headers=await _headers())

    assert exc.value.detail == {"kind": "contract"}  # recoverable → the stage falls back
    assert "no HTML" in exc.value.message
    assert "PENDING" in exc.value.message  # the raw payload is quoted, so the shape is diagnosable


def _proposal() -> dict[str, Any]:
    """What Stitch actually answered a real ForgeFlow brief with: a scope proposal, no design."""
    return {
        "projectId": "proj-9",
        "sessionId": "sess-1",
        "outputComponents": [
            {
                "text": (
                    "Because this is a full multi-user platform, I propose a cohesive 5-screen "
                    "experience covering auth, publishing, discussion and moderation. "
                    "Would you like me to proceed?"
                )
            }
        ],
    }


async def test_a_proposal_is_confirmed_so_the_design_still_comes_from_stitch(
    patched_httpx: Any,
) -> None:
    """The live failure this fixes: a broad brief made Stitch answer with a five-screen proposal
    and "shall I proceed?", which ended the generation as a ``contract`` error and dropped the
    whole design onto the fake fallback. Nobody is at the keyboard to say yes, so we say it."""
    server = McpServer()
    server.clarifications = [_proposal()]
    patched_httpx(server)

    payload = await HttpStitchClient(sleeper=_no_sleep).text_to_ui(
        "a blog and discussion site", headers=await _headers()
    )

    assert payload.html == "<main>generated</main>"  # a real Stitch design, not a fallback
    assert payload.external_ref == "proj-9/screen-1"
    generates = [
        r["params"]["arguments"]["prompt"]
        for r in server.requests
        if r.get("method") == "tools/call" and r["params"]["name"] == "generate_screen_from_text"
    ]
    assert len(generates) == 2  # the ask, then the confirmation
    # The tool takes no session handle, so the confirmation cannot be threaded onto the
    # conversation that asked: it has to carry the proposed scope *and* the original brief.
    assert "5-screen" in generates[1]
    assert "a blog and discussion site" in generates[1]
    assert "proceed" in generates[1].lower()


async def test_an_edit_that_asks_a_question_is_confirmed_too(patched_httpx: Any) -> None:
    """A refine goes through the same conversational model, so it gets the same answer."""
    server = McpServer()
    server.clarifications = [_proposal()]
    patched_httpx(server)

    payload = await HttpStitchClient(sleeper=_no_sleep).refine(
        "proj-9/screen-1", "make it dark", headers=await _headers()
    )

    assert payload.html == "<main>generated</main>"
    edits = [
        r["params"]["arguments"]["prompt"]
        for r in server.requests
        if r.get("method") == "tools/call" and r["params"]["name"] == "edit_screens"
    ]
    assert len(edits) == 2
    assert "make it dark" in edits[1]


async def test_confirming_is_bounded_and_configurable(
    patched_httpx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model that only ever wants to talk must not loop: the budget caps the round trips, and
    the stage then falls back rather than spending the Stitch quota on a conversation."""
    monkeypatch.setenv("STITCH_CLARIFY_MAX_REPLIES", "1")
    reset_config()
    server = McpServer()
    server.generate_result = _proposal()  # never answers with a design
    patched_httpx(server)

    with pytest.raises(ProviderError) as exc:
        await HttpStitchClient(sleeper=_no_sleep).text_to_ui("x", headers=await _headers())

    assert exc.value.detail is not None
    assert exc.value.detail["kind"] == "clarification"  # → the user is asked, not another provider
    assert server.tool_calls().count("generate_screen_from_text") == 2  # ask + one confirmation


async def test_confirming_can_be_turned_off(
    patched_httpx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STITCH_CLARIFY_MAX_REPLIES", "0")
    reset_config()
    server = McpServer()
    server.generate_result = _proposal()
    patched_httpx(server)

    with pytest.raises(ProviderError):
        await HttpStitchClient(sleeper=_no_sleep).text_to_ui("x", headers=await _headers())

    assert server.tool_calls().count("generate_screen_from_text") == 1  # asked once, gave up


async def test_a_question_that_survives_confirmation_is_raised_for_the_user(
    patched_httpx: Any,
) -> None:
    """A question a blind "yes" cannot answer — *who* is this for — is not a provider failure and
    must not be resolved by designing something else: it is classified as `clarification`, which
    stops the fallback chain, and carries everything the design stage needs to put it to the user
    and resume the call with their answer."""
    server = McpServer()
    server.generate_result = {
        "projectId": "proj-9",
        "sessionId": "sess-1",
        "outputComponents": [
            {"text": "I'd love to help you design your ToDo app. Who is it for?"},
        ],
        "suggestions": ["Busy professionals", {"text": "Students"}],
    }
    patched_httpx(server)

    with pytest.raises(ProviderError) as exc:
        await HttpStitchClient(sleeper=_no_sleep).text_to_ui("todo app", headers=await _headers())

    assert exc.value.detail is not None
    assert exc.value.detail["kind"] == "clarification"
    assert "asked for more detail" in exc.value.message
    assert "Who is it for?" in exc.value.message  # Stitch's own question survives, not just a dump
    assert "no HTML" not in exc.value.message  # the specific message wins over the generic one
    detail = exc.value.detail["detail"]
    assert detail["provider"] == "stitch"
    assert detail["question"] == "I'd love to help you design your ToDo app. Who is it for?"
    # Quick replies become one-click answers in the UI; both shapes the payload has been seen in.
    assert detail["suggestions"] == ["Busy professionals", "Students"]
    # The brief and the container the asking call opened — so the answer resumes the same call in
    # the same Stitch project rather than starting over beside it.
    assert detail["prompt"] == "todo app"
    assert detail["workspace"] == "proj-9"
    # Reached only once the confirmation (default budget: 1) has been answered with more talk.
    assert server.tool_calls().count("generate_screen_from_text") == 2


# ---------------------------------------------------------------- auth


async def test_an_api_key_is_sent_as_the_google_header(
    patched_httpx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STITCH_API_KEY", "key-123")
    monkeypatch.setenv("STITCH_GCP_PROJECT", "my-cloud-project")
    reset_config()
    server = McpServer()
    patched_httpx(server)

    await HttpStitchClient().text_to_ui("x", headers=await StitchAuth().headers())

    assert server.headers[0]["x-goog-api-key"] == "key-123"
    assert server.headers[0]["x-goog-user-project"] == "my-cloud-project"
    assert "authorization" not in server.headers[0]  # a key short-circuits the token path


async def test_a_static_access_token_is_sent_as_a_bearer(
    patched_httpx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STITCH_ACCESS_TOKEN", "ya29.abc")
    reset_config()
    server = McpServer()
    patched_httpx(server)

    await HttpStitchClient().text_to_ui("x", headers=await StitchAuth().headers())

    assert server.headers[0]["authorization"] == "Bearer ya29.abc"


# ---------------------------------------------------------------- flows & failures


async def test_refine_edits_the_named_screen(patched_httpx: Any) -> None:
    server = McpServer()
    patched_httpx(server)

    payload = await HttpStitchClient().refine(
        "proj-9/screen-1", "make it dark", headers=await _headers()
    )

    edit = server.arguments_for("edit_screens")
    assert set(edit) == {"projectId", "selectedScreenIds", "prompt"}
    assert edit["projectId"] == "proj-9"
    assert edit["selectedScreenIds"] == ["screen-1"]  # the API's own spelling, not `screenIds`
    assert edit["prompt"].startswith("make it dark")
    assert payload.external_ref == "proj-9/screen-1"
    assert payload.html == "<main>generated</main>"


async def test_fetch_code_reads_the_screen_without_generating(patched_httpx: Any) -> None:
    server = McpServer()
    patched_httpx(server)

    payload = await HttpStitchClient().fetch_code("proj-9/screen-1", headers=await _headers())

    assert server.tool_calls() == ["get_screen"]  # no create_project, no generation
    # `name` is the resource path the API requires; the id pair is its deprecated form.
    assert server.arguments_for("get_screen") == {
        "name": "projects/proj-9/screens/screen-1",
        "projectId": "proj-9",
        "screenId": "screen-1",
    }
    assert payload.html == "<main>generated</main>"


async def test_a_resource_path_id_is_reduced_to_its_last_segment(patched_httpx: Any) -> None:
    """Google resources answer as `projects/9/screens/1`; the transport stores plain ids."""
    server = McpServer()
    patched_httpx(server)

    payload = await HttpStitchClient().fetch_code(
        "projects/proj-9/screens/screen-1", headers=await _headers()
    )
    assert payload.external_ref.endswith("/screen-1")


async def test_a_tool_error_is_recoverable_not_a_silent_empty_design(patched_httpx: Any) -> None:
    """A tool rejection must classify as `contract` — Stitch can't serve this, but figma/fake may,
    so the stage falls back instead of 502-ing (a `fatal` would dead-end it)."""
    server = McpServer()

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("method") == "tools/call":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": "prompt rejected"}],
                    },
                },
            )
        return server._handle(request)

    original = httpx.AsyncClient

    def build(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handle)
        return original(*args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(httpx, "AsyncClient", build)
        with pytest.raises(ProviderError) as exc:
            await HttpStitchClient().text_to_ui("x", headers=await _headers())

    assert exc.value.detail == {"kind": "contract"}
    assert "prompt rejected" in exc.value.message


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (403, "auth"), (429, "quota"), (500, "transient"), (400, "contract")],
)
async def test_http_status_maps_to_the_error_taxonomy(status: int, kind: str) -> None:
    original = httpx.AsyncClient

    def build(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(lambda _r: httpx.Response(status))
        return original(*args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(httpx, "AsyncClient", build)
        with pytest.raises(ProviderError) as exc:
            await HttpStitchClient().text_to_ui("x", headers={"X-Goog-Api-Key": "k"})

    assert exc.value.detail == {"kind": kind}


# ---------------------------------------------------------------- surviving a lost generation


async def test_a_generation_whose_response_is_lost_is_recovered_from_the_project(
    patched_httpx: Any,
) -> None:
    """The bug this guards: Stitch generates, the HTTP call dies, the design is served anyway.

    A multi-screen prompt takes minutes against the live endpoint, so the response can be lost to a
    timeout long after Stitch has produced the screens and billed the generation. Falling back to
    another provider there makes the user watch Stitch build their design and then get a fake one.
    """
    server = McpServer()
    server.generate_times_out = True
    server.screens_after_generate = [screen_doc()]
    patched_httpx(server)

    payload = await HttpStitchClient().text_to_ui("a todo app", headers=await _headers())

    assert payload.html == "<main>generated</main>"
    assert payload.external_ref == "proj-9/screen-1"
    # It was recovered by re-reading the project, not by generating a second time.
    assert server.tool_calls().count("generate_screen_from_text") == 1


async def test_only_screens_the_lost_generation_created_are_recovered(
    patched_httpx: Any,
) -> None:
    """Pre-existing screens must not be mistaken for this generation's output."""
    stale = {"id": "old-1", "name": "projects/proj-9/screens/old-1"}
    server = McpServer()
    server.project_screens = [stale]  # snapshotted before the generate
    server.generate_times_out = True
    server.screens_after_generate = [stale, screen_doc()]
    patched_httpx(server)

    payload = await HttpStitchClient().text_to_ui("x", headers=await _headers())

    assert payload.external_ref == "proj-9/screen-1"  # the new screen, not `old-1`


async def test_a_lost_generation_that_produced_nothing_still_fails(
    patched_httpx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery must not invent a design: no new screen → the original error stands."""
    monkeypatch.setenv("STITCH_READY_TIMEOUT_S", "0")
    reset_config()
    server = McpServer()
    server.generate_times_out = True
    server.screens_after_generate = []
    patched_httpx(server)

    with pytest.raises(ProviderError) as exc:
        await HttpStitchClient().text_to_ui("x", headers=await _headers())

    assert exc.value.detail == {"kind": "transient"}


async def test_a_spurious_invalid_argument_on_a_read_is_retried(patched_httpx: Any) -> None:
    """The live endpoint rejects ~1 in 4 valid reads; one flake must not sink the design."""
    server = McpServer()
    server.flaky_tools = {"get_screen": 2}
    patched_httpx(server)

    payload = await HttpStitchClient().fetch_code("proj-9/screen-1", headers=await _headers())

    assert payload.html == "<main>generated</main>"
    assert server.tool_calls().count("get_screen") == 3  # two flakes, then the real answer


async def test_a_generation_rejected_as_invalid_is_not_retried(patched_httpx: Any) -> None:
    """A retry of a generate would spend a second generation from the monthly quota."""
    server = McpServer()
    server.flaky_tools = {"generate_screen_from_text": 1}
    server.screens_after_generate = []
    patched_httpx(server)

    with pytest.raises(ProviderError):
        await HttpStitchClient().text_to_ui("x", headers=await _headers())

    assert server.tool_calls().count("generate_screen_from_text") == 1


async def test_generation_gets_a_longer_timeout_than_a_metadata_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """60s is a fine read budget and far too short for a generation — they must differ."""
    monkeypatch.setenv("STITCH_TIMEOUT_S", "60")
    monkeypatch.setenv("STITCH_GENERATE_TIMEOUT_S", "300")
    reset_config()

    server = McpServer()
    seen: list[tuple[str, Any]] = []
    original = httpx.AsyncClient

    def build(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = server.transport()
        client = original(*args, **kwargs)
        seen.append((str(kwargs.get("timeout")), client))
        return client

    monkeypatch.setattr(httpx, "AsyncClient", build)
    await HttpStitchClient().text_to_ui("x", headers=await _headers())

    timeouts = [t for t, _ in seen]
    assert "300.0" in timeouts  # the generate call
    assert "60.0" in timeouts  # the handshake/reads
