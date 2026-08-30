"""A frontend that fails to *build* must degrade the deploy, not abandon it.

The SPA is compiled inside ForgeFlow's own sandbox, so a broken build surfaces as a ``UserError``
from the frontend builder — not as the ``ProviderError`` the orchestrator used to catch. Escaping
that ``except`` abandoned the whole deploy: the backend was already live (and billed), the database
already provisioned, and no ``Deployment`` was written to record either. The user saw one sentence
("that didn't go through") and the topology kept showing the state before the attempt.

The same applies to a plan with no frontend at all. That is legal — a backend-only project deploys
fine — but it must be *said*, because the only other evidence is a node quietly missing from the
graph, which reads as a rendering bug rather than as a decision the analyzer made.
"""

from __future__ import annotations

import pytest

from app.core.errors import UserError
from app.db.models.enums import DeployMode
from app.deploy.analyzer import BackendPlan, DatabasePlan, FrontendPlan, InfraPlan
from app.deploy.orchestrate import (
    STATUS_DEGRADED,
    STATUS_FAILED,
    STATUS_LIVE,
    DeployOrchestrator,
)
from app.deploy.providers.base import DeployTarget
from tests.deploy.orchestrate_fakes import (
    FakeBackendSource,
    FakeDeployProvider,
    FakeFrontendBuilder,
    RecordingEmitter,
    factory_of,
    full_plan,
    project_with_build,
    static_plan_source,
)

pytestmark = pytest.mark.usefixtures("mongo_db", "fernet_key", "blob_env", "deployable_db")

_BE_URL = "https://api.vercel.app"
_FE_URL = "https://web.vercel.app"

#: What a real broken build looks like coming out of the builder: the command, its exit code, and
#: the compiler's own words. All three have to reach the user.
_BUILD_ERROR = (
    "The frontend build failed: `pnpm build` exited 1.\n\n"
    "src/App.tsx(12,7): error TS2322: Type 'number' is not assignable to type 'string'."
)


class _BrokenFrontendBuilder:
    """A builder whose build command fails — the shape a compile error really arrives in."""

    def __init__(self, message: str = _BUILD_ERROR) -> None:
        self.message = message
        self.calls = 0

    async def __call__(
        self, project: object, fe: object, build_env: dict[str, str]
    ) -> dict[str, str]:
        self.calls += 1
        raise UserError(self.message)


def _orchestrator(
    *,
    fe: FakeDeployProvider,
    be: FakeDeployProvider,
    emitter: RecordingEmitter,
    plan: InfraPlan | None = None,
    frontend_builder: object | None = None,
    backend_source: object | None = None,
) -> DeployOrchestrator:
    return DeployOrchestrator(
        plan_source=static_plan_source(plan or full_plan()),
        frontend_builder=frontend_builder or FakeFrontendBuilder(),  # type: ignore[arg-type]
        backend_source=backend_source or FakeBackendSource(),  # type: ignore[arg-type]
        provider_factory=factory_of(fe, be),
        emitter=emitter,
    )


async def test_a_failed_build_degrades_instead_of_abandoning_the_deploy() -> None:
    """The record must exist: without it the live backend is orphaned and untracked."""
    emitter = RecordingEmitter()
    builder = _BrokenFrontendBuilder()
    fe = FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL)
    be = FakeDeployProvider("vercel", DeployTarget.be, _BE_URL)
    project = await project_with_build()

    deployment = await _orchestrator(
        fe=fe, be=be, emitter=emitter, frontend_builder=builder
    ).deploy(project, mode=DeployMode.seamless)

    assert builder.calls == 1
    assert deployment.status == STATUS_DEGRADED
    # The backend that really did go live is recorded, not lost with the aborted deploy.
    assert deployment.urls == {"be": _BE_URL}
    assert deployment.be_target == "vercel"
    # Nothing was shipped to the FE provider — there was no bundle to ship.
    assert fe.deploy_specs == []


async def test_the_compiler_error_reaches_the_user() -> None:
    """The point of the fix: the *cause*, not just "produced no output"."""
    emitter = RecordingEmitter()
    fe = FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL)
    be = FakeDeployProvider("vercel", DeployTarget.be, _BE_URL)
    project = await project_with_build()

    await _orchestrator(
        fe=fe, be=be, emitter=emitter, frontend_builder=_BrokenFrontendBuilder()
    ).deploy(project, mode=DeployMode.seamless)

    failed = [
        p for _e, p in emitter.events if p.get("step") == "fe" and p.get("status") == "failed"
    ]
    assert len(failed) == 1
    assert "TS2322" in failed[0]["error"]
    assert "pnpm build" in failed[0]["error"]


