"""Project CRUD + stage-state orchestration surface (phase-06).

The only caller of :mod:`app.projects.state_machine`; persists its pure results and emits
``stage.transition`` events. Downstream phases (conductor, stage handlers) call this service
rather than touching the repos directly.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from beanie import Document, PydanticObjectId
from bson.errors import InvalidId

# SystemError shadows the builtin (taxonomy name fixed by the plan); A004 suppressed on-line.
from app.core.errors import NotFoundError, SystemError, UserError  # noqa: A004
from app.db.models import (
    Deployment,
    Project,
    RepairAttempt,
    RequirementSpec,
    Run,
    StageState,
    TestRun,
    TestSuite,
)
from app.db.models.common import utcnow
from app.db.models.enums import ProjectStatus, Stage, StageStatus
from app.db.repos import ProjectRepo, StageStateRepo
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.messages import MessageService
from app.projects.state_machine import Action, apply_transition
from app.realtime.hub import emit
from app.realtime.schemas import EventType

logger = logging.getLogger(__name__)

#: Per-project collections cascaded on delete. Stage states, messages and artifacts are handled
#: separately (they own blobs / need per-document cleanup); everything else is a plain bulk delete.
CASCADE_MODELS: tuple[type[Document], ...] = (
    RequirementSpec,
    TestSuite,
    TestRun,
    RepairAttempt,
    Deployment,
    Run,
)


@dataclass
class DeleteReport:
    """What a delete actually reclaimed. Warnings name external resources that outlived it."""

    sandbox_removed: bool = False
    app_db_dropped: bool = False
    #: Provider deployments taken down (phase-62), across every deployment record of the project.
    deployments_destroyed: int = 0
    documents_removed: int = 0
    warnings: list[str] = field(default_factory=list)


def parse_object_id(raw: str, label: str = "Project") -> PydanticObjectId:
    """Parse a path-param string into an ObjectId, or 404 (never leak malformed-id vs missing)."""
    try:
        return PydanticObjectId(raw)
    except (InvalidId, ValueError, TypeError) as exc:
        raise NotFoundError(f"{label} not found") from exc


@dataclass(frozen=True)
class TransitionOutcome:
    stage: Stage
    from_status: StageStatus
    to_status: StageStatus
    stale: tuple[Stage, ...]


class ProjectService:
    def __init__(self) -> None:
        self._projects = ProjectRepo()
        self._stages = StageStateRepo()

    async def create_project(self, user_id: PydanticObjectId, name: str) -> Project:
        name = name.strip()
        if not name:
            raise UserError("Project name must not be empty")
        app_db_name = f"forgeflow_app_{uuid.uuid4().hex[:20]}"
        project = await self._projects.insert(
            Project(user_id=user_id, name=name, app_db_name=app_db_name)
        )
        if project.id is None:  # pragma: no cover - an inserted document always has an id
            raise SystemError("Project insert did not return an id")
        for stage in Stage:
            await self._stages.get_or_create(project.id, stage)
        return project

    async def list_for_user(self, user_id: PydanticObjectId) -> list[Project]:
        return await self._projects.list_for_user(user_id)

    async def get_owned(self, project_id: PydanticObjectId, user_id: PydanticObjectId) -> Project:
        project = await self._projects.get(project_id)
        if project is None or project.user_id != user_id:
            raise NotFoundError("Project not found")
        return project

    async def rename(
        self, project_id: PydanticObjectId, user_id: PydanticObjectId, name: str
    ) -> Project:
        name = name.strip()
        if not name:
            raise UserError("Project name must not be empty")
        project = await self.get_owned(project_id, user_id)
        project.name = name
        project.updated_at = utcnow()
        await project.save()
        return project

    async def archive(self, project_id: PydanticObjectId, user_id: PydanticObjectId) -> Project:
        project = await self.get_owned(project_id, user_id)
        project.status = ProjectStatus.archived
        project.updated_at = utcnow()
        await project.save()
        return project

    async def delete(
        self,
        project_id: PydanticObjectId,
        user_id: PydanticObjectId,
        *,
        drop_app_data: bool = True,
    ) -> DeleteReport:
        """Delete a project and everything it owns.

        Ordering is deliberate: **external** resources (sandbox container + volume, the per-project
        application database) go first, while the ``Project`` document still exists to identify
        them. Dropping the document first would orphan a running container and a live database with
        no record pointing at either.

        External teardown FAILS SOFT. A stopped Docker daemon or an unreachable Atlas cluster must
        not leave the user with a project they cannot remove — the metadata cascade proceeds and the
        report names what could not be reclaimed, so it can be surfaced and retried out-of-band.
        Metadata deletion, by contrast, is allowed to raise: a half-deleted project in our own store
        is a bug, not a degraded mode.
        """
        project = await self.get_owned(project_id, user_id)
        report = DeleteReport()

        # -- external resources (best effort) ----------------------------------------------
        # Deployments first: they are the only externally *reachable* thing this project owns, and
        # until phase-62 nothing ever destroyed them — a deleted project kept serving (and billing)
        # its live URLs with nothing in ForgeFlow left pointing at them.
        report.deployments_destroyed = await self._destroy_deployments(project_id, user_id, report)

        try:
            from app.sandbox.manager import get_manager

            await get_manager().destroy(project, remove_volume=True)
            report.sandbox_removed = True
        except (
            Exception
        ) as exc:  # noqa: BLE001 - deliberately broad; teardown must not block delete
            report.warnings.append(f"sandbox: {exc}")
            logger.warning(
                "project delete: sandbox teardown failed",
                extra={"project_id": str(project_id), "error": str(exc)},
            )

        if drop_app_data:
            try:
                from app.deploy.db_provision import DbProvisioner

                report.app_db_dropped = await DbProvisioner().teardown(project, drop_data=True)
            except Exception as exc:  # noqa: BLE001 - same rationale as above
                report.warnings.append(f"app database: {exc}")
                logger.warning(
                    "project delete: app database teardown failed",
                    extra={"project_id": str(project_id), "error": str(exc)},
                )

        # -- control-plane metadata (must succeed) -----------------------------------------
        for state in await self._stages.list_for_project(project_id):
            await state.delete()
        await MessageService().delete_for_project(project_id)
        await ArtifactService().delete_for_project(project_id)

        # Everything else keyed by project_id. Kept as a table so a new per-project collection is
        # one line away from being cascaded — the failure mode here is silent orphan documents.
        for model in CASCADE_MODELS:
            # Beanie returns ``DeleteResult | None`` (None when the motor driver reports nothing),
            # so the count is read defensively — it is a report field, never a control-flow value.
            result = await model.find({"project_id": project_id}).delete()
            report.documents_removed += getattr(result, "deleted_count", 0) or 0

        await project.delete()

        await emit(
            str(project_id),
            EventType.project_deleted,
            {"project_id": str(project_id), "warnings": report.warnings},
        )
        return report

    async def list_stages(
        self, project_id: PydanticObjectId, user_id: PydanticObjectId
    ) -> list[StageState]:
        await self.get_owned(project_id, user_id)
        return await self._stages.list_for_project(project_id)

    async def _destroy_deployments(
        self,
        project_id: PydanticObjectId,
        user_id: PydanticObjectId,
        report: DeleteReport,
    ) -> int:
        """Take down every live deployment this project owns. Fail-soft, like the other legs."""
        from app.db.models import STATUS_DELETED
        from app.deploy.teardown import DeploymentTeardown

        destroyed = 0
        try:
            records = await Deployment.find({"project_id": project_id}).to_list()
        except Exception as exc:  # noqa: BLE001 - a read failure must not block the delete
            report.warnings.append(f"deployments: {exc}")
            return 0

        teardown = DeploymentTeardown()
        for record in records:
            if record.status == STATUS_DELETED or not record.refs:
                continue
            try:
                result = await teardown.destroy(record, user_id=user_id)
            except Exception as exc:  # noqa: BLE001 - same rationale as the sandbox leg
                report.warnings.append(f"deployment {record.id}: {exc}")
                logger.warning(
                    "project delete: deployment teardown failed",
                    extra={"project_id": str(project_id), "deployment_id": str(record.id)},
                )
                continue
            destroyed += len(result.destroyed)
            report.warnings.extend(f"deployment {record.id} — {w}" for w in result.warnings)
        return destroyed

    async def transition_stage(
        self,
        project_id: PydanticObjectId,
        user_id: PydanticObjectId,
        stage: Stage,
        action: Action,
    ) -> TransitionOutcome:
        project = await self.get_owned(project_id, user_id)
        current = await self._stages.list_for_project(project_id)
        snapshot = {state.stage: state.status for state in current}
        # What an `unskip` of this stage would restore (recorded by the `skip` that hid it).
        restore_point = next(
            (state.previous_status for state in current if state.stage is stage), None
        )

        try:
            result = apply_transition(snapshot, stage, action, restore_point=restore_point)
        except ValueError as exc:
            raise UserError(str(exc)) from exc

        await self._stages.set_status(
            project_id, result.stage, result.to_status, previous_status=result.restore_point
        )
        for stale_stage in result.stale:
            await self._stages.set_status(project_id, stale_stage, StageStatus.stale)

        project.current_stage = stage
        project.updated_at = utcnow()
        await project.save()

        await emit(
            str(project_id),
            EventType.stage_transition,
            {
                "stage": str(result.stage),
                "from": str(result.from_status),
                "to": str(result.to_status),
                "stale": [str(s) for s in result.stale],
            },
            stage=result.stage,
        )

        return TransitionOutcome(
            stage=result.stage,
            from_status=result.from_status,
            to_status=result.to_status,
            stale=result.stale,
        )
