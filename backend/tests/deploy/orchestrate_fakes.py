"""Shared fakes for the deploy-orchestration tests (phase-37).

Every collaborator the orchestrator drives is behind a seam, so these fakes let the ordering, env
wiring, health and failure logic run without Docker, a real DB, or a real provider.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from beanie import PydanticObjectId

from app.db.models import Project
from app.db.models.enums import CredentialKind, DeployMode, Stage, StageStatus
from app.db.repos import StageStateRepo
from app.deploy.analyzer import BackendPlan, DatabasePlan, FrontendPlan, InfraPlan
from app.deploy.providers.base import (
    DeployRef,
    DeployResult,
    DeploySpec,
    DeployState,
    DeployTarget,
    LogLine,
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
    """Records deploy/env/status calls; returns a live URL, or fails on ``deploy``.

    Two *different* ways to fail, because the real providers have two (phase-58):

    - ``fail=True`` raises a ``ProviderError`` — an auth/quota/network refusal.
    - ``status=`` returns that state without raising — a provider that accepted the request and
      then reported ``failed``, or that is still ``building`` when the health poll times out. This
      is the common case and was previously untested.
    """

    def __init__(
        self,
        key: str,
        target: DeployTarget,
        url: str,
        *,
        fail: bool = False,
        status: DeployState = DeployState.live,
        env_fails: bool = False,
        destroy_fails: bool = False,
        log_lines: list[str] | None = None,
        credential_kind: CredentialKind = CredentialKind.vercel,
    ) -> None:
        self.key = key
        self.target = target
        # Part of the `DeployProvider` contract, not an implementation detail: phase-62's BYO
        # readiness check asks the provider which vault credential it authenticates with.
        self.credential_kind = credential_kind
        self._url = url
        self._fail = fail
        self._status = status
        self._env_fails = env_fails
        self._destroy_fails = destroy_fails
        self._log_lines = log_lines or []
        self.deploy_specs: list[DeploySpec] = []
        #: One entry per ``set_env`` call: the ref it targeted and the env it wrote (phase-62).
        self.env_calls: list[dict[str, str]] = []
        self.env_refs: list[DeployRef] = []
        self.destroy_refs: list[DeployRef] = []
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
            ref=DeployRef(id=f"{self.key}-1", project=spec.name, target=self.target),
            provider=self.key,
            target=self.target,
            status=self._status,
            url=self._url if self._status is DeployState.live else None,
        )

    async def status(
        self, ref: DeployRef, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> DeployResult:
        self.status_calls += 1
        # Polling must report the SAME state the deploy returned, or a provider stuck in `building`
        # would silently "become" live and the health-timeout path could never be exercised.
        return DeployResult(
            ref=ref,
            provider=self.key,
            target=self.target,
            status=self._status,
            url=self._url if self._status is DeployState.live else None,
        )

    async def set_env(
        self,
        ref: DeployRef,
        env: Mapping[str, str],
        *,
        mode: DeployMode,
        user_id: PydanticObjectId | None = None,
    ) -> None:
        self.env_refs.append(ref)
        if self._env_fails:
            # A provider that accepted the deployment and then refused the project-env write. The
            # deployment is already live at this point, so it must survive this (phase-62).
            raise deploy_error(
                self.key, DeployErrorKind.fatal, f"{self.key} rejected the env write"
            )
        self.env_calls.append(dict(env))

    async def logs(
        self, ref: DeployRef, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> list[LogLine]:
        return [LogLine(message=line) for line in self._log_lines]

    async def destroy(
        self, ref: DeployRef, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> bool:
        self.destroy_refs.append(ref)
        if self._destroy_fails:
            raise deploy_error(self.key, DeployErrorKind.fatal, f"{self.key} refused the destroy")
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


class FakeBackendSource:
    """Returns a canned backend file set; records the plan dir it was asked for (phase-58)."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.files = files or {
            "package.json": '{"name":"backend"}',
            "vercel.json": '{"version":2}',
            "api/index.ts": "export { default } from '../src/serverless'",
            "src/app.ts": "export const createApp = () => null",
        }
        self.calls: list[str] = []

    async def __call__(self, project: Project, be: BackendPlan) -> dict[str, str]:
        self.calls.append(be.dir)
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
