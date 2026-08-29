from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from beanie import PydanticObjectId
from pydantic import BaseModel, Field

from app.db.models import Artifact, Message
from app.db.models.enums import ArtifactType, MessageRole, Stage, StageStatus


class MessagePublic(BaseModel):
    id: str
    stage: Stage | None
    role: MessageRole
    content: str
    artifacts: list[str]
    token_usage: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_message(cls, message: Message) -> MessagePublic:
        return cls(
            id=str(message.id),
            stage=message.stage,
            role=message.role,
            content=message.content,
            artifacts=[str(a) for a in message.artifacts],
            token_usage=message.token_usage,
            created_at=message.created_at,
        )


class ArtifactPublic(BaseModel):
    id: str
    project_id: str
    stage: Stage
    type: ArtifactType
    version: int
    ref: str | None
    meta: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_artifact(cls, artifact: Artifact) -> ArtifactPublic:
        return cls(
            id=str(artifact.id),
            project_id=str(artifact.project_id),
            stage=artifact.stage,
            type=artifact.type,
            version=artifact.version,
            ref=artifact.ref,
            meta=artifact.meta,
            created_at=artifact.created_at,
        )


class ArtifactDetail(ArtifactPublic):
    """An artifact plus its resolved text payload (inline or fetched from the blob store)."""

    content: str | None

    @classmethod
    def from_artifact_with_content(cls, artifact: Artifact, content: str | None) -> ArtifactDetail:
        base = ArtifactPublic.from_artifact(artifact)
        return cls(**base.model_dump(), content=content)


class ArtifactDiffResponse(BaseModel):
    a: str
    b: str
    diff: str


# --------------------------------------------------------------------- Conductor (phase-08)


class IntentAction(StrEnum):
    """The UI affordances (§8): iterate, accept-and-advance, opt out — and undo the opt-out.

    ``unskip`` is the inverse of ``skip``: it restores the status the stage held before it was
    skipped, without re-running the stage. The conductor answers it itself (no stage handler runs),
    so an accidental skip costs a click to undo rather than a full agent run.
    """

    refine = "refine"
    proceed = "proceed"
    skip = "skip"
    unskip = "unskip"


class IntentRequest(BaseModel):
    """``POST /projects/{id}/intent`` body — project id comes from the path."""

    stage: Stage
    action: IntentAction
    message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class Intent(BaseModel):
    """A fully-resolved user intent handed to the conductor."""

    project_id: PydanticObjectId
    stage: Stage
    action: IntentAction
    message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_request(cls, project_id: PydanticObjectId, body: IntentRequest) -> Intent:
        return cls(
            project_id=project_id,
            stage=body.stage,
            action=body.action,
            message=body.message,
            payload=body.payload,
        )

    @property
    def is_approval(self) -> bool:
        """A human *accepting* the stage as it stands, rather than asking for more work.

        Carried on the payload rather than as its own :class:`IntentAction` because it is a variant
        of "accept this stage" (``proceed``), not a further affordance — the same shape as the build
        stage's other payload options (``fresh``, ``scope``). Defined once here because two readers
        must agree: the conductor (which must not detach it as a build, or charge it) and the stage
        handler (which must not run the agent for it).
        """
        return self.action is IntentAction.proceed and bool(self.payload.get("approve"))


class OutboundMessage(BaseModel):
    """A message a stage handler wants persisted (usually the assistant's reply)."""

    role: MessageRole = MessageRole.assistant
    content: str


class ArtifactSpec(BaseModel):
    """An artifact a stage handler wants versioned + stored by the conductor."""

    stage: Stage
    type: ArtifactType
    text: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class StageResult(BaseModel):
    """What a stage handler returns; the conductor persists it and applies the transition.

    ``next_status`` optionally overrides the status the state machine would set (e.g. a real
    handler that needs more input can return ``awaiting_user``). ``events`` are extra realtime
    events to emit beyond the ``stage.transition`` the conductor always emits.
    """

    messages: list[OutboundMessage] = Field(default_factory=list)
    artifacts: list[ArtifactSpec] = Field(default_factory=list)
    next_status: StageStatus | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)


class IntentResponse(BaseModel):
    """Summary of a conductor run, returned by the intent endpoint.

    A detached stage (build) answers as soon as the work is *accepted*, so ``messages`` and
    ``artifacts`` are empty and ``to_status`` is ``in_progress``; the outcome arrives over the
    realtime channel and via ``GET /projects/{id}/activity``.
    """

    stage: Stage
    action: IntentAction
    from_status: StageStatus
    to_status: StageStatus
    stale: list[Stage]
    messages: list[MessagePublic]
    artifacts: list[ArtifactPublic]
    run_id: str


class BuildActivityPublic(BaseModel):
    """A build that is running right now, as a reloaded page needs to see it."""

    run_id: str
    started_at: datetime
    step: str = ""
    label: str = ""
    target: str = ""
    files: list[str] = Field(default_factory=list)
    # The phase in flight (phase-56), so a reload reattaches to the right row of the phase rail.
    phase_index: int = 0
    phase_total: int = 0
    phase_id: str = ""


class ActivityPublic(BaseModel):
    """Long-running work in flight for a project. ``build`` is ``None`` when nothing is running."""

    build: BuildActivityPublic | None = None
