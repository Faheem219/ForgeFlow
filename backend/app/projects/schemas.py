from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models import Project, StageState
from app.db.models.enums import ProjectStatus, Stage, StageStatus
from app.projects.service import TransitionOutcome
from app.projects.state_machine import Action


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectPublic(BaseModel):
    id: str
    name: str
    current_stage: Stage
    status: ProjectStatus
    stack: str
    sandbox_id: str | None
    app_db_name: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_project(cls, project: Project) -> ProjectPublic:
        return cls(
            id=str(project.id),
            name=project.name,
            current_stage=project.current_stage,
            status=project.status,
            stack=project.stack,
            sandbox_id=project.sandbox_id,
            app_db_name=project.app_db_name,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )


class StageStatePublic(BaseModel):
    stage: Stage
    status: StageStatus
    artifacts: list[str]
    updated_at: datetime

    @classmethod
    def from_state(cls, state: StageState) -> StageStatePublic:
        return cls(
            stage=state.stage,
            status=state.status,
            artifacts=[str(a) for a in state.artifacts],
            updated_at=state.updated_at,
        )


class StageTransitionRequest(BaseModel):
    action: Action


class StageTransitionResponse(BaseModel):
    stage: Stage
    from_status: StageStatus
    to_status: StageStatus
    stale: list[Stage]

    @classmethod
    def from_outcome(cls, outcome: TransitionOutcome) -> StageTransitionResponse:
        return cls(
            stage=outcome.stage,
            from_status=outcome.from_status,
            to_status=outcome.to_status,
            stale=list(outcome.stale),
        )
