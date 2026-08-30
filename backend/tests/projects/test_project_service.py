from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.core.errors import NotFoundError, UserError
from app.db.models.enums import Stage, StageStatus
from app.db.repos import StageStateRepo
from app.projects.service import ProjectService, parse_object_id
from app.projects.state_machine import Action

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_create_project_initializes_six_empty_stages_and_app_db_name() -> None:
    user_id = PydanticObjectId()
    project = await ProjectService().create_project(user_id, "My App")
    assert project.id is not None

    assert project.app_db_name
    assert project.app_db_name != "forgeflow_meta"

    states = await ProjectService().list_stages(project.id, user_id)
    assert {s.stage for s in states} == set(Stage)
    assert all(s.status is StageStatus.empty for s in states)


async def test_create_project_rejects_blank_name() -> None:
    with pytest.raises(UserError):
        await ProjectService().create_project(PydanticObjectId(), "   ")


async def test_list_for_user_is_scoped() -> None:
    mine, other = PydanticObjectId(), PydanticObjectId()
    await ProjectService().create_project(mine, "p1")
    await ProjectService().create_project(mine, "p2")
    await ProjectService().create_project(other, "p3")

    listed = await ProjectService().list_for_user(mine)
    assert {p.name for p in listed} == {"p1", "p2"}


async def test_get_owned_rejects_foreign_project() -> None:
    owner, intruder = PydanticObjectId(), PydanticObjectId()
    project = await ProjectService().create_project(owner, "mine")
    assert project.id is not None

    with pytest.raises(NotFoundError):
        await ProjectService().get_owned(project.id, intruder)


async def test_get_owned_rejects_missing_project() -> None:
    with pytest.raises(NotFoundError):
        await ProjectService().get_owned(PydanticObjectId(), PydanticObjectId())


async def test_parse_object_id_rejects_garbage() -> None:
    with pytest.raises(NotFoundError):
        parse_object_id("not-an-object-id")


async def test_rename_requires_ownership() -> None:
    owner, intruder = PydanticObjectId(), PydanticObjectId()
    project = await ProjectService().create_project(owner, "mine")
    assert project.id is not None

    with pytest.raises(NotFoundError):
        await ProjectService().rename(project.id, intruder, "renamed")

    renamed = await ProjectService().rename(project.id, owner, "renamed")
    assert renamed.name == "renamed"


async def test_delete_removes_project_and_its_stage_states() -> None:
    owner = PydanticObjectId()
    project = await ProjectService().create_project(owner, "gone-soon")
    assert project.id is not None

    report = await ProjectService().delete(project.id, owner)

    with pytest.raises(NotFoundError):
        await ProjectService().get_owned(project.id, owner)
    assert await StageStateRepo().list_for_project(project.id) == []
    # External teardown must fail soft and *report* what happened rather than raising — a stopped
    # Docker daemon must never block removal. Whether a sandbox was actually there to reclaim
    # depends on the host, so the assertion is on the report's shape, not on the host's state.
    assert isinstance(report.sandbox_removed, bool)
    assert isinstance(report.warnings, list)


async def test_delete_cascades_project_scoped_documents() -> None:
    from app.db.models import Run

    owner = PydanticObjectId()
    project = await ProjectService().create_project(owner, "cascade-me")
    assert project.id is not None
    await Run(project_id=project.id, kind="conductor:build").insert()
    await Run(project_id=project.id, kind="codegen:build").insert()

    report = await ProjectService().delete(project.id, owner)

    assert report.documents_removed >= 2
    assert await Run.find({"project_id": project.id}).count() == 0


