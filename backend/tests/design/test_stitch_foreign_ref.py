"""Stitch must not send another provider's design ref to the server.

Defence in depth behind `provider_for_refine`: pairing a foreign ref (a `fake-text-…`, an imported
design, a Figma key) with whatever project this client last created is what turned a provider
mix-up into an opaque *"Requested entity was not found"* from Google's API. Rejected locally, with
a message that names the actual problem — and `fatal`, since no other provider could refine it
either, so the fallback chain must not spend attempts on it.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.core.config import reset_config
from app.core.errors import ProviderError
from app.design.stitch import HttpStitchClient, _looks_like_stitch_id
from app.design.stitch_auth import StitchAuth

MCP_URL = "https://stitch.example/mcp"


@pytest.fixture(autouse=True)
def _mcp_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STITCH_MCP_URL", MCP_URL)
    reset_config()


async def _headers() -> dict[str, str]:
    return await StitchAuth(token_fetcher=lambda: _token()).headers()


async def _token() -> tuple[str, float]:
    return "tok", 3600.0


class _NotFoundServer:
    """An MCP endpoint that answers every tool call the way Stitch reports a missing entity."""

    def __init__(self) -> None:
        self.tool_calls: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body.get("method")
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            return self._rpc(body["id"], {"protocolVersion": "2024-11-05"})
        if method == "tools/list":
            return self._rpc(
                body["id"],
                {
                    "tools": [
                        {
                            "name": "edit_screens",
                            "inputSchema": {
                                "properties": {
                                    "projectId": {},
                                    "selectedScreenIds": {},
                                    "prompt": {},
                                },
                                "required": ["projectId", "selectedScreenIds", "prompt"],
                            },
                        }
                    ]
                },
            )
        if method == "tools/call":
            self.tool_calls.append(body["params"]["name"])
            return self._rpc(
                body["id"],
                {
                    "isError": True,
                    "content": [{"type": "text", "text": "Requested entity was not found."}],
                },
            )
        return httpx.Response(400, json={"error": "unexpected"})

    @staticmethod
    def _rpc(rpc_id: int, result: dict[str, Any]) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": rpc_id, "result": result})


@pytest.fixture
def route_to(monkeypatch: pytest.MonkeyPatch) -> Any:
    original = httpx.AsyncClient

    def factory(server: _NotFoundServer) -> None:
        def build(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = server.transport()
            return original(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", build)

    return factory


# ---------------------------------------------------------------- the local guard


@pytest.mark.parametrize(
    "ref",
    [
        "fake-text-6f58ed671eaeea68",  # the fallback provider's ref — the reported failure
        "fake-image-cd15c5f7ca8161bb",
        "figma-file-key",
        "imported",
    ],
)
async def test_a_foreign_ref_is_refused_before_any_call(route_to: Any, ref: str) -> None:
    server = _NotFoundServer()
    route_to(server)

    with pytest.raises(ProviderError) as excinfo:
        await HttpStitchClient().refine(ref, "make it dark", headers=await _headers())

    assert "not produced by Stitch" in excinfo.value.message
    detail = excinfo.value.detail
    assert isinstance(detail, dict) and detail["kind"] == "fatal"  # never spend a fallback on this
    assert server.tool_calls == []  # …and never bother the server with it


async def test_a_real_stitch_ref_is_not_mistaken_for_a_foreign_one(route_to: Any) -> None:
    """The guard must not block the normal `{projectId}/{screenId}` shape."""
    server = _NotFoundServer()
    route_to(server)

    with pytest.raises(ProviderError):  # the scripted server always answers not-found
        await HttpStitchClient().refine(
            "18394645912139309833/ef74c24d8213487cb5898c30b78c0ff8",
            "make it dark",
            headers=await _headers(),
        )

    # …preceded only by the pre-edit screen snapshot. The edit *was* sent — this ref is ours.
    assert server.tool_calls == ["list_screens", "edit_screens"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ef74c24d8213487cb5898c30b78c0ff8", True),  # a real Stitch screen id
        ("18394645912139309833", True),  # a real Stitch project id
        ("fake-text-6f58ed671eaeea68", False),
        ("imported", False),
        ("abc", False),  # too short to be one of ours
        ("", False),
    ],
)
def test_stitch_id_recognition(value: str, expected: bool) -> None:
    assert _looks_like_stitch_id(value) is expected


# ---------------------------------------------------------------- the server's own not-found


async def test_a_missing_entity_is_explained_as_such_not_as_a_schema_problem(
    route_to: Any,
) -> None:
    """A NOT_FOUND means the project/screen is gone — pointing at the schema misleads the user."""
    server = _NotFoundServer()
    route_to(server)

    with pytest.raises(ProviderError) as excinfo:
        await HttpStitchClient().refine(
            "proj-9/ef74c24d8213487cb5898c30b78c0ff8",
            "make it dark",
            headers=await _headers(),
        )

    hint = excinfo.value.fallback_hint or ""
    assert "no longer exists" in hint
    assert "Generate a new design" in hint
    assert "stitch_probe" not in hint  # the schema-drift hint would be the wrong advice here
