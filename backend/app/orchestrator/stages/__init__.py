"""Stage-handler registry (phase-08).

Maps each stage to its handler. This phase registers **no-op stubs** so the conductor's routing,
legality, persistence, and event machinery is fully exercisable before real stage logic lands.
Real handlers replace stubs via :func:`register_handler` in their own epics — the conductor never
changes.
"""

from __future__ import annotations

from app.core.errors import SystemError  # noqa: A004 - taxonomy name fixed by the plan
from app.db.models.enums import ArtifactType, MessageRole, Stage
from app.orchestrator.schemas import ArtifactSpec, Intent, OutboundMessage, StageResult
from app.orchestrator.stages.base import StageContext, StageHandler

# Which artifact type a stage's stub emits (a plausible placeholder per stage).
_ARTIFACT_FOR_STAGE: dict[Stage, ArtifactType] = {
    Stage.design: ArtifactType.design,
    Stage.requirements: ArtifactType.requirement,
    Stage.build: ArtifactType.code_change,
    Stage.test: ArtifactType.test,
    Stage.deploy: ArtifactType.deployment,
    Stage.validate: ArtifactType.test_result,
}


class StubStageHandler:
    """Echoes the intent and produces one trivial artifact — a stand-in for real stage logic."""

    def __init__(self, stage: Stage) -> None:
        self.stage = stage

    async def handle(self, intent: Intent, ctx: StageContext) -> StageResult:
        summary = f"[{self.stage} stub] handled action={intent.action}"
        if intent.message:
            summary += f" · {intent.message}"
        return StageResult(
            messages=[OutboundMessage(role=MessageRole.assistant, content=summary)],
            artifacts=[
                ArtifactSpec(
                    stage=self.stage,
                    type=_ARTIFACT_FOR_STAGE[self.stage],
                    text=summary,
                    meta={"stub": True, "action": str(intent.action)},
                )
            ],
        )


_HANDLERS: dict[Stage, StageHandler] = {stage: StubStageHandler(stage) for stage in Stage}


def get_handler(stage: Stage) -> StageHandler:
    handler = _HANDLERS.get(stage)
    if handler is None:  # pragma: no cover - every Stage is registered above
        raise SystemError(f"No handler registered for stage: {stage}")
    return handler


def register_handler(stage: Stage, handler: StageHandler) -> None:
    """Replace a stage's handler (used by later epics to swap in real logic)."""
    _HANDLERS[stage] = handler


def _register_real_handlers() -> None:
    """Swap real handlers in for their stubs as each stage lands. Imported here (not at top) so a
    handler module can import ``stages.base`` without a circular import through this package."""
    from app.orchestrator.stages.build import BuildStageHandler
    from app.orchestrator.stages.deploy import DeployStageHandler
    from app.orchestrator.stages.design import DesignStageHandler
    from app.orchestrator.stages.validate import ValidateStageHandler

    register_handler(Stage.design, DesignStageHandler())
    register_handler(Stage.build, BuildStageHandler())
    register_handler(Stage.deploy, DeployStageHandler())
    register_handler(Stage.validate, ValidateStageHandler())


_register_real_handlers()
