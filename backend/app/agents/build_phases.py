"""Phase planning and phase-by-phase execution (phase-56 tasks 3 & 4).

Two collaborators the codegen agent drives:

- :class:`BuildPlanner` — turns design + requirements into a :class:`~app.agents.build_plan.
  BuildPlan` on **Haiku** (``TaskKind.summarize``, D4 / Golden Rule 7), persists it as a versioned
  ``build_plan`` artifact *and* (by default) into the workspace's ``phase-plan/`` folder, and falls
  back deterministically so a build is never blocked on a missing or unparseable plan (D12).
- :class:`PhaseRunner` — implements **one** phase per model loop with a *small* context, gates it on
  a package-scoped ``pnpm typecheck``, gives it a bounded fix sub-loop, and commits it.

The three wins over one monolithic loop are context size, failure localisation and resumability, and
they compound: a smaller context makes each phase more likely to typecheck first time, and a
per-phase gate hands the repair loop a diff of one phase rather than of the whole app.

A failed phase does **not** abort the build — it is recorded and carried into phase-55's integration
repair, where the diff-aware loop gets a shot with the whole app in view. An *environment* verdict
(phase-55's classifier) is the one thing that does abort: patching source cannot fix a dead sandbox,
so every further phase would be wasted spend.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from beanie import PydanticObjectId

from app.agents.anthropic_client import STOP_MAX_TOKENS, STOP_MAX_TURNS
from app.agents.build_errors import BuildFailureKind, classify_output
from app.agents.build_plan import (
    PHASE_DONE,
    PHASE_FAILED,
    PHASE_RUNNING,
    BuildPhase,
    BuildPlan,
    fallback_plan,
    parse_build_plan,
    render_markdown,
)
from app.agents.models import TaskKind
from app.agents.prompts import system_prompt
from app.agents.prompts.codegen import (
    PHASE_IMPLEMENT_SYSTEM_EXTRA,
    PHASE_PLAN_SYSTEM_EXTRA,
    phase_plan_user_prompt,
    phase_user_prompt,
)
from app.agents.tools.context import ToolContext
from app.agents.tools.registry import ToolDispatch
from app.core.config import get_config
from app.core.errors import NotFoundError, SystemError, UserError  # noqa: A004 - taxonomy name
from app.db.models import Project, Run
from app.db.models.common import utcnow
from app.db.models.enums import ArtifactType, Stage
from app.orchestrator.artifacts import ArtifactService
from app.realtime.hub import emit
from app.realtime.schemas import EventType

BUILD_PLAN_KIND = "build_plan"

PLAN_FILE = "plan.json"
PLAN_README = "README.md"

#: Tools withheld from a *non-wiring* phase's loop, on top of codegen's own exclusions. Installing,
#: booting and testing belong to verification (phase-55); offering them here only burns turns.
_PHASE_EXCLUDED_TOOLS = frozenset({"install_deps", "start_preview", "restart_preview", "run_tests"})

_STORY_MAX_CHARS = 800  # the "small message to give context" — a summary, never a transcript

#: Phase-64 — evidence, not silence. A phase whose loop finished without a single `write_file`
#: used to typecheck trivially (the workspace was unchanged) and be recorded `done`; complex apps
#: then shipped as the untouched template with a report claiming every phase complete. This
#: corrective turn carries the one thing the failed attempt lacked: an explicit statement that the
#: output must be files. Prompt surface: bump ``PROMPT_VERSION`` when it changes.
NOOP_FEEDBACK = (
    "This phase produced NO files — nothing was written to the workspace. It is not done. "
    "It owns:\n{files}\nGoal: {goal}\n"
    "Write them now with `write_file`, one file per call, the smallest complete version first. "
    "Do not summarise, plan or re-read what you have already read: write."
)
NOTE_WROTE_NOTHING = "wrote nothing"
NOTE_NO_SUMMARY = "no closing summary"


@dataclass
class PhaseOutcome:
    """What one phase actually did — the durable record resume and the UI read."""

    id: str
    title: str
    kind: str
    status: str
    files: list[str] = field(default_factory=list)
    commit: str | None = None
    attempts: int = 0
    note: str = ""
    summary: str = ""  # the model's own closing line for this phase

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "status": self.status,
            "files": list(self.files),
            "commit": self.commit,
            "attempts": self.attempts,
            "note": self.note,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PhaseOutcome:
        return cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            kind=str(data.get("kind", "backend")),
            status=str(data.get("status", "pending")),
            files=[str(f) for f in data.get("files", []) if isinstance(f, str)],
            commit=data.get("commit") if isinstance(data.get("commit"), str) else None,
            attempts=int(data.get("attempts", 0) or 0),
            note=str(data.get("note", "")),
            summary=str(data.get("summary", "")),
        )


class BuildPlanAborted(Exception):
    """The sandbox, not the code, is broken — stop the build rather than spend on more phases."""

    def __init__(self, reason: str, hint: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.hint = hint


# --------------------------------------------------------------------- planner


class BuildPlanner:
    """Produce, validate and persist the phase plan."""

    def __init__(self, client: Any, artifacts: ArtifactService | None = None) -> None:
        self._client = client
        self._artifacts = artifacts or ArtifactService()

    async def plan(
        self,
        project: Project,
        run: Run,
        *,
        ctx: ToolContext,
        channel: str,
        design: str | None,
        requirements: str | None,
        features: list[str],
        existing: str | None = None,
        instruction: str | None = None,
    ) -> BuildPlan:
        project_id = project.id
        assert project_id is not None
        config = get_config()
        max_phases = int(config.get("build_max_phases"))

        plan: BuildPlan | None = None
        try:
            result = await self._client.run_tool_loop(
                task_kind=TaskKind.summarize,  # MODEL_ROUTING (Haiku) — planning stays cheap
                project_id=project_id,
                run=run,
                system=system_prompt(PHASE_PLAN_SYSTEM_EXTRA),
                messages=[
                    {
                        "role": "user",
                        "content": phase_plan_user_prompt(
                            project.name,
                            design,
                            requirements,
                            existing,
                            max_phases=max_phases,
                            instruction=instruction,
                        ),
                    }
                ],
                channel=channel,
            )
            plan = parse_build_plan(result.text)
        except (UserError, NotFoundError):
            plan = None  # a plan the model could not produce is not a reason to block the build

        if plan is not None:
            plan = plan.validate(max_phases=max_phases)
        if plan is None or not plan.phases:
            # Mandatory, not defensive: this is what satisfies D12 (build works with neither
            # requirements nor design).
            plan = fallback_plan(features, has_design=bool(design)).validate(max_phases=max_phases)

        await self.persist(project_id, plan)
        await self.write_workspace(project, ctx, plan, {})
        return plan

    async def persist(self, project_id: PydanticObjectId, plan: BuildPlan) -> None:
        """Version the plan as an artifact — a new ``meta.kind`` under the existing type (§7).

        ``ArtifactType`` is fixed by the implementation plan, so a new kind is the compatible move.
        The version counter is shared with ``build_report`` (counters are per project/stage/type),
        so each sequence shows gaps; harmless, and `get_latest_of_kind` reads past them.
        """
        await self._artifacts.create_version(
            project_id,
            Stage.build,
            ArtifactType.code_change,
            text=plan.to_json(),
            meta={
                "kind": BUILD_PLAN_KIND,
                "phase_count": len(plan.phases),
                "phase_ids": [p.id for p in plan.phases],
                "source": plan.source,
            },
        )

    async def write_workspace(
        self,
        project: Project,
        ctx: ToolContext,
        plan: BuildPlan,
        outcomes: dict[str, str],
    ) -> None:
        """Mirror the plan into ``<build_plan_dir>/`` — what the user asked to see in the IDE.

        Written through ``ctx.workspace.write`` so each rewrite emits ``fs.write`` and streams live
        into the editor tree. Rewritten after every phase, so the checklist visibly fills in. Both
        files come from ONE object, so they can never disagree.
        """
        if not bool(get_config().get("build_plan_in_workspace")):
            return
        folder = str(get_config().get("build_plan_dir")).strip("/") or "phase-plan"
        payload = plan.to_dict()
        payload["outcomes"] = outcomes
        try:
            await ctx.workspace.write(
                project, f"{folder}/{PLAN_FILE}", json.dumps(payload, indent=2)
            )
            await ctx.workspace.write(
                project, f"{folder}/{PLAN_README}", render_markdown(plan, outcomes)
            )
        except (UserError, NotFoundError):
            # The plan is durable in the artifact store regardless; a workspace write failure must
            # never take the build down with it.
            pass


# --------------------------------------------------------------------- runner


class PhaseRunner:
    """Implement one phase: a small loop, a scoped typecheck gate, a bounded fix loop, a commit."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def run(
        self,
        project: Project,
        run: Run,
        phase: BuildPhase,
        *,
        ctx: ToolContext,
        channel: str,
        dispatch: ToolDispatch,
        tools: list[dict[str, Any]],
        index: int,
        total: int,
        project_name: str,
        design: str | None,
        requirements: str | None,
        existing: str | None,
        story_so_far: str,
        notes: list[str] | None = None,
        files_before: set[str] | None = None,
        typecheck: Any = None,
    ) -> PhaseOutcome:
        project_id = project.id
        assert project_id is not None
        config = get_config()
        outcome = PhaseOutcome(
            id=phase.id, title=phase.title, kind=phase.kind, status=PHASE_RUNNING
        )

        await self._announce(channel, run, phase, index, total, PHASE_RUNNING)

        seen_before = set(files_before or set())
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": phase_user_prompt(
                    project_name,
                    index,
                    total,
                    phase.title,
                    phase.kind,
                    phase.goal,
                    phase.files,
                    phase.done_when,
                    story_so_far,
                    design,
                    requirements,
                    existing,
                    notes,
                ),
            }
        ]
        result = await self._client.run_tool_loop(
            task_kind=TaskKind.codegen,
            project_id=project_id,
            run=run,
            system=system_prompt(PHASE_IMPLEMENT_SYSTEM_EXTRA),
            messages=messages,
            tools=self._tools_for(phase, tools),
            tool_dispatch=dispatch,
            channel=channel,
            max_turns=int(config.get("build_phase_max_tool_turns")),
        )
        outcome.attempts = 1
        conversation = list(result.messages)

        # Evidence gate (phase-64): a loop that FINISHED without writing anything is not a phase
        # that is done — it is the exact shape of a model that ran out of output budget while
        # thinking, or answered in prose instead of tool calls. Feed that back, bounded, and only
        # then judge. The typecheck below is skipped for a no-op phase: there is nothing to check,
        # and running it would only manufacture a green signal from an unchanged workspace.
        noop_retries = int(config.get("build_phase_noop_retries"))
        while result.finished and not self._wrote(run, seen_before) and noop_retries > 0:
            noop_retries -= 1
            conversation.append(
                {
                    "role": "user",
                    "content": NOOP_FEEDBACK.format(
                        files="\n".join(f"- {f}" for f in phase.files) or "- (see goal)",
                        goal=phase.goal,
                    ),
                }
            )
            result = await self._client.run_tool_loop(
                task_kind=TaskKind.codegen,
                project_id=project_id,
                run=run,
                system=system_prompt(PHASE_IMPLEMENT_SYSTEM_EXTRA),
                messages=conversation,
                tools=self._tools_for(phase, tools),
                tool_dispatch=dispatch,
                channel=channel,
                max_turns=int(config.get("build_phase_max_tool_turns")),
            )
            conversation = list(result.messages)
            outcome.attempts += 1
        wrote = self._wrote(run, seen_before)

        # Gate: typecheck scoped to this phase's package, so a red frontend cannot fail a backend
        # phase (and the errors handed back are only ever about the code just written).
        max_fixes = int(config.get("build_phase_max_fix_attempts"))
        probe = await self._typecheck(project, ctx, phase, typecheck) if wrote else None
        while probe is not None and probe.exit_code != 0 and outcome.attempts <= max_fixes:
            # Classify BEFORE feeding anything back (phase-55's discipline): a broken sandbox is not
            # something more model turns can fix, and every further phase would be wasted spend.
            diagnosis = classify_output(
                probe.output, exit_code=probe.exit_code, timed_out=probe.timed_out
            )
            if diagnosis.kind is not BuildFailureKind.code:
                raise BuildPlanAborted(diagnosis.reason, diagnosis.hint)
            conversation.append(
                {
                    "role": "user",
                    "content": (
                        "The typecheck for this phase failed. Fix ONLY these errors — do not start "
                        "any later phase's work:\n\n" + _trim(probe.output)
                    ),
                }
            )
            result = await self._client.run_tool_loop(
                task_kind=TaskKind.codegen,
                project_id=project_id,
                run=run,
                system=system_prompt(PHASE_IMPLEMENT_SYSTEM_EXTRA),
                messages=conversation,
                tools=self._tools_for(phase, tools),
                tool_dispatch=dispatch,
                channel=channel,
                max_turns=int(config.get("build_phase_max_tool_turns")),
            )
            conversation = list(result.messages)
            outcome.attempts += 1
            probe = await self._typecheck(project, ctx, phase, typecheck)

        green = probe is None or probe.exit_code == 0
        # The closing line is evidence too (phase-64). A loop that *finished* always carries the
        # model's last words (the client nudges an empty or cut-off turn and continues, so it
        # cannot end on silence). The remaining case is a model that wrote its files and then
        # produced nothing usable through every nudge: the loop stops with `max_tokens`. With a
        # summary required (the default) that phase is failed — the silence is exactly the signal
        # that would have exposed the original bug; with it optional, the closing line is
        # synthesised from the files and the phase can be done.
        summary = result.text.strip()
        finished = result.finished
        silent = wrote and result.stopped == STOP_MAX_TOKENS
        if silent and not bool(config.get("build_phase_require_summary")):
            files_now = sorted(set(run.progress.files) - seen_before)
            summary = summary or f"wrote {len(files_now)} file(s): " + ", ".join(files_now)
            finished = True

        # `done` ⟺ green ∧ finished ∧ wrote ∧ summary. A phase that ran out of turns (or began
        # repeating itself, or kept hitting the output cap) has NOT finished, even if what it did
        # write happens to typecheck — half a feature compiles perfectly well. And a phase that
        # wrote nothing has nothing to typecheck: an unchanged workspace is not evidence.
        outcome.summary = summary
        outcome.status = (
            PHASE_DONE if (green and finished and wrote and bool(summary)) else PHASE_FAILED
        )
        if not wrote:
            outcome.note = f"{NOTE_WROTE_NOTHING} after {outcome.attempts} attempt(s)"
            if not result.finished:
                outcome.note += f" ({self._stop_note(result)})"
        elif not green:
            # Carried, not fatal: phase-55's integration repair sees the whole app and may well fix
            # what one phase's narrow view could not.
            outcome.note = f"typecheck still failing after {outcome.attempts} attempt(s)"
        elif silent and not finished:
            outcome.note = f"{NOTE_NO_SUMMARY} — the model produced nothing after its last write"
        elif not finished:
            outcome.note = self._stop_note(result)

        # One commit per phase, so `changed_paths` and every diff are phase-attributable.
        try:
            sha, _ = await ctx.workspace.commit(project, f"build({phase.kind}): {phase.title}")
            outcome.commit = sha
        except (UserError, NotFoundError):
            outcome.commit = None

        outcome.files = sorted(set(run.progress.files) - seen_before)
        await self._announce(channel, run, phase, index, total, outcome.status)
        return outcome

    # -- helpers ------------------------------------------------------------------------

    @staticmethod
    def _wrote(run: Run, seen_before: set[str]) -> bool:
        """Whether this phase has written at least one file the run had not seen before it."""
        return bool(set(run.progress.files) - seen_before)

    @staticmethod
    def _stop_note(result: Any) -> str:
        if result.stopped == STOP_MAX_TURNS:
            return f"stopped after {result.turns} tool turns (its safety bound)"
        if result.stopped == STOP_MAX_TOKENS:
            return "stopped after repeatedly running out of output budget (max_tokens)"
        return "stopped because it began repeating the same action"

    def _tools_for(self, phase: BuildPhase, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Narrow the surface for non-`wiring` phases (task 4.2)."""
        if phase.kind == "wiring":
            return tools
        return [t for t in tools if t.get("name") not in _PHASE_EXCLUDED_TOOLS]

    async def _typecheck(
        self, project: Project, ctx: ToolContext, phase: BuildPhase, typecheck: Any
    ) -> Any:
        step = typecheck or _default_phase_typecheck
        try:
            return await step(project, ctx, phase)
        except (UserError, NotFoundError, SystemError):
            # The gate could not run at all (no sandbox, a dead container, a unit test with no
            # exec service). That is not evidence the phase is broken, and it is not this loop's
            # job to diagnose it: phase-55's verification runs next and classifies it properly as
            # `environment`. Failing the phase here would blame the code for the sandbox.
            return None

    async def _announce(
        self, channel: str, run: Run, phase: BuildPhase, index: int, total: int, status: str
    ) -> None:
        await emit(
            channel,
            EventType.build_phase,
            {
                "stage": "build",
                "phase_id": phase.id,
                "index": index,
                "total": total,
                "title": phase.title,
                "kind": phase.kind,
                "status": status,
            },
            stage=Stage.build,
        )
        # Mirrored onto the Run so a reload reattaches to the right phase (the realtime ring is
        # bounded and a build's traffic evicts it many times over).
        run.progress.step = "phase"
        run.progress.label = f"Phase {index}/{total}: {phase.title}"
        run.progress.phase_index = index
        run.progress.phase_total = total
        run.progress.phase_id = phase.id
        run.progress.updated_at = utcnow()
        await run.save()


def story_so_far(outcomes: list[PhaseOutcome]) -> str:
    """A ≤400-char summary of previous phases — the "small message to give context"."""
    if not outcomes:
        return ""
    parts = [f"{o.title} ({o.status})" for o in outcomes]
    text = "; ".join(parts)
    if len(text) <= _STORY_MAX_CHARS:
        return text
    return text[: _STORY_MAX_CHARS - 1].rsplit(";", 1)[0] + "; …"


def _trim(output: str, limit: int = 8000) -> str:
    """The tail of a command's output — the errors are at the end, and the head is noise."""
    return output if len(output) <= limit else "…\n" + output[-limit:]


async def _default_phase_typecheck(project: Project, ctx: ToolContext, phase: BuildPhase) -> Any:
    """``pnpm typecheck`` scoped to the phase's package (``--filter``), or the whole workspace."""
    import shlex

    from app.orchestrator.stages.build_verify import StepProbe, _read_output

    cmd = shlex.split(str(get_config().get("build_typecheck_cmd")))
    package = phase.package
    if package and cmd and cmd[0] == "pnpm":
        cmd = [cmd[0], "--filter", package, *cmd[1:]]
    outcome = await ctx.exec_service.run(
        project, cmd, timeout=float(get_config().get("build_typecheck_timeout_s"))
    )
    return StepProbe(
        output=await _read_output(outcome), exit_code=outcome.exit_code, timed_out=outcome.timed_out
    )


__all__ = [
    "BUILD_PLAN_KIND",
    "NOOP_FEEDBACK",
    "NOTE_NO_SUMMARY",
    "NOTE_WROTE_NOTHING",
    "BuildPlanAborted",
    "BuildPlanner",
    "PhaseOutcome",
    "PhaseRunner",
    "story_so_far",
]
