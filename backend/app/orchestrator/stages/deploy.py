"""Deploy stage handler (phase-37) — wraps the orchestrator as a steerable, conversational stage.

Action mapping (§8, D12):

- ``proceed`` / ``refine`` — **deploy** (or re-deploy). Runs the orchestrator: DB → BE → FE →
  health, recording a ``Deployment``. A live result leaves the stage ``complete`` (so validate's
  hard prereq is satisfiable); a degraded/failed result leaves it ``awaiting_user`` so it is never
  mistaken for a healthy deployment. Initial deploy and re-deploy share ONE path; the conductor
  resolves a ``refine`` over a complete stage to the ``refine`` action, so it stales validate.
- ``skip`` — opt out; the stage is marked ``skipped`` and validate stays blocked.

The conductor enforces the only hard prereq (deploy⇐build) *before* this handler runs, so a
``proceed`` here is guaranteed a completed build. Deploy runs on its **own** ``Run`` (kind
``deploy``) so its trace/cost is a distinct, eval-readable unit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.db.models import Deployment, Run
from app.db.models.common import utcnow
from app.db.models.enums import DeployMode, MessageRole, StageStatus
from app.db.repos import RunRepo
from app.deploy.orchestrate import STATUS_LIVE, DeployOrchestrator
from app.orchestrator.schemas import Intent, IntentAction, OutboundMessage, StageResult

if TYPE_CHECKING:  # avoid an import cycle: stages/__init__ imports this module
    from app.core.config import ConfigResolver
    from app.orchestrator.stages.base import StageContext


class DeployStageHandler:
    def __init__(self, orchestrator: DeployOrchestrator | None = None) -> None:
        self._orchestrator = orchestrator
        self._runs = RunRepo()

    async def handle(self, intent: Intent, ctx: StageContext) -> StageResult:
        if intent.action is IntentAction.skip:
            return StageResult(
                messages=[
                    OutboundMessage(
                        role=MessageRole.assistant,
                        content="Deploy skipped — validate stays blocked until a deploy is live.",
                    )
                ]
            )

        project = ctx.project
        project_id = project.id
        if project_id is None:  # pragma: no cover - a persisted project always carries an id
            from app.core.errors import UserError

            raise UserError("Project is not persisted")

        mode = _resolve_mode(intent, ctx.config)
        orchestrator = self._orchestrator or DeployOrchestrator()

        # Deploy is its own costed Run (kept distinct from the conductor's intent trace).
        run = await self._runs.insert(Run(project_id=project_id, kind="deploy"))
        try:
            deployment = await orchestrator.deploy(project, mode=mode, user_id=project.user_id)
        finally:
            run.finished_at = utcnow()
            await run.save()

        next_status = (
            StageStatus.complete if deployment.status == STATUS_LIVE else StageStatus.awaiting_user
        )
        return StageResult(
            messages=[OutboundMessage(role=MessageRole.assistant, content=_summarize(deployment))],
            next_status=next_status,
            events=[
                {
                    "event": "progress",
                    "payload": {
                        "stage": "deploy",
                        "status": str(next_status),
                        "deploy_status": deployment.status,
                        "urls": deployment.urls,
                        "run_id": str(run.id),
                    },
                }
            ],
        )


def _resolve_mode(intent: Intent, config: ConfigResolver) -> DeployMode:
    raw = str(intent.payload.get("mode") or config.get("deploy_default_mode")).strip().lower()
    return DeployMode.byo if raw == DeployMode.byo.value else DeployMode.seamless


def _summarize(deployment: Deployment) -> str:
    lines = [f"Deploy {deployment.status} (mode: {deployment.mode})."]
    if deployment.urls.get("fe"):
        lines.append(f"Frontend: {deployment.urls['fe']}")
    if deployment.urls.get("be"):
        lines.append(f"Backend: {deployment.urls['be']}")
    if deployment.status != STATUS_LIVE:
        lines.append("Not fully live yet — check the step logs and retry, or open the topology.")
    return "\n".join(lines)
