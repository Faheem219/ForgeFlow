"""The non-linear stage state machine (§8, D12). Pure — no I/O — so every rule below is
exhaustively unit-testable; :mod:`app.projects.service` is the only caller and does the
persisting.

Stage order (for downstream/staleness purposes only — **not** a required sequence)::

    requirements -> design -> build -> test -> deploy -> validate

Truth table (the source the tests in ``test_state_machine.py`` are written against)::

    action    | precondition                          | effect
    ----------|----------------------------------------|--------------------------------------
    enter     | none, EXCEPT the two hard prereqs below| status -> in_progress (no-op if the
              |                                        | stage is already past `empty`; use
              |                                        | `refine` to reopen a finished stage)
    skip      | none                                   | status -> skipped
    complete  | none, EXCEPT the two hard prereqs below| status -> complete
    refine    | current status in                      | status -> in_progress; every strictly
              |   {complete, skipped, stale}           | downstream stage whose status != empty
              |                                        | is marked `stale` (artifacts untouched)

    Hard prereqs (the ONLY mandatory ordering, D12):
      - enter/complete `deploy`   requires `build`  == complete
      - enter/complete `validate` requires `deploy`  == complete
    `skip` and `refine` are never blocked by hard prereqs — opting out or reopening a stage
    never requires an upstream stage to be in any particular state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from app.db.models.enums import Stage, StageStatus

STAGE_ORDER: tuple[Stage, ...] = (
    Stage.requirements,
    Stage.design,
    Stage.build,
    Stage.test,
    Stage.deploy,
    Stage.validate,
)

_REOPENABLE = frozenset({StageStatus.complete, StageStatus.skipped, StageStatus.stale})


class Action(StrEnum):
    enter = "enter"
    skip = "skip"
    complete = "complete"
    refine = "refine"


StageSnapshot = Mapping[Stage, StageStatus]


@dataclass(frozen=True)
class TransitionResult:
    """Outcome of :func:`can_transition` — a pure legality check, no state produced."""

    allowed: bool
    reason: str | None = None


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of :func:`apply_transition` — the new snapshot + newly-staled stages."""

    states: dict[Stage, StageStatus]
    stage: Stage
    from_status: StageStatus
    to_status: StageStatus
    stale: tuple[Stage, ...] = field(default_factory=tuple)


def downstream_of(stage: Stage) -> tuple[Stage, ...]:
    """Stages strictly after ``stage`` in :data:`STAGE_ORDER`."""
    idx = STAGE_ORDER.index(stage)
    return STAGE_ORDER[idx + 1 :]


def _status_of(states: StageSnapshot, stage: Stage) -> StageStatus:
    return states.get(stage, StageStatus.empty)


def _hard_prereq_violation(states: StageSnapshot, stage: Stage, action: Action) -> str | None:
    if action not in (Action.enter, Action.complete):
        return None
    if stage is Stage.deploy and _status_of(states, Stage.build) is not StageStatus.complete:
        return "deploy requires the build stage to be complete"
    if stage is Stage.validate and _status_of(states, Stage.deploy) is not StageStatus.complete:
        return "validate requires the deploy stage to be complete"
    return None


def can_transition(states: StageSnapshot, stage: Stage, action: Action) -> TransitionResult:
    """Pure legality check for ``action`` on ``stage`` given the project's current snapshot."""
    violation = _hard_prereq_violation(states, stage, action)
    if violation is not None:
        return TransitionResult(allowed=False, reason=violation)

    if action is Action.refine and _status_of(states, stage) not in _REOPENABLE:
        return TransitionResult(
            allowed=False,
            reason="refine requires the stage to already be complete, skipped, or stale",
        )

    return TransitionResult(allowed=True)


def apply_transition(states: StageSnapshot, stage: Stage, action: Action) -> ApplyResult:
    """Compute the new snapshot for ``action`` on ``stage``.

    Raises ``ValueError`` if the transition is illegal — callers (the service layer) are
    expected to check :func:`can_transition` first, or catch and re-raise as a ``UserError``.
    """
    result = can_transition(states, stage, action)
    if not result.allowed:
        raise ValueError(result.reason)

    from_status = _status_of(states, stage)
    new_states = dict(states)
    stale: tuple[Stage, ...] = ()

    if action is Action.enter:
        to_status = from_status if from_status is not StageStatus.empty else StageStatus.in_progress
    elif action is Action.skip:
        to_status = StageStatus.skipped
    elif action is Action.complete:
        to_status = StageStatus.complete
    else:  # Action.refine
        to_status = StageStatus.in_progress
        stale = tuple(
            s for s in downstream_of(stage) if _status_of(states, s) is not StageStatus.empty
        )
        for s in stale:
            new_states[s] = StageStatus.stale

    new_states[stage] = to_status
    return ApplyResult(
        states=new_states,
        stage=stage,
        from_status=from_status,
        to_status=to_status,
        stale=stale,
    )
