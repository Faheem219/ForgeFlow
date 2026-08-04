"""Project CRUD + stage-state orchestration surface (phase-06).

The only caller of :mod:`app.projects.state_machine`; persists its pure results and emits
``stage.transition`` events. Downstream phases (conductor, stage handlers) call this service
rather than touching the repos directly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from beanie import PydanticObjectId
from bson.errors import InvalidId

# SystemError shadows the builtin (taxonomy name fixed by the plan); A004 suppressed on-line.
from app.core.errors import NotFoundError, SystemError, UserError  # noqa: A004
from app.db.models import Project, StageState
from app.db.models.common import utcnow
from app.db.models.enums import ProjectStatus, Stage, StageStatus
from app.db.repos import ProjectRepo, StageStateRepo
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.messages import MessageService
from app.projects.state_machine import Action, apply_transition
from app.realtime.hub import emit
from app.realtime.schemas import EventType


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

    async def delete(self, project_id: PydanticObjectId, user_id: PydanticObjectId) -> None:
        project = await self.get_owned(project_id, user_id)
        # Cascade: stage states, conversation, and versioned artifacts (+ their offloaded blobs).
        for state in await self._stages.list_for_project(project_id):
            await state.delete()
        await MessageService().delete_for_project(project_id)
        await ArtifactService().delete_for_project(project_id)
        await project.delete()

    async def list_stages(
        self, project_id: PydanticObjectId, user_id: PydanticObjectId
    ) -> list[StageState]:
        await self.get_owned(project_id, user_id)
        return await self._stages.list_for_project(project_id)

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

        try:
            result = apply_transition(snapshot, stage, action)
        except ValueError as exc:
            raise UserError(str(exc)) from exc

        await self._stages.set_status(project_id, result.stage, result.to_status)
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
