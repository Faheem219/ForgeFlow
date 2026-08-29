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
  - ``unskip``   → ``unskip`` (restore the status the stage held before it was skipped)
  - ``proceed``  → ``complete`` (accept the stage and advance; honors the hard prereqs)
  - ``refine``   → ``refine`` if the stage is already complete/skipped/stale (a jump-back that
                   stales downstream), else ``enter`` (start/continue an in-flight stage).

``unskip`` is answered by step 6 alone — it is a pure state restore, so no stage handler runs and
no artifact is produced (step 5 is skipped entirely).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from beanie import PydanticObjectId

from app.core.config import get_config
from app.core.errors import ForgeFlowError, UserError
from app.db.models import Run
from app.db.models.common import utcnow
from app.db.models.enums import MessageRole, Stage, StageStatus
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
from app.realtime.schemas import EventType

logger = logging.getLogger(__name__)

_REOPENABLE = {StageStatus.complete, StageStatus.skipped, StageStatus.stale}

#: Statuses in the words the UI shows, so an un-skip reply names the same state the badge does.
_STATUS_WORDS = {
    StageStatus.empty: "not started",
    StageStatus.in_progress: "in progress",
    StageStatus.awaiting_user: "needs you",
    StageStatus.complete: "complete",
    StageStatus.skipped: "skipped",
    StageStatus.stale: "stale",
}

#: Stages whose handler runs far longer than a request should be held open. Their work is detached
#: so it survives the client that started it (a refresh, a proxy timeout) and is followed over the
#: realtime channel instead.
_DETACHED_STAGES = {Stage.build}

#: Run kinds that mean "a build is in flight" — the conductor's own trace and the codegen agent's.
BUILD_RUN_KINDS = ("conductor:build", "codegen:build")

#: Strong refs to detached tasks; asyncio only holds weak ones, so without this they can vanish
#: mid-build with no error anywhere.
_DETACHED: set[asyncio.Task[None]] = set()


def _runs_detached(intent: Intent, action: Action) -> bool:
    """Whether this intent should be answered immediately and finished in the background.

    Only the actions that actually start an agent run qualify. The ones that merely move the stage
    marker — skip, unskip, and a human *approval* of a build — do no work worth detaching, and
    detaching them would make a synchronous answer look like a build in flight (and take the
    concurrent-build lock with it).
    """
    if intent.stage not in _DETACHED_STAGES:
        return False
    if action not in (Action.enter, Action.complete, Action.refine):
        return False
    return not intent.is_approval


async def reap_orphaned_runs() -> int:
    """Close **every** run left open by a previous process. Returns how many were reaped.

    Runs live in the API process, so any still-open run at startup belongs to a process that no
    longer exists — it cannot still be running. Left alone each reads as work in flight forever: the
    cost panel lists it as ``running`` indefinitely (which is how a closed app looked like it had
    lost the work), and for a *build* the concurrent-build guard would lock the project out of ever
    building again. Called once from the app lifespan.
    """
    reaped = 0
    for run in await Run.find({"finished_at": None}).to_list():
        run.finished_at = utcnow()
        if run.progress.step not in ("", "report"):
            run.progress.step = "failed"
            run.progress.label = "Interrupted by a restart"
        await run.save()
        reaped += 1
    if reaped:
        logger.info("reaped %d orphaned run(s) from a previous process", reaped)
    return reaped


async def drain_detached() -> None:
    """Await every detached stage task currently in flight.

    Detached work outlives the response by design, which leaves tests (and a graceful shutdown) with
    nothing to await. This is that join point.
    """
    while _DETACHED:
        await asyncio.gather(*list(_DETACHED), return_exceptions=True)


