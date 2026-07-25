"""The real Stitch MCP transport: JSON-RPC 2.0 over HTTP to ``stitch.googleapis.com/mcp``.

These exercise :class:`HttpStitchClient` against a scripted ASGI-free HTTP mock, so the protocol
itself is covered: the initialize handshake, the ``tools/call`` envelope, SSE framing, the session
header, tool-error handling, project bootstrapping, and the HTML download that turns Stitch's
download URL into the markup ForgeFlow stores.
"""

from __future__ import annotations

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


class McpServer:
    """A scripted Stitch MCP endpoint: records requests, answers by tool name."""

    def __init__(self, *, sse: bool = False, session: str | None = "sess-1") -> None:
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self.sse = sse
        self.session = session
        self.screens: dict[str, dict[str, Any]] = {}

    # -- wiring ---------------------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == HTML_URL:
            return httpx.Response(200, text="<main>generated</main>")

        body = json.loads(request.content)
        self.requests.append(body)
        self.headers.append(request.headers)

        if body.get("method") == "notifications/initialized":
            return httpx.Response(202)
        if body.get("method") == "initialize":
            headers = {"Mcp-Session-Id": self.session} if self.session else {}
            return self._rpc(body["id"], {"protocolVersion": "2024-11-05"}, headers)
        if body.get("method") == "tools/call":
            name = body["params"]["name"]
            args = body["params"].get("arguments", {})
            return self._rpc(body["id"], {"content": [self._tool(name, args)]})
        return httpx.Response(400, json={"error": "unexpected"})

    def _tool(self, name: str, args: dict[str, Any]) -> dict[str, str]:
        if name == "create_project":
            return self._text({"projectId": "proj-9"})
        if name == "generate_screen_from_text":
            self.screens["screen-1"] = {"htmlUrl": HTML_URL, "imageUrl": "https://img.example/1"}
            return self._text({"screenId": "screen-1"})  # id only — forces a get_screen
        if name == "edit_screens":
            return self._text({"screenId": args["screenIds"][0]})
        if name == "get_screen":
            return self._text(
                {
                    "screenId": args["screenId"],
                    **self.screens.get(args["screenId"], {"htmlUrl": HTML_URL}),
                }
            )
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
    assert methods[:2] == ["initialize", "notifications/initialized"]
    assert server.tool_calls() == ["create_project", "generate_screen_from_text", "get_screen"]

    body = server.requests[0]
    assert body["jsonrpc"] == "2.0" and "id" in body  # a real JSON-RPC envelope, not a bare call
    assert server.arguments_for("generate_screen_from_text") == {
        "projectId": "proj-9",
        "prompt": "a todo app",
    }
    # The screen's HTML arrives as a download URL and is fetched into the stored design.
    assert payload.html == "<main>generated</main>"
    assert payload.external_ref == "proj-9/screen-1"
    assert payload.preview_image == "https://img.example/1"


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
    # ...and the project is created once, then reused.
    assert server.tool_calls().count("create_project") == 1


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

    assert server.arguments_for("edit_screens") == {
        "projectId": "proj-9",
        "screenIds": ["screen-1"],
        "prompt": "make it dark",
    }
    assert payload.external_ref == "proj-9/screen-1"


async def test_fetch_code_reads_the_screen_without_generating(patched_httpx: Any) -> None:
    server = McpServer()
    patched_httpx(server)

    payload = await HttpStitchClient().fetch_code("proj-9/screen-1", headers=await _headers())

    assert server.tool_calls() == ["get_screen"]  # no create_project, no generation
    assert payload.html == "<main>generated</main>"


async def test_a_resource_path_id_is_reduced_to_its_last_segment(patched_httpx: Any) -> None:
    """Google resources answer as `projects/9/screens/1`; the transport stores plain ids."""
    server = McpServer()
    patched_httpx(server)

    payload = await HttpStitchClient().fetch_code(
        "projects/proj-9/screens/screen-1", headers=await _headers()
    )
    assert payload.external_ref.endswith("/screen-1")


async def test_a_tool_error_is_fatal_not_a_silent_empty_design(patched_httpx: Any) -> None:
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

    assert exc.value.detail == {"kind": "fatal"}
    assert "prompt rejected" in exc.value.message


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (403, "auth"), (429, "quota"), (500, "transient"), (400, "fatal")],
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
