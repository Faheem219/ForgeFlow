"""The Conductor (phase-08): the single owner of transition legality and intent routing (§8).

Flow for one intent:

1. Resolve ownership; snapshot stage statuses.
2. Map the intent's UI action → a state-machine action and **reject illegal transitions before
   any side effect** (``UserError``).
3. Persist the inbound user message.
4. Open a ``Run`` (cost hook; zero cost for stubs, real accrual lands in phase-20).
5. Dispatch to the stage handler; persist its messages + versioned artifacts.
6. Apply the state transition (phase-06) — which also emits ``stage.transition`` (phase-05).
7. Close the ``Run``.

Intent → state-machine action mapping:
  - ``skip``     → ``skip``
  - ``proceed``  → ``complete`` (accept the stage and advance; honors the hard prereqs)
  - ``refine``   → ``refine`` if the stage is already complete/skipped/stale (a jump-back that
                   stales downstream), else ``enter`` (start/continue an in-flight stage).
"""

from __future__ import annotations

from beanie import PydanticObjectId

from app.core.config import get_config
from app.core.errors import UserError
from app.db.models import Run
from app.db.models.common import utcnow
from app.db.models.enums import MessageRole, StageStatus
from app.db.repos import RunRepo, StageStateRepo
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.messages import MessageService
from app.orchestrator.schemas import (
    ArtifactPublic as ArtifactOut,
)
from app.orchestrator.schemas import (
    Intent,
    IntentAction,
    IntentResponse,
)
from app.orchestrator.schemas import (
    MessagePublic as MessageOut,
)
from app.orchestrator.stages import get_handler
from app.orchestrator.stages.base import StageContext
from app.projects.service import ProjectService
from app.projects.state_machine import Action, can_transition
from app.realtime.hub import emit

_REOPENABLE = {StageStatus.complete, StageStatus.skipped, StageStatus.stale}


def _resolve_action(action: IntentAction, current: StageStatus) -> Action:
    if action is IntentAction.skip:
        return Action.skip
    if action is IntentAction.proceed:
        return Action.complete
    # refine: a jump-back only makes sense once the stage has produced something.
    return Action.refine if current in _REOPENABLE else Action.enter


class Conductor:
    def __init__(self) -> None:
        self._projects = ProjectService()
        self._messages = MessageService()
        self._artifacts = ArtifactService()
        self._stages = StageStateRepo()
        self._runs = RunRepo()

    async def handle_intent(self, user_id: PydanticObjectId, intent: Intent) -> IntentResponse:
        project = await self._projects.get_owned(intent.project_id, user_id)

        states = {s.stage: s.status for s in await self._stages.list_for_project(intent.project_id)}
        current = states.get(intent.stage, StageStatus.empty)
        action = _resolve_action(intent.action, current)

        # Own transition legality — reject before ANY persistence or dispatch.
        legality = can_transition(states, intent.stage, action)
        if not legality.allowed:
            raise UserError(legality.reason or "Illegal stage transition")

        # Inbound user message (only after we know the intent is legal).
        if intent.message:
            await self._messages.append(
                intent.project_id, MessageRole.user, intent.message, stage=intent.stage
            )

        run = await self._runs.insert(
            Run(project_id=intent.project_id, kind=f"conductor:{intent.stage}")
        )
        try:
            stage_state = await self._stages.get_or_create(intent.project_id, intent.stage)
            ctx = StageContext(
                project=project,
                stage_state=stage_state,
                messages=self._messages,
                artifacts=self._artifacts,
                config=get_config(),
                emit=emit,
            )
            result = await get_handler(intent.stage).handle(intent, ctx)

            created = [
                await self._artifacts.create_version(
                    intent.project_id, spec.stage, spec.type, text=spec.text, meta=spec.meta
                )
                for spec in result.artifacts
            ]
            artifact_ids = [a.id for a in created if a.id is not None]

            persisted = [
                await self._messages.append(
                    intent.project_id,
                    m.role,
                    m.content,
                    stage=intent.stage,
                    # Link the round's artifacts to the assistant's reply.
                    artifacts=artifact_ids if m.role is MessageRole.assistant else None,
                )
                for m in result.messages
            ]

            outcome = await self._projects.transition_stage(
                intent.project_id, user_id, intent.stage, action
            )

            to_status = outcome.to_status
            if result.next_status is not None and result.next_status is not outcome.to_status:
                await self._stages.set_status(intent.project_id, intent.stage, result.next_status)
                to_status = result.next_status

            for extra in result.events:
                await emit(
                    str(intent.project_id),
                    str(extra.get("event", "progress")),
                    dict(extra.get("payload", {})),
                    stage=intent.stage,
                )

            return IntentResponse(
                stage=intent.stage,
                action=intent.action,
                from_status=outcome.from_status,
                to_status=to_status,
                stale=list(outcome.stale),
                messages=[MessageOut.from_message(m) for m in persisted],
                artifacts=[ArtifactOut.from_artifact(a) for a in created],
                run_id=str(run.id),
            )
        finally:
            run.finished_at = utcnow()
            await run.save()
