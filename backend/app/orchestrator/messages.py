"""Message service (phase-07): per-project / per-stage conversation history.

This phase is store + retrieval only — the conductor and agent phases produce the actual content
later. A small :func:`token_usage` helper standardizes the usage blob attached to messages/runs,
consumed by ``phase-20`` (Anthropic client) and ``phase-46`` (cost tracking).
"""

from __future__ import annotations

from typing import Any

from beanie import PydanticObjectId

from app.db.models import Message
from app.db.models.enums import MessageRole, Stage
from app.db.repos import MessageRepo


def token_usage(
    input_tokens: int = 0,
    output_tokens: int = 0,
    model: str | None = None,
    inr: float = 0.0,
) -> dict[str, Any]:
    """Standard token-usage blob stored on a message (or aggregated onto a run)."""
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "model": model,
        "inr": inr,
    }


class MessageService:
    def __init__(self) -> None:
        self._messages = MessageRepo()

    async def append(
        self,
        project_id: PydanticObjectId,
        role: MessageRole,
        content: str,
        *,
        stage: Stage | None = None,
        artifacts: list[PydanticObjectId] | None = None,
        usage: dict[str, Any] | None = None,
    ) -> Message:
        return await self._messages.insert(
            Message(
                project_id=project_id,
                role=role,
                content=content,
                stage=stage,
                artifacts=artifacts or [],
                token_usage=usage or {},
            )
        )

    async def list_for_project(
        self,
        project_id: PydanticObjectId,
        *,
        stage: Stage | None = None,
        skip: int = 0,
        limit: int | None = None,
    ) -> list[Message]:
        return await self._messages.list_for_project(project_id, stage, skip=skip, limit=limit)

    async def delete_for_project(self, project_id: PydanticObjectId) -> None:
        """Cascade helper: delete every message of a project."""
        for message in await self._messages.list_for_project(project_id):
            await message.delete()
