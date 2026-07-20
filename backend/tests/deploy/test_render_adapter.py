"""Render adapter (phase-35): deploy the Node web service, set env, status/logs (mocked HTTP)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.config import reset_config
from app.core.errors import ProviderError
from app.db.models.enums import DeployMode
from app.deploy.providers.base import DeployRef, DeploySpec, DeployState, DeployTarget
from app.deploy.providers.errors import DeployErrorKind, kind_of
from app.deploy.providers.render import RenderDeployProvider
from tests.deploy.conftest import FakeDeployApi, Route, error_detail, no_sleep

pytestmark = pytest.mark.usefixtures("mongo_db")

TOKEN = "render-platform-key"


@pytest.fixture
def platform_token(monkeypatch: pytest.MonkeyPatch, fernet_key: str) -> str:
    monkeypatch.setenv("RENDER_API_KEY", TOKEN)
    reset_config()
    return TOKEN


def spec(**overrides: Any) -> DeploySpec:
    base: dict[str, Any] = {
        "name": "forgeflow-todos-api",
        "target": DeployTarget.be,
        "repo_url": "https://github.com/example/todos",
        "root_dir": "backend",
        "build_cmd": "pnpm install && pnpm build",
        "start_cmd": "pnpm start",
        "port": 3001,
    }
    base.update(overrides)
    return DeploySpec(**base)


def provider(api: FakeDeployApi) -> RenderDeployProvider:
    return RenderDeployProvider(transport=api.transport(), sleep=no_sleep)


def _service(status: str = "live") -> dict[str, object]:
    return {
        "id": "srv-abc123",
        "name": "forgeflow-todos-api",
        "status": status,
        "serviceDetails": {"url": "https://forgeflow-todos-api.onrender.com"},
    }


def _created_routes() -> dict[tuple[str, str], Route]:
    return {
        ("POST", "/v1/services/"): (201, {"id": "dep-1", "status": "created"}),
        ("POST", "/v1/services"): (201, {"service": _service("created")}),
    }


# ---------------------------------------------------------------- deploy


async def test_creates_a_node_web_service_and_returns_its_url(platform_token: str) -> None:
    api = FakeDeployApi(_created_routes())

    result = await provider(api).deploy(spec(), mode=DeployMode.seamless)

    assert result.provider == "render"
    assert result.target is DeployTarget.be
    assert result.url == "https://forgeflow-todos-api.onrender.com"
    assert result.ref.id == "srv-abc123"
    assert result.status is DeployState.queued


async def test_the_service_is_a_persistent_node_web_service(platform_token: str) -> None:
    """The whole reason the backend is not on Vercel — it must be a long-lived process."""
    api = FakeDeployApi(_created_routes())

    await provider(api).deploy(spec(), mode=DeployMode.seamless)

    body = api.body_of("POST", "/v1/services")
    assert body["type"] == "web_service"
    assert body["serviceDetails"]["env"] == "node"
    assert body["serviceDetails"]["envSpecificDetails"]["startCommand"] == "pnpm start"
    assert (
        body["serviceDetails"]["envSpecificDetails"]["buildCommand"] == "pnpm install && pnpm build"
    )
    assert body["repo"] == "https://github.com/example/todos"
    assert body["rootDir"] == "backend"


async def test_env_vars_are_sent_with_the_service(platform_token: str) -> None:
    api = FakeDeployApi(_created_routes())

    await provider(api).deploy(
        spec(env={"MONGODB_URI": "mongodb+srv://host/db", "NODE_ENV": "production"}),
        mode=DeployMode.seamless,
    )

    body = api.body_of("POST", "/v1/services")
    assert body["envVars"] == [
        {"key": "MONGODB_URI", "value": "mongodb+srv://host/db"},
        {"key": "NODE_ENV", "value": "production"},
    ]


async def test_deploy_is_triggered_after_the_service_is_created(platform_token: str) -> None:
    api = FakeDeployApi(_created_routes())

    await provider(api).deploy(spec(), mode=DeployMode.seamless)

    assert api.paths == ["/v1/services", "/v1/services/srv-abc123/deploys"]


async def test_deploy_sends_the_bearer_token(platform_token: str) -> None:
    api = FakeDeployApi(_created_routes())

    await provider(api).deploy(spec(), mode=DeployMode.seamless)

    assert api.requests[0].headers["Authorization"] == f"Bearer {TOKEN}"


async def test_a_backend_deployment_needs_a_start_command() -> None:
    with pytest.raises(ProviderError) as exc:
        spec(start_cmd="")
    assert kind_of(exc.value) is DeployErrorKind.config


# ---------------------------------------------------------------- env / status / logs / destroy


async def test_set_env_replaces_the_service_env_vars(platform_token: str) -> None:
    api = FakeDeployApi({("PUT", "/v1/services"): (200, [])})

    await provider(api).set_env(
        DeployRef(id="srv-abc123"),
        {"MONGODB_URI": "mongodb+srv://host/db", "NODE_ENV": "production"},
        mode=DeployMode.seamless,
    )

    assert api.paths == ["/v1/services/srv-abc123/env-vars"]
    assert api.body_of("PUT") == [
        {"key": "MONGODB_URI", "value": "mongodb+srv://host/db"},
        {"key": "NODE_ENV", "value": "production"},
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("live", DeployState.live),
        ("build_in_progress", DeployState.building),
        ("build_failed", DeployState.failed),
        ("deactivated", DeployState.canceled),
        ("something_new", DeployState.queued),  # unknown → safe default, never a crash
    ],
)
async def test_status_maps_render_states(
    platform_token: str, raw: str, expected: DeployState
) -> None:
    api = FakeDeployApi({("GET", "/v1/services"): (200, _service(raw))})

    result = await provider(api).status(DeployRef(id="srv-abc123"), mode=DeployMode.seamless)

    assert result.status is expected
    assert api.paths == ["/v1/services/srv-abc123"]


async def test_logs_are_returned_as_lines(platform_token: str) -> None:
    payload = {
        "logs": [
            {"timestamp": "2026-07-20T00:00:00Z", "message": "Starting service"},
            {"timestamp": "2026-07-20T00:00:01Z", "message": "Listening on 3001"},
            {"timestamp": "2026-07-20T00:00:02Z"},  # no message — skipped
        ]
    }
    api = FakeDeployApi({("GET", "/v1/logs"): (200, payload)})

    lines = await provider(api).logs(DeployRef(id="srv-abc123"), mode=DeployMode.seamless)

    assert [line.message for line in lines] == ["Starting service", "Listening on 3001"]
    assert lines[0].ts == "2026-07-20T00:00:00Z"
    assert api.requests[0].url.params["resource"] == "srv-abc123"


async def test_destroy_deletes_the_service(platform_token: str) -> None:
    api = FakeDeployApi({("DELETE", "/v1/services"): (200, {})})

    assert await provider(api).destroy(DeployRef(id="srv-abc123"), mode=DeployMode.seamless)
    assert api.paths == ["/v1/services/srv-abc123"]


# ---------------------------------------------------------------- errors + resilience


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, DeployErrorKind.auth),
        (429, DeployErrorKind.quota),
        (422, DeployErrorKind.fatal),
    ],
)
async def test_http_failures_are_classified(
    platform_token: str, status: int, expected: DeployErrorKind
) -> None:
    api = FakeDeployApi({("POST", "/v1/services"): (status, {"message": "nope"})})

    with pytest.raises(ProviderError) as exc:
        await provider(api).deploy(spec(), mode=DeployMode.seamless)

    assert kind_of(exc.value) is expected
    assert error_detail(exc.value)["target"] == "be"
    assert exc.value.fallback_hint


async def test_a_transient_failure_is_retried(platform_token: str) -> None:
    attempts: list[int] = []

    def flaky(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(502, json={"message": "bad gateway"})
        return httpx.Response(201, json={"service": _service("created")})

    api = FakeDeployApi(
        {("POST", "/v1/services/"): (201, {"id": "dep-1"}), ("POST", "/v1/services"): flaky}
    )

    result = await provider(api).deploy(spec(), mode=DeployMode.seamless)

    assert len(attempts) == 2
    assert result.ref.id == "srv-abc123"


async def test_a_missing_service_id_is_fatal(platform_token: str) -> None:
    api = FakeDeployApi({("POST", "/v1/services"): (201, {"service": {"name": "x"}})})

    with pytest.raises(ProviderError) as exc:
        await provider(api).deploy(spec(), mode=DeployMode.seamless)

    assert kind_of(exc.value) is DeployErrorKind.fatal
