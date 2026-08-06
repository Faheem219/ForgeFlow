"""Stage-handler contract (phase-08).

Every stage (requirements/design/build/test/deploy/validate) implements :class:`StageHandler`.
Handlers are **pure orchestration units**: they receive an :class:`Intent` + a :class:`StageContext`
and return a :class:`StageResult` describing what to persist — the conductor does the actual
persistence, transition, and event emission. This keeps stages independently shippable: a real
handler drops in where a stub was, with zero conductor changes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from app.core.config import ConfigResolver
from app.db.models import Project, StageState
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.messages import MessageService
from app.orchestrator.schemas import Intent, StageResult
from app.realtime.schemas import Event


@dataclass
class StageContext:
    """Everything a handler may need. Agent client + sandbox handle are added in later epics."""

    project: Project
    stage_state: StageState
    messages: MessageService
    artifacts: ArtifactService
    config: ConfigResolver
    emit: Callable[..., Awaitable[Event]]


class StageHandler(Protocol):
    async def handle(self, intent: Intent, ctx: StageContext) -> StageResult: ...
