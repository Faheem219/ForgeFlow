"""Vercel adapter (phase-35): deploy the built SPA, set env, read status/logs (mocked HTTP)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.config import reset_config
from app.core.errors import ProviderError
from app.db.models.enums import DeployMode
from app.deploy.providers.base import DeployRef, DeploySpec, DeployState, DeployTarget
from app.deploy.providers.errors import DeployErrorKind, kind_of
from app.deploy.providers.vercel import VercelDeployProvider
from tests.deploy.conftest import FakeDeployApi, error_detail, no_sleep

pytestmark = pytest.mark.usefixtures("mongo_db")

TOKEN = "vercel-platform-token"
BUNDLE = {"index.html": "<!doctype html><div id=root></div>", "assets/app.js": "console.log(1)"}


@pytest.fixture
def platform_token(monkeypatch: pytest.MonkeyPatch, fernet_key: str) -> str:
    monkeypatch.setenv("VERCEL_TOKEN", TOKEN)
    reset_config()
    return TOKEN


def spec(**overrides: Any) -> DeploySpec:
    base: dict[str, Any] = {
        "name": "forgeflow-todos",
        "target": DeployTarget.fe,
        "files": BUNDLE,
    }
    base.update(overrides)
    return DeploySpec(**base)


def provider(api: FakeDeployApi) -> VercelDeployProvider:
    return VercelDeployProvider(transport=api.transport(), sleep=no_sleep)


def _deployment(ready_state: str = "READY") -> dict[str, object]:
    return {"id": "dpl_abc123", "url": "forgeflow-todos.vercel.app", "readyState": ready_state}


# ---------------------------------------------------------------- deploy


async def test_deploys_the_built_bundle_and_returns_a_live_url(platform_token: str) -> None:
    api = FakeDeployApi({("POST", "/v13/deployments"): (200, _deployment())})

    result = await provider(api).deploy(spec(), mode=DeployMode.seamless)

    assert result.provider == "vercel"
    assert result.target is DeployTarget.fe
    assert result.status is DeployState.live
    assert result.url == "https://forgeflow-todos.vercel.app"
    assert result.ref.id == "dpl_abc123"
    assert result.ref.project == "forgeflow-todos"


async def test_deploy_uploads_every_built_file(platform_token: str) -> None:
    api = FakeDeployApi({("POST", "/v13/deployments"): (200, _deployment())})

    await provider(api).deploy(spec(), mode=DeployMode.seamless)

    body = api.body_of("POST", "/v13/deployments")
    assert body["name"] == "forgeflow-todos"
    assert body["target"] == "production"
    assert {f["file"] for f in body["files"]} == set(BUNDLE)
    assert {f["file"]: f["data"] for f in body["files"]} == BUNDLE


async def test_deploy_sends_the_bearer_token(platform_token: str) -> None:
    api = FakeDeployApi({("POST", "/v13/deployments"): (200, _deployment())})

    await provider(api).deploy(spec(), mode=DeployMode.seamless)

    assert api.requests[0].headers["Authorization"] == f"Bearer {TOKEN}"


async def test_deploy_passes_build_env_through(platform_token: str) -> None:
    api = FakeDeployApi({("POST", "/v13/deployments"): (200, _deployment())})

    await provider(api).deploy(
        spec(env={"VITE_API_BASE_URL": "https://api.example.com"}), mode=DeployMode.seamless
    )

    body = api.body_of("POST", "/v13/deployments")
    assert body["env"] == {"VITE_API_BASE_URL": "https://api.example.com"}
    assert body["build"]["env"] == {"VITE_API_BASE_URL": "https://api.example.com"}


async def test_a_building_deployment_is_not_reported_live(platform_token: str) -> None:
    api = FakeDeployApi({("POST", "/v13/deployments"): (200, _deployment("BUILDING"))})

    result = await provider(api).deploy(spec(), mode=DeployMode.seamless)
    assert result.status is DeployState.building


async def test_a_frontend_deployment_needs_a_built_bundle() -> None:
    with pytest.raises(ProviderError) as exc:
        spec(files={})
    assert kind_of(exc.value) is DeployErrorKind.config


# ---------------------------------------------------------------- env


async def test_set_env_upserts_project_scoped_production_vars(platform_token: str) -> None:
    api = FakeDeployApi({("POST", "/v10/projects"): (200, {"key": "ok"})})
    ref = DeployRef(id="dpl_abc123", project="forgeflow-todos")

    await provider(api).set_env(
        ref, {"VITE_API_BASE_URL": "https://api.example.com"}, mode=DeployMode.seamless
    )

    request = api.calls("POST", "/v10/projects/forgeflow-todos/env")[0]
    assert request.url.params["upsert"] == "true"
    body = api.body_of("POST", "/v10/projects")
    assert body == {
        "key": "VITE_API_BASE_URL",
        "value": "https://api.example.com",
        "type": "plain",
        "target": ["production"],
    }


async def test_a_secret_shaped_var_is_stored_encrypted(platform_token: str) -> None:
    """``MONGODB_URI`` carries cluster credentials. ``plain`` would leave a connection string
    readable in Vercel's settings store and returnable from its API; ``encrypted`` still lists the
    key in the dashboard (the visibility the project-scoped copy exists for) without that."""
    api = FakeDeployApi({("POST", "/v10/projects"): (200, {"key": "ok"})})
    ref = DeployRef(id="dpl_abc123", project="forgeflow-api")

    await provider(api).set_env(
        ref, {"MONGODB_URI": "mongodb+srv://u:p@c.mongodb.net/db"}, mode=DeployMode.seamless
    )

    assert api.body_of("POST", "/v10/projects")["type"] == "encrypted"


async def test_each_var_is_typed_independently(platform_token: str) -> None:
    """One upsert per key, so a secret and a non-secret in the same map get different treatment."""
    api = FakeDeployApi({("POST", "/v10/projects"): (200, {"key": "ok"})})
    ref = DeployRef(id="dpl_abc123", project="forgeflow-api")

    await provider(api).set_env(
        ref,
        {"MONGODB_URI": "mongodb+srv://u:p@c.mongodb.net/db", "NODE_ENV": "production"},
        mode=DeployMode.seamless,
    )

    bodies = {b["key"]: b["type"] for b in api.bodies if isinstance(b, dict) and "key" in b}
    assert bodies == {"MONGODB_URI": "encrypted", "NODE_ENV": "plain"}


async def test_set_env_without_a_project_is_a_config_error(platform_token: str) -> None:
    api = FakeDeployApi()

    with pytest.raises(ProviderError) as exc:
        await provider(api).set_env(DeployRef(id="dpl_1"), {"A": "b"}, mode=DeployMode.seamless)

    assert kind_of(exc.value) is DeployErrorKind.config


# ---------------------------------------------------------------- status / logs / destroy


async def test_status_maps_the_ready_state(platform_token: str) -> None:
    api = FakeDeployApi({("GET", "/v13/deployments"): (200, _deployment("ERROR"))})

    result = await provider(api).status(DeployRef(id="dpl_abc123"), mode=DeployMode.seamless)

    assert result.status is DeployState.failed
    assert api.paths == ["/v13/deployments/dpl_abc123"]


async def test_status_prefers_the_production_alias(platform_token: str) -> None:
    payload = {**_deployment(), "alias": ["todos.example.com"]}
    api = FakeDeployApi({("GET", "/v13/deployments"): (200, payload)})

    result = await provider(api).status(DeployRef(id="dpl_abc123"), mode=DeployMode.seamless)
    assert result.url == "https://todos.example.com"


async def test_logs_are_returned_as_lines(platform_token: str) -> None:
    events = [
        {"created": 1, "text": "Installing dependencies"},
        {"created": 2, "payload": {"text": "Build completed"}},
        {"created": 3},  # no text at all — skipped rather than crashing
    ]
    api = FakeDeployApi({("GET", "/v2/deployments"): (200, events)})

    lines = await provider(api).logs(DeployRef(id="dpl_abc123"), mode=DeployMode.seamless)

    assert [line.message for line in lines] == ["Installing dependencies", "Build completed"]
    assert lines[0].ts == "1"


async def test_destroy_deletes_the_deployment(platform_token: str) -> None:
    api = FakeDeployApi({("DELETE", "/v13/deployments"): (200, {"state": "DELETED"})})

    assert await provider(api).destroy(DeployRef(id="dpl_abc123"), mode=DeployMode.seamless)
    assert api.paths == ["/v13/deployments/dpl_abc123"]


# ---------------------------------------------------------------- errors + resilience


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, DeployErrorKind.auth),
        (403, DeployErrorKind.auth),
        (429, DeployErrorKind.quota),
        (400, DeployErrorKind.fatal),
    ],
)
async def test_http_failures_are_classified(
    platform_token: str, status: int, expected: DeployErrorKind
) -> None:
    api = FakeDeployApi({("POST", "/v13/deployments"): (status, {"error": "nope"})})

    with pytest.raises(ProviderError) as exc:
        await provider(api).deploy(spec(), mode=DeployMode.seamless)

    assert kind_of(exc.value) is expected
    assert exc.value.fallback_hint  # always actionable for phase-37/48


async def test_a_transient_failure_is_retried_then_succeeds(platform_token: str) -> None:
    attempts: list[int] = []

    def flaky(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(200, json=_deployment())

    api = FakeDeployApi({("POST", "/v13/deployments"): flaky})

    result = await provider(api).deploy(spec(), mode=DeployMode.seamless)

    assert len(attempts) == 2  # one retry
    assert result.status is DeployState.live


async def test_a_persistent_transient_failure_eventually_raises(platform_token: str) -> None:
    api = FakeDeployApi({("POST", "/v13/deployments"): (500, {"error": "boom"})})

    with pytest.raises(ProviderError) as exc:
        await provider(api).deploy(spec(), mode=DeployMode.seamless)

    assert kind_of(exc.value) is DeployErrorKind.transient
    assert len(api.requests) == 3  # 1 + deploy_max_retries (2)


async def test_the_error_names_the_failing_target(platform_token: str) -> None:
    """Phase-37 reports partial failures — the target must be on the error."""
    api = FakeDeployApi({("POST", "/v13/deployments"): (400, {"error": "nope"})})

    with pytest.raises(ProviderError) as exc:
        await provider(api).deploy(spec(), mode=DeployMode.seamless)

    assert error_detail(exc.value)["target"] == "fe"
    assert error_detail(exc.value)["provider"] == "vercel"


async def test_a_missing_deployment_id_is_fatal(platform_token: str) -> None:
    api = FakeDeployApi({("POST", "/v13/deployments"): (200, {"url": "x.vercel.app"})})

    with pytest.raises(ProviderError) as exc:
        await provider(api).deploy(spec(), mode=DeployMode.seamless)

    assert kind_of(exc.value) is DeployErrorKind.fatal
