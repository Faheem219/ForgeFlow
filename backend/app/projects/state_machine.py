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
    skip      | none                                   | status -> skipped; the status it held is
              |                                        | recorded as the stage's restore point
    complete  | none, EXCEPT the two hard prereqs below| status -> complete
    refine    | current status in                      | status -> in_progress; every strictly
              |   {complete, skipped, stale}           | downstream stage whose status != empty
              |                                        | is marked `stale` (artifacts untouched)
    unskip    | current status == skipped              | status -> the restore point recorded by
              |                                        | the `skip` (`empty` if none); no staleness

    Hard prereqs (the ONLY mandatory ordering, D12):
      - enter/complete `deploy`   requires `build`  == complete
      - enter/complete `validate` requires `deploy`  == complete
    `skip`, `refine` and `unskip` are never blocked by hard prereqs — opting out, reopening, or
    *undoing an opt-out* never requires an upstream stage to be in any particular state.

Restore points (``unskip``)
---------------------------
A skip is reversible: `skip` remembers the status the stage held, and `unskip` puts it back. That
matters most for **build**, where the alternative to an undo is a full (paid) rebuild just to get
an already-working app back to `complete`. The restore point travels in and out of
:func:`apply_transition` rather than living here — this module stays pure; the caller persists it
(``StageState.previous_status``). Every transition other than `skip` clears it, because once a
stage has moved on there is nothing left to restore.
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
    unskip = "unskip"


StageSnapshot = Mapping[Stage, StageStatus]


@dataclass(frozen=True)
class TransitionResult:
    """Outcome of :func:`can_transition` — a pure legality check, no state produced."""

    allowed: bool
    reason: str | None = None


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of :func:`apply_transition` — the new snapshot + newly-staled stages.

    ``restore_point`` is what the caller should now persist as the stage's restore point: the
    status a later ``unskip`` puts back. It is authoritative, not advisory — ``None`` means
    *clear* it, so a caller that always writes it can never leave a stale one behind.
    """

    states: dict[Stage, StageStatus]
    stage: Stage
    from_status: StageStatus
    to_status: StageStatus
    stale: tuple[Stage, ...] = field(default_factory=tuple)
    restore_point: StageStatus | None = None


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

    if action is Action.unskip and _status_of(states, stage) is not StageStatus.skipped:
        return TransitionResult(allowed=False, reason="unskip requires the stage to be skipped")

    return TransitionResult(allowed=True)


def restore_target(recorded: StageStatus | None) -> StageStatus:
    """The status an ``unskip`` puts back, given the restore point recorded by the ``skip``.

    Nothing recorded means the stage was never anything but skipped, so it goes back to ``empty``.
    A recorded ``in_progress`` restores as ``awaiting_user`` instead: whatever was running when the
    stage was skipped is long gone, and re-showing "in progress" would promise work nobody is doing.
    """
    if recorded is None:
        return StageStatus.empty
    if recorded is StageStatus.in_progress:
        return StageStatus.awaiting_user
    return recorded


def apply_transition(
    states: StageSnapshot,
    stage: Stage,
    action: Action,
    *,
    restore_point: StageStatus | None = None,
) -> ApplyResult:
    """Compute the new snapshot for ``action`` on ``stage``.

    ``restore_point`` is the one currently recorded for ``stage`` (``StageState.previous_status``);
    it is what an ``unskip`` restores. The new one to persist comes back on the result.

    Raises ``ValueError`` if the transition is illegal — callers (the service layer) are
    expected to check :func:`can_transition` first, or catch and re-raise as a ``UserError``.
    """
    result = can_transition(states, stage, action)
    if not result.allowed:
        raise ValueError(result.reason)

    from_status = _status_of(states, stage)
    new_states = dict(states)
    stale: tuple[Stage, ...] = ()
    # Cleared by default: any transition other than a skip leaves nothing to restore.
    next_restore_point: StageStatus | None = None

    to_status: StageStatus
    if action is Action.enter:
        to_status = from_status if from_status is not StageStatus.empty else StageStatus.in_progress
    elif action is Action.skip:
        to_status = StageStatus.skipped
        # Re-skipping an already-skipped stage must not overwrite the real restore point with
        # `skipped` — that would turn the undo into a no-op.
        next_restore_point = restore_point if from_status is StageStatus.skipped else from_status
    elif action is Action.complete:
        to_status = StageStatus.complete
    elif action is Action.unskip:
        to_status = restore_target(restore_point)
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
        restore_point=next_restore_point,
    )