async def test_the_topology_shows_the_frontend_as_failed() -> None:
    """A planned frontend that did not ship stays on the graph — as failed, not as absent."""
    fe = FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL)
    be = FakeDeployProvider("vercel", DeployTarget.be, _BE_URL)
    project = await project_with_build()

    deployment = await _orchestrator(
        fe=fe, be=be, emitter=RecordingEmitter(), frontend_builder=_BrokenFrontendBuilder()
    ).deploy(project, mode=DeployMode.seamless)

    fe_node = next(n for n in deployment.topology_snapshot["nodes"] if n["id"] == "fe")
    assert fe_node["status"] == "failed"
    assert fe_node["url"] is None


async def test_a_redeploy_after_a_failed_build_succeeds() -> None:
    """The degraded record must not block the retry that fixes it."""
    project = await project_with_build()
    be1 = FakeDeployProvider("vercel", DeployTarget.be, _BE_URL)
    first = await _orchestrator(
        fe=FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL),
        be=be1,
        emitter=RecordingEmitter(),
        frontend_builder=_BrokenFrontendBuilder(),
    ).deploy(project, mode=DeployMode.seamless)
    assert first.status == STATUS_DEGRADED

    second = await _orchestrator(
        fe=FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL),
        be=FakeDeployProvider("vercel", DeployTarget.be, _BE_URL),
        emitter=RecordingEmitter(),
    ).deploy(project, mode=DeployMode.seamless)

    assert second.status == STATUS_LIVE
    assert second.urls == {"be": _BE_URL, "fe": _FE_URL}
    assert second.id != first.id


# --- a plan with no frontend --------------------------------------------------------------------


async def test_a_plan_without_a_frontend_says_so_rather_than_going_quiet() -> None:
    """The failure mode this replaces: a backend-only deploy reported as a clean `live`.

    Nothing in the log, no event, and a topology whose missing frontend node was the only clue.
    """
    emitter = RecordingEmitter()
    fe = FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL)
    be = FakeDeployProvider("vercel", DeployTarget.be, _BE_URL)
    project = await project_with_build()
    be_only = InfraPlan(fe=None, be=BackendPlan(), db=DatabasePlan())

    deployment = await _orchestrator(fe=fe, be=be, emitter=emitter, plan=be_only).deploy(
        project, mode=DeployMode.seamless
    )

    # A backend-only project is legal, so this is still a success...
    assert deployment.status == STATUS_LIVE
    assert fe.deploy_specs == []
    # ...but the omission is announced.
    fe_events = [p for _e, p in emitter.events if p.get("step") == "fe"]
    assert [p["status"] for p in fe_events] == ["skipped"]
    assert "no frontend" in fe_events[0]["error"]


async def test_the_skip_is_written_to_the_deploy_log(blob_env: None) -> None:
    """The pipeline log is what the UI shows after a reload; the reason has to survive there."""
    from app.db.blobs import get_blob_store

    fe = FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL)
    be = FakeDeployProvider("vercel", DeployTarget.be, _BE_URL)
    project = await project_with_build()
    be_only = InfraPlan(fe=None, be=BackendPlan(), db=DatabasePlan())

    deployment = await _orchestrator(fe=fe, be=be, emitter=RecordingEmitter(), plan=be_only).deploy(
        project, mode=DeployMode.seamless
    )

    assert deployment.logs_ref is not None
    log = (await get_blob_store().get(deployment.logs_ref)).decode("utf-8")
    assert "fe: skipped — no frontend was detected" in log


# --- the backend half of the same defect ---------------------------------------------------------


class _BrokenBackendSource:
    """The source reader's own refusals (empty dir, no vercel.json) are ``UserError`` too."""

    async def __call__(self, project: object, be: object) -> dict[str, str]:
        raise UserError("The backend has no vercel.json — it cannot be deployed as a function.")


async def test_an_unusable_backend_source_is_recorded_not_raised() -> None:
    """Otherwise the deploy vanishes after the database was already provisioned."""
    emitter = RecordingEmitter()
    fe = FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL)
    be = FakeDeployProvider("vercel", DeployTarget.be, _BE_URL)
    project = await project_with_build()

    deployment = await _orchestrator(
        fe=fe,
        be=be,
        emitter=emitter,
        plan=InfraPlan(fe=FrontendPlan(), be=BackendPlan(), db=DatabasePlan()),
        backend_source=_BrokenBackendSource(),
    ).deploy(project, mode=DeployMode.seamless)

    assert deployment.status == STATUS_FAILED
    assert deployment.db_target is not None  # the DB work that already happened is still recorded
    be_failed = [
        p for _e, p in emitter.events if p.get("step") == "be" and p.get("status") == "failed"
    ]
    assert len(be_failed) == 1
    assert "vercel.json" in be_failed[0]["error"]
    # A backend that never came up must not drag a billed frontend up with it.
    assert fe.deploy_specs == []
