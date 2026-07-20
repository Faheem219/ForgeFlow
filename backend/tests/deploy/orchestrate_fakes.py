"""Shared fakes for the deploy-orchestration tests (phase-37).

Every collaborator the orchestrator drives is behind a seam, so these fakes let the ordering, env
wiring, health and failure logic run without Docker, a real DB, or a real provider.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from beanie import PydanticObjectId

from app.db.models import Project
from app.db.models.enums import DeployMode, Stage, StageStatus
from app.db.repos import StageStateRepo
from app.deploy.analyzer import BackendPlan, DatabasePlan, FrontendPlan, InfraPlan
from app.deploy.providers.base import (
    DeployRef,
    DeployResult,
    DeploySpec,
    DeployState,
    DeployTarget,
)
from app.deploy.providers.errors import DeployErrorKind, deploy_error
from app.projects.service import ProjectService


def full_plan() -> InfraPlan:
    """The fixed-stack shape: a Vite FE, an Express BE, and a Mongo DB."""
    return InfraPlan(fe=FrontendPlan(), be=BackendPlan(), db=DatabasePlan())


def static_plan_source(plan: InfraPlan) -> Callable[[Project], Awaitable[InfraPlan]]:
    async def source(_project: Project) -> InfraPlan:
        return plan

    return source


async def project_with_build(name: str = "app") -> Project:
    """A project whose build stage is complete (the deploy hard prereq)."""
    project = await ProjectService().create_project(PydanticObjectId(), name)
    assert project.id is not None
    await StageStateRepo().set_status(project.id, Stage.build, StageStatus.complete)
    return project


class FakeDeployProvider:
    """Records deploy/env/status calls; returns a live URL, or fails on ``deploy``."""

    def __init__(self, key: str, target: DeployTarget, url: str, *, fail: bool = False) -> None:
        self.key = key
        self.target = target
        self._url = url
        self._fail = fail
        self.deploy_specs: list[DeploySpec] = []
        self.env_calls: list[dict[str, str]] = []
        self.status_calls = 0

    async def deploy(
        self, spec: DeploySpec, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> DeployResult:
        self.deploy_specs.append(spec)
        if self._fail:
            raise deploy_error(
                self.key,
                DeployErrorKind.fatal,
                f"{self.key} deploy failed",
                target=str(self.target),
            )
        return DeployResult(
            ref=DeployRef(id=f"{self.key}-1", project=spec.name),
            provider=self.key,
            target=self.target,
            status=DeployState.live,
            url=self._url,
        )

    async def status(
        self, ref: DeployRef, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> DeployResult:
        self.status_calls += 1
        return DeployResult(
            ref=ref, provider=self.key, target=self.target, status=DeployState.live, url=self._url
        )

    async def set_env(
        self,
        ref: DeployRef,
        env: Mapping[str, str],
        *,
        mode: DeployMode,
        user_id: PydanticObjectId | None = None,
    ) -> None:
        self.env_calls.append(dict(env))

    async def logs(
        self, ref: DeployRef, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> list[Any]:
        return []

    async def destroy(
        self, ref: DeployRef, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> bool:
        return True


def factory_of(
    fe: FakeDeployProvider, be: FakeDeployProvider
) -> Callable[[DeployTarget], FakeDeployProvider]:
    def factory(target: DeployTarget) -> FakeDeployProvider:
        return fe if target is DeployTarget.fe else be

    return factory


class FakeFrontendBuilder:
    """Returns a canned built-SPA file set; records the build env it was handed."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.files = files or {"index.html": "<!doctype html>", "assets/app.js": "console.log(1)"}
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, project: Project, fe: FrontendPlan, build_env: dict[str, str]
    ) -> dict[str, str]:
        self.calls.append({"dir": fe.dir, "env": dict(build_env)})
        return dict(self.files)


class RecordingEmitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(
        self, channel: str, event_type: object, payload: dict[str, Any], stage: object = None
    ) -> None:
        self.events.append((str(event_type), payload))

    def steps(self) -> list[str]:
        return [str(p.get("step")) for _e, p in self.events if "step" in p]
