"""How much build a change request actually needs (phase-60).

`phase-56` plans every build into phases and implements them one at a time. That is right for a cold
build — small per-phase context, a typecheck gate each, resumability — and pure overhead for
*"the sign-in button is missing, please fix"*, which is what a user typing into **Refine Build**
usually means. The reported symptom was exactly that: a one-line fix produced a full re-plan and a
phase-by-phase execution.

`CodegenAgent.run` used to plan unconditionally, threading ``instruction`` *into* the planner
(``codegen.py``) rather than using it to decide **whether** to plan — even though the stage handler
already names the distinction (*"A refine carries the change-request instruction; a proceed is the
initial build"*). This module makes that comment true.

**Why a model call and not a heuristic.** *"add dark mode across the whole app"* is also short, also
a refine, and genuinely wants phases. Scope is a property of the request, not of its length. So a
cheap ``TaskKind.classify`` call decides (``MODEL_CLASSIFY``, falling back to ``MODEL_ROUTING``) —
and a deterministic fallback covers every way that call can fail, because a build must never be
blocked on a classifier.

**Cost discipline is strengthened, not relaxed** (Golden Rule 7): one cheap classification replaces
a planning call plus N phase loops on the common case. The expensive model still writes every line
of code, and the small path still runs the *same* phase-55 verification and bounded repair — the
saving is in planning, never in proof.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from beanie import PydanticObjectId

from app.agents.models import TaskKind
from app.core.errors import NotFoundError, UserError
from app.db.models import Run

logger = logging.getLogger(__name__)

#: Keep the classifier's own budget tiny — it answers with one word plus a short reason.
_MAX_REASON_CHARS = 200


class BuildScope(StrEnum):
    small = "small"  # one incremental codegen pass, then verification
    large = "large"  # plan into phases and implement one at a time (phase-56)


@dataclass(frozen=True)
class ScopeDecision:
    """The routing verdict, plus why — shown to the user, never hidden."""

    scope: BuildScope
    reason: str
    #: True when a model actually decided. False for every deterministic fallback, so the UI can be
    #: honest about which one happened and tests can assert the fallback was taken.
    classified: bool = False

    @property
    def is_small(self) -> bool:
        return self.scope is BuildScope.small


SYSTEM_EXTRA = """
You decide how much work a change request to an existing web app needs. Answer with ONE word on the
first line: SMALL or LARGE. On the second line give a short reason (one sentence).

SMALL — a fix or tweak confined to a few files: a missing button, a wrong label, a broken handler, a
styling correction, one endpoint's behaviour, a bug in existing logic.

LARGE — work that spans the app or introduces a new capability: a new feature or page, a new data
model or entity, auth or permissions, a cross-cutting change (theming, i18n, routing overhaul), or
several unrelated changes requested at once.

When genuinely unsure, answer LARGE: an over-planned small change is slow, but an under-planned
large one produces a half-built app.
""".strip()


def user_prompt(instruction: str, *, existing_files: int) -> str:
    return (
        f"The app already exists ({existing_files} feature file(s) written).\n\n"
        f"Change request:\n{instruction.strip()}\n\n"
        "SMALL or LARGE?"
    )


def parse_scope(text: str) -> ScopeDecision | None:
    """Read the model's verdict. Returns ``None`` when it did not answer in the agreed shape."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return None
    head = lines[0].upper()
    # Tolerate decoration ("SMALL.", "**LARGE**", "Answer: SMALL") without accepting a reply that
    # names both — an ambiguous verdict is no verdict.
    has_small = "SMALL" in head
    has_large = "LARGE" in head
    if has_small == has_large:
        return None
    reason = lines[1][:_MAX_REASON_CHARS] if len(lines) > 1 else ""
    return ScopeDecision(
        scope=BuildScope.small if has_small else BuildScope.large,
        reason=reason or ("a small, contained change" if has_small else "a broad change"),
        classified=True,
    )


def heuristic_scope(
    instruction: str | None, *, has_build: bool, fresh: bool, forced: BuildScope | None = None
) -> ScopeDecision | None:
    """The decisions that need no model at all. ``None`` means "ask the classifier"."""
    if forced is not None:
        return ScopeDecision(forced, "you chose this explicitly")
    if not (instruction or "").strip():
        # A cold build has no change request to scope — it is the case phase-56 exists for.
        return ScopeDecision(BuildScope.large, "an initial build always plans its phases")
    if fresh:
        return ScopeDecision(BuildScope.large, "a full regeneration always plans its phases")
    if not has_build:
        return ScopeDecision(BuildScope.large, "there is no existing build to change incrementally")
    return None


class ScopeClassifier:
    """Decides `small` vs `large` for one build invocation."""

    def __init__(self, client: object) -> None:
        self._client = client

    async def decide(
        self,
        *,
        project_id: PydanticObjectId,
        run: Run,
        channel: str,
        instruction: str | None,
        has_build: bool,
        fresh: bool = False,
        existing_files: int = 0,
        forced: BuildScope | None = None,
    ) -> ScopeDecision:
        decided = heuristic_scope(instruction, has_build=has_build, fresh=fresh, forced=forced)
        if decided is not None:
            return decided

        assert instruction is not None  # heuristic_scope returns non-None when it is blank
        try:
            result = await self._client.run_tool_loop(  # type: ignore[attr-defined]
                task_kind=TaskKind.classify,
                project_id=project_id,
                run=run,
                system=SYSTEM_EXTRA,
                messages=[
                    {
                        "role": "user",
                        "content": user_prompt(instruction, existing_files=existing_files),
                    }
                ],
                channel=channel,
                tools=[],  # a verdict, not a task: no tools, so the loop is one turn
                max_turns=1,
            )
        except (UserError, NotFoundError) as exc:
            # Budget exhausted, provider down, project gone — none of which is a reason to block a
            # build. Fall back to today's behaviour (phase-56's fallback-plan posture).
            logger.info("build scope classification unavailable (%s); planning phases", exc)
            return ScopeDecision(BuildScope.large, "the scope classifier was unavailable")

        parsed = parse_scope(result.text)
        if parsed is None:
            logger.info("build scope reply was unparseable; planning phases")
            return ScopeDecision(BuildScope.large, "the scope classifier gave no clear answer")
        return parsed


__all__ = [
    "SYSTEM_EXTRA",
    "BuildScope",
    "ScopeClassifier",
    "ScopeDecision",
    "heuristic_scope",
    "parse_scope",
    "user_prompt",
]
