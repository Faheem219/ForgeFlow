from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.db.models import Artifact, Message
from app.db.models.enums import ArtifactType, MessageRole, Stage


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
