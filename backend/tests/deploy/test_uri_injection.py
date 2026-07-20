"""URI injection (phase-36): the right MONGODB_URI reaches the preview/deploy backend env, and it's
treated as a secret — composed correctly for the shared cluster and never logged."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from beanie import PydanticObjectId

from app.core.config import reset_config
from app.db.models import Project
from app.deploy.db_provision import DbProvisioner, _compose_uri
from app.sandbox.preview import PreviewService

pytestmark = pytest.mark.usefixtures("mongo_db", "fernet_key")


async def _project(db_name: str = "forgeflow_app_inject") -> Project:
    return await Project(user_id=PydanticObjectId(), name="p", app_db_name=db_name).insert()


def test_compose_handles_plain_srv_and_existing_path() -> None:
    # Plain host → db appended.
    assert _compose_uri("mongodb://host:27017", "appdb") == "mongodb://host:27017/appdb"
    # srv + query → db slotted before the query, query preserved.
    assert (
        _compose_uri("mongodb+srv://u:p@c.net/?retryWrites=true&w=majority", "appdb")
        == "mongodb+srv://u:p@c.net/appdb?retryWrites=true&w=majority"
    )
    # A stray database on the base is replaced, not duplicated.
    assert _compose_uri("mongodb://host:27017/ignored", "appdb") == "mongodb://host:27017/appdb"


async def test_platform_uri_targets_the_project_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_DB_CLUSTER_URI", "mongodb+srv://svc:pw@apps.net/?w=majority")
    reset_config()
    project = await _project("forgeflow_app_x1")

    uri = await DbProvisioner().get_app_mongodb_uri(project)
    # This is exactly what a deploy (phase-37 Render env) or preview would inject.
    assert uri == "mongodb+srv://svc:pw@apps.net/forgeflow_app_x1?w=majority"


async def test_uri_is_never_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("APP_DB_CLUSTER_URI", "mongodb+srv://svc:sup3rsecret@apps.net")
    reset_config()
    project = await _project("forgeflow_app_x2")

    with caplog.at_level(logging.DEBUG):
        uri = await DbProvisioner().get_app_mongodb_uri(project)

    assert "sup3rsecret" in uri  # the secret is in the returned URI …
    assert "sup3rsecret" not in caplog.text  # … but never in the logs
    assert uri not in caplog.text


# --------------------------------------------------------------------- preview injection


class _Handle:
    def stream(self) -> Any:
        return iter(())

    def wait(self) -> int:
        return 0

    def kill(self) -> None:
        pass


class _RecordingRuntime:
    """Records every start_exec so we can inspect the env preview injects (not a DockerRuntime)."""

    def __init__(self) -> None:
        self.execs: list[dict[str, Any]] = []

    def start_exec(self, argv: list[str], cwd: str = "", env: dict[str, str] | None = None) -> Any:
        self.execs.append({"argv": list(argv), "cwd": cwd, "env": dict(env) if env else None})
        return _Handle()


def _provider(runtime: _RecordingRuntime) -> Callable[[Project], Awaitable[Any]]:
    async def provide(_project: Project) -> Any:
        return runtime

    return provide


async def test_preview_backend_env_gets_the_provisioned_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://cluster:27017")
    monkeypatch.setenv("PREVIEW_HEALTH_TIMEOUT_S", "0")  # don't wait on health in the test
    reset_config()

    project = await _project("forgeflow_app_prev")
    runtime = _RecordingRuntime()
    await PreviewService(provider=_provider(runtime)).start(project)

    be_env = next(c["env"] for c in runtime.execs if c["env"] and "MONGODB_URI" in c["env"])
    assert be_env["MONGODB_URI"] == "mongodb://cluster:27017/forgeflow_app_prev"
