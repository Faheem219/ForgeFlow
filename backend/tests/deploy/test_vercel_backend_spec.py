"""What a backend deploy actually sends to Vercel (phase-58).

The backend ships as an **inline upload** of the workspace source plus the skeleton's
``vercel.json`` — the same transport the frontend already used, which is what removes the need for
a git host. These tests pin the request body, because the difference between a working function and
a static 404 is entirely in ``projectSettings`` and where the env lands.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.config import reset_config
from app.db.models.enums import DeployMode
from app.deploy.providers.base import DeploySpec, DeployTarget
from app.deploy.providers.vercel import VercelDeployProvider
from tests.deploy.conftest import FakeDeployApi, no_sleep

pytestmark = pytest.mark.usefixtures("mongo_db")

_CREATED = {"id": "dpl_be_1", "readyState": "READY", "url": "todos-api.vercel.app"}


@pytest.fixture
def platform_creds(monkeypatch: pytest.MonkeyPatch, fernet_key: str) -> None:
    monkeypatch.setenv("VERCEL_TOKEN", "platform-vercel-token")
    reset_config()


def _source() -> dict[str, str]:
    return {
        "vercel.json": '{"version":2}',
        "api/index.ts": "export { default } from '../src/serverless'",
        "package.json": '{"name":"backend"}',
    }


def _be_spec(env: dict[str, str] | None = None) -> DeploySpec:
    return DeploySpec(
        name="forgeflow-todos-api",
        target=DeployTarget.be,
        env=env or {},
        files=_source(),
        start_cmd="pnpm start",
        port=3001,
    )


async def _deploy(api: FakeDeployApi, spec: DeploySpec) -> dict[str, Any]:
    provider = VercelDeployProvider(transport=api.transport(), sleep=no_sleep)
    await provider.deploy(spec, mode=DeployMode.seamless)
    body = api.bodies[0]
    parsed = json.loads(body) if isinstance(body, (str, bytes)) else body
    assert isinstance(parsed, dict)
    return parsed


async def test_the_workspace_source_is_uploaded_inline(platform_creds: None) -> None:
    api = FakeDeployApi({("POST", "/v13/deployments"): (200, _CREATED)})

    body = await _deploy(api, _be_spec())

    uploaded = {entry["file"]: entry["data"] for entry in body["files"]}
    assert uploaded == _source()


async def test_framework_detection_is_disabled_but_the_output_dir_is_not_pinned(
    platform_creds: None,
) -> None:
    """The uploaded vercel.json declares the function; a static outputDirectory would 404 it."""
    api = FakeDeployApi({("POST", "/v13/deployments"): (200, _CREATED)})

    body = await _deploy(api, _be_spec())

    assert body["projectSettings"] == {"framework": None}


async def test_the_db_uri_is_runtime_env_only(platform_creds: None) -> None:
    """MONGODB_URI is a secret read per request — it must not be baked into the build env."""
    api = FakeDeployApi({("POST", "/v13/deployments"): (200, _CREATED)})

    body = await _deploy(api, _be_spec({"MONGODB_URI": "mongodb+srv://u:p@c/db"}))

    assert body["env"]["MONGODB_URI"] == "mongodb+srv://u:p@c/db"
    assert "build" not in body


async def test_a_frontend_deploy_is_unchanged(platform_creds: None) -> None:
    """Regression guard: the SPA path still pins the output dir and sets the build env."""
    api = FakeDeployApi({("POST", "/v13/deployments"): (200, _CREATED)})
    spec = DeploySpec(
        name="forgeflow-todos",
        target=DeployTarget.fe,
        env={"VITE_API_BASE_URL": "https://api.example.com"},
        files={"index.html": "<h1>"},
    )

    body = await _deploy(api, spec)

    assert body["projectSettings"] == {"framework": None, "outputDirectory": None}
    # A Vite var is read at BUILD time, so it has to be in both places.
    assert body["build"]["env"]["VITE_API_BASE_URL"] == "https://api.example.com"
    assert body["env"]["VITE_API_BASE_URL"] == "https://api.example.com"