async def test_delete_tears_down_the_projects_deployments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Until phase-62 a deleted project kept serving — and billing — its live URLs, with nothing in
    ForgeFlow left pointing at them."""
    from app.db.models import Deployment, DeploymentRef
    from app.db.models.enums import DeployMode
    from app.deploy import teardown as teardown_mod

    destroyed: list[str] = []

    class StubProvider:
        async def destroy(self, ref: object, **_: object) -> bool:
            destroyed.append(getattr(ref, "id", ""))
            return True

    monkeypatch.setattr(teardown_mod, "provider_for", lambda _target: StubProvider())

    owner = PydanticObjectId()
    project = await ProjectService().create_project(owner, "deployed")
    assert project.id is not None
    await Deployment(
        project_id=project.id,
        mode=DeployMode.seamless,
        urls={"be": "https://api", "fe": "https://web"},
        refs={
            "be": DeploymentRef(provider="vercel", id="dpl_be", project="api"),
            "fe": DeploymentRef(provider="vercel", id="dpl_fe", project="web"),
        },
        status="live",
    ).insert()

    report = await ProjectService().delete(project.id, owner)

    assert sorted(destroyed) == ["dpl_be", "dpl_fe"]
    assert report.deployments_destroyed == 2
    # The record itself goes with the rest of the project metadata (CASCADE_MODELS).
    assert await Deployment.find({"project_id": project.id}).count() == 0


async def test_delete_reports_a_deployment_it_could_not_reclaim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-soft like the sandbox and app-DB legs: an unreachable provider must not leave the user
    with a project they cannot remove, but must not vanish silently either."""
    from app.db.models import Deployment, DeploymentRef
    from app.db.models.enums import DeployMode
    from app.deploy import teardown as teardown_mod

    class RefusingProvider:
        async def destroy(self, ref: object, **_: object) -> bool:
            raise RuntimeError("vercel is unreachable")

    monkeypatch.setattr(teardown_mod, "provider_for", lambda _target: RefusingProvider())

    owner = PydanticObjectId()
    project = await ProjectService().create_project(owner, "stubborn")
    assert project.id is not None
    await Deployment(
        project_id=project.id,
        mode=DeployMode.seamless,
        urls={"be": "https://api"},
        refs={"be": DeploymentRef(provider="vercel", id="dpl_be", project="api")},
        status="live",
    ).insert()

    report = await ProjectService().delete(project.id, owner)

    # The project is still gone…
    with pytest.raises(NotFoundError):
        await ProjectService().get_owned(project.id, owner)
    # …and what outlived it is named.
    assert report.deployments_destroyed == 0
    assert any("vercel is unreachable" in w for w in report.warnings)


async def test_delete_skips_deployments_with_nothing_to_reclaim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A record written before refs were persisted, or one already deleted, has no provider call."""
    from app.db.models import STATUS_DELETED, Deployment
    from app.db.models.enums import DeployMode
    from app.deploy import teardown as teardown_mod

    calls: list[str] = []

    class Spy:
        async def destroy(self, ref: object, **_: object) -> bool:  # pragma: no cover
            calls.append("called")
            return True

    monkeypatch.setattr(teardown_mod, "provider_for", lambda _target: Spy())

    owner = PydanticObjectId()
    project = await ProjectService().create_project(owner, "legacy")
    assert project.id is not None
    await Deployment(project_id=project.id, mode=DeployMode.seamless, status="live").insert()
    await Deployment(
        project_id=project.id, mode=DeployMode.seamless, status=STATUS_DELETED
    ).insert()

    report = await ProjectService().delete(project.id, owner)

    assert calls == []
    assert report.deployments_destroyed == 0


async def test_transition_stage_rejects_illegal_hard_prereq() -> None:
    owner = PydanticObjectId()
    project = await ProjectService().create_project(owner, "p")
    assert project.id is not None

    with pytest.raises(UserError):
        await ProjectService().transition_stage(project.id, owner, Stage.deploy, Action.enter)


async def test_transition_stage_persists_and_updates_current_stage() -> None:
    owner = PydanticObjectId()
    project = await ProjectService().create_project(owner, "p")
    assert project.id is not None

    outcome = await ProjectService().transition_stage(
        project.id, owner, Stage.design, Action.complete
    )
    assert outcome.to_status is StageStatus.complete

    states = await ProjectService().list_stages(project.id, owner)
    design_state = next(s for s in states if s.stage is Stage.design)
    assert design_state.status is StageStatus.complete

    refreshed = await ProjectService().get_owned(project.id, owner)
    assert refreshed.current_stage is Stage.design


async def test_transition_stage_propagates_stale_on_refine() -> None:
    owner = PydanticObjectId()
    project = await ProjectService().create_project(owner, "p")
    assert project.id is not None

    await ProjectService().transition_stage(project.id, owner, Stage.requirements, Action.complete)
    await ProjectService().transition_stage(project.id, owner, Stage.design, Action.complete)
    outcome = await ProjectService().transition_stage(
        project.id, owner, Stage.requirements, Action.refine
    )

    assert outcome.stale == (Stage.design,)
    states = await ProjectService().list_stages(project.id, owner)
    design_state = next(s for s in states if s.stage is Stage.design)
    assert design_state.status is StageStatus.stale