def _resolve_action(action: IntentAction, current: StageStatus) -> Action:
    if action is IntentAction.skip:
        return Action.skip
    if action is IntentAction.unskip:
        return Action.unskip
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

        # Refuse to start a second build on top of one already running: they share one workspace
        # and would race on the same files. The open `Run` is the durable lock.
        if _runs_detached(intent, action):
            active = await self._runs.active_for_project(intent.project_id, BUILD_RUN_KINDS)
            if active:
                raise UserError(
                    "A build is already running for this project — wait for it to finish, "
                    "or reload to follow its progress."
                )

        run = await self._runs.insert(
            Run(project_id=intent.project_id, kind=f"conductor:{intent.stage}")
        )
        stage_state = await self._stages.get_or_create(intent.project_id, intent.stage)

        # Publish "this stage is working" BEFORE the handler runs. A build holds this request
        # open for minutes, and until now the stage stayed `empty` for all of it — the UI (and
        # any second viewer) showed "Not started" while the agent was plainly building.
        #
        # Only for enter/complete: `refine` requires a complete/skipped/stale status, so writing
        # in_progress first would make the closing transition illegal.
        # An approval does no work, so there is nothing for an in_progress marker to describe — and
        # marking it would let a *rejected* approval (no build to approve) leave the stage moved.
        marked_in_progress = action in (Action.enter, Action.complete) and not intent.is_approval
        if marked_in_progress:
            await self._stages.set_status(intent.project_id, intent.stage, StageStatus.in_progress)
            await self._emit_transition(intent, current, StageStatus.in_progress)

        ctx = StageContext(
            project=project,
            stage_state=stage_state,
            messages=self._messages,
            artifacts=self._artifacts,
            config=get_config(),
            emit=emit,
        )

        # A build runs for minutes. Holding the HTTP request open for it makes the work only as
        # durable as one browser tab: a reload (or any proxy timeout) abandons the response, and
        # the UI loses every trace of a build that is in fact still running. Detach it instead and
        # answer at once — the client follows over the realtime channel and can re-discover the run
        # from its `Run` document after a refresh. NOTE: the detached path owns closing `run`.
        if _runs_detached(intent, action):
            self._spawn(
                self._execute_detached(
                    user_id, intent, ctx, action, run, marked_in_progress=marked_in_progress
                )
            )
            return IntentResponse(
                stage=intent.stage,
                action=intent.action,
                from_status=current,
                to_status=StageStatus.in_progress,
                stale=[],
                messages=[],
                artifacts=[],
                run_id=str(run.id),
            )

        failure: str | None = None
        try:
            return await self._execute(
                user_id, intent, ctx, action, current, run, marked_in_progress
            )
        except Exception as exc:
            failure = str(exc) or exc.__class__.__name__
            raise
        finally:
            await self._close_run(run, intent, failure)

    # -- execution ---------------------------------------------------------------------------

    async def _execute(
        self,
        user_id: PydanticObjectId,
        intent: Intent,
        ctx: StageContext,
        action: Action,
        current: StageStatus,
        run: Run,
        marked_in_progress: bool,
    ) -> IntentResponse:
        """Dispatch to the stage handler, then persist everything it produced."""
        if action is Action.unskip:
            return await self._unskip(user_id, intent, current, run)

        try:
            result = await get_handler(intent.stage).handle(intent, ctx)
        except Exception as exc:
            # A failed handler must not strand the stage in `in_progress` forever — that reads as
            # "still working" and blocks the user from retrying with a clear head.
            await self._release(intent, marked_in_progress)
            # The user's message is already persisted, so without a reply the conversation records
            # a request that was apparently ignored — the reason lived only in a transient toast,
            # and after a reload the work looked silently discarded. Record it instead.
            await self._note_failure(intent, exc)
            raise

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
            # The status the stage held when the user acted. `outcome.from_status` is re-read after
            # the early in_progress mark above, so it would report that instead.
            from_status=current,
            to_status=to_status,
            stale=list(outcome.stale),
            messages=[MessageOut.from_message(m) for m in persisted],
            artifacts=[ArtifactOut.from_artifact(a) for a in created],
            run_id=str(run.id),
        )

    async def _execute_detached(
        self,
        user_id: PydanticObjectId,
        intent: Intent,
        ctx: StageContext,
        action: Action,
        run: Run,
        *,
        marked_in_progress: bool,
    ) -> None:
        """Run a long stage after the response has gone out.

        Nothing may escape to the event loop from here — there is no caller left to catch it.
        """
        failure: str | None = None
        try:
            await self._execute(
                user_id, intent, ctx, action, StageStatus.in_progress, run, marked_in_progress
            )
        except Exception as exc:  # noqa: BLE001 - a detached task has nobody to raise to
            failure = str(exc) or exc.__class__.__name__
            logger.warning("detached %s stage failed", intent.stage, exc_info=True)
            # `_execute` already reset the stage; tell the client why, since there is no response
            # left to carry the error.
            await emit(
                str(intent.project_id),
                EventType.progress,
                {"stage": str(intent.stage), "step": "failed", "error": str(exc)},
                stage=intent.stage,
            )
        finally:
            # Closing the Run is what flips "a build is running" off, so it must happen whatever
            # else did — otherwise the project is locked out of building forever.
            await self._close_run(run, intent, failure)

    async def _unskip(
        self,
        user_id: PydanticObjectId,
        intent: Intent,
        current: StageStatus,
        run: Run,
    ) -> IntentResponse:
        """Undo a skip: put the stage back to the status it held, with no handler run.

        Deliberately *not* routed through a stage handler. Un-skipping is an undo, not new work:
        re-entering the build stage would mean paying for a whole rebuild of an app that is already
        on disk and already passed verification — which is exactly the trap an accidental skip used
        to leave the user in.
        """
        outcome = await self._projects.transition_stage(
            intent.project_id, user_id, intent.stage, Action.unskip
        )
        restored = (
            f"restored to {_STATUS_WORDS[outcome.to_status]}"
            if outcome.to_status is not StageStatus.empty
            else "it had not been started before the skip, so it is back to not started"
        )
        message = await self._messages.append(
            intent.project_id,
            MessageRole.assistant,
            f"{intent.stage.value.capitalize()} is no longer skipped — {restored}. "
            "Nothing was re-run.",
            stage=intent.stage,
        )
        return IntentResponse(
            stage=intent.stage,
            action=intent.action,
            from_status=current,
            to_status=outcome.to_status,
            stale=list(outcome.stale),
            messages=[MessageOut.from_message(message)],
            artifacts=[],
            run_id=str(run.id),
        )

    # -- helpers -----------------------------------------------------------------------------

    async def _close_run(self, run: Run, intent: Intent, failure: str | None) -> None:
        """Close a run and broadcast its terminal state.

        The event matters most on the **detached** path: there the HTTP response went out before
        the work began, so it carries no completion signal at all and the realtime channel is the
        only thing that can say "this finished". On the synchronous path it is the safety net for a
        client whose connection a proxy dropped mid-request — the work still completed, and the UI
        should stop claiming otherwise. Emitted whatever happened, success or failure.
        """
        run.finished_at = utcnow()
        await run.save()
        await emit(
            str(intent.project_id),
            EventType.run_finished,
            {
                "run_id": str(run.id),
                "kind": run.kind,
                "stage": str(intent.stage),
                "ok": failure is None,
                "error": failure,
            },
            stage=intent.stage,
        )

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        """Fire a detached task, holding a strong reference so it is not garbage-collected."""
        task = asyncio.create_task(coro)
        _DETACHED.add(task)
        task.add_done_callback(_DETACHED.discard)

    async def _emit_transition(
        self, intent: Intent, from_status: StageStatus, to_status: StageStatus
    ) -> None:
        await emit(
            str(intent.project_id),
            EventType.stage_transition,
            {
                "stage": str(intent.stage),
                "from": str(from_status),
                "to": str(to_status),
                "stale": [],
            },
            stage=intent.stage,
        )

    async def _release(self, intent: Intent, marked_in_progress: bool) -> None:
        """Hand a failed stage back to the user instead of leaving it stuck `in_progress`."""
        if not marked_in_progress:
            return
        await self._stages.set_status(intent.project_id, intent.stage, StageStatus.awaiting_user)
        await self._emit_transition(intent, StageStatus.in_progress, StageStatus.awaiting_user)

    async def _note_failure(self, intent: Intent, exc: BaseException) -> None:
        """Persist an assistant reply saying the request failed, and why.

        The conversation is the project's durable record: an HTTP error alone disappears with the
        toast that showed it, leaving the user's message standing there unanswered. Only the
        taxonomy's user-facing text is used — an unexpected exception is summarised, never dumped,
        so internals (and anything they might carry) stay out of the transcript.
        """
        if isinstance(exc, ForgeFlowError):
            content = f"That didn't go through: {exc.message}"
            hint = getattr(exc, "fallback_hint", None)
            if isinstance(hint, str) and hint:
                content = f"{content}\n\n{hint}"
        else:
            content = (
                "That didn't go through — something went wrong on our side. "
                "Nothing was changed; try again."
            )
        try:
            await self._messages.append(
                intent.project_id, MessageRole.assistant, content, stage=intent.stage
            )
        except Exception:  # never let bookkeeping replace the original failure
            logger.warning("could not record the failure reply", exc_info=True)
