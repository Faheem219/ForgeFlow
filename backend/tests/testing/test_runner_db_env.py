"""The test stage must hand the backend suites a ``MONGODB_URI`` (and only the backend suites).

The runner used to pass ``env=None`` for both unit invocations, so the generated backend fell back
to the skeleton's ``mongodb://localhost:27017/forgeflow_app`` — inside a sandbox, that is the
sandbox itself, where nothing is listening. A suite that mocked Mongoose passed and hid it; a suite
that really connected hung until mongoose gave up.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet

from app.core.config import reset_config
from app.deploy.secrets import reset_vault
from app.testing.runner import TestRunner
from tests.testing.conftest import (
    FakeRunnerWorkspace,
    RecordingEmitter,
    ScriptedExec,
    jest_report,
    make_project,
    vitest_report,
)

pytestmark = pytest.mark.usefixtures("mongo_db", "vault_key", "blob_env")

_SANDBOX_BASE = "mongodb://forgeflow-appdb:27017"


@pytest.fixture
def vault_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A real key, so the BYO lookup takes its normal path rather than the fail-soft one."""
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("FERNET_KEY", key)
    reset_config()
    reset_vault()
    yield key
    reset_vault()


@pytest.fixture
def sandbox_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_DB_SANDBOX_URI", _SANDBOX_BASE)
    reset_config()


def _workspace() -> FakeRunnerWorkspace:
    return FakeRunnerWorkspace(
        {
            "frontend/vitest-report.json": vitest_report(),
            "backend/jest-report.json": jest_report(failing=False),
        }
    )


def _env_of(scripted: ScriptedExec, framework: str) -> dict[str, str] | None:
    call = next(c for c in scripted.calls if c["framework"] == framework)
    env = call["env"]
    assert env is None or isinstance(env, dict)
    return env


async def test_the_backend_suite_is_given_a_database(sandbox_route: None) -> None:
    project = await make_project()
    scripted = ScriptedExec()

    await TestRunner(exec_service=scripted, workspace=_workspace(), emitter=RecordingEmitter()).run(
        project, scope="unit"
    )

    env = _env_of(scripted, "jest")
    assert env is not None
    assert env["MONGODB_URI"] == f"{_SANDBOX_BASE}/db_test"


async def test_it_is_not_the_database_the_preview_serves(sandbox_route: None) -> None:
    """A suite clearing collections must not be able to wipe the user's app data."""
    project = await make_project()
    scripted = ScriptedExec()

    await TestRunner(exec_service=scripted, workspace=_workspace(), emitter=RecordingEmitter()).run(
        project, scope="unit"
    )

    env = _env_of(scripted, "jest")
    assert env is not None
    assert env["MONGODB_URI"].rsplit("/", 1)[-1] != project.app_db_name


async def test_the_frontend_suite_gets_no_database(sandbox_route: None) -> None:
    """A Vite app can only ever receive VITE_-prefixed vars; Mongo is none of its business."""
    project = await make_project()
    scripted = ScriptedExec()

    await TestRunner(exec_service=scripted, workspace=_workspace(), emitter=RecordingEmitter()).run(
        project, scope="unit"
    )

    assert _env_of(scripted, "vitest") is None


async def test_playwright_keeps_its_own_env_untouched(sandbox_route: None) -> None:
    """E2E drives the preview servers, which already carry the app's env."""
    project = await make_project()
    scripted = ScriptedExec()
    workspace = FakeRunnerWorkspace({"frontend/playwright-report.json": '{"suites": []}'})

    await TestRunner(exec_service=scripted, workspace=workspace, emitter=RecordingEmitter()).run(
        project, scope="e2e"
    )

    env = _env_of(scripted, "playwright")
    assert env == {"PLAYWRIGHT_JSON_OUTPUT_NAME": "playwright-report.json"}


async def test_a_run_still_completes_when_no_database_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail soft: projects whose tests mock Mongoose have never needed one, and still don't."""
    monkeypatch.setenv("APP_DB_SANDBOX_URI", "")
    monkeypatch.setenv("APP_DB_CLUSTER_URI", "")
    monkeypatch.setenv("MONGODB_URI", "")
    reset_config()
    project = await make_project()
    scripted = ScriptedExec()

    run = await TestRunner(
        exec_service=scripted, workspace=_workspace(), emitter=RecordingEmitter()
    ).run(project, scope="unit")

    assert run.id is not None  # the run happened rather than erroring out
    assert _env_of(scripted, "jest") == {}
