"""Full truth-table coverage for the pure state machine (phase-06). No I/O, no event loop."""

from __future__ import annotations

import pytest

from app.db.models.enums import Stage, StageStatus
from app.projects.state_machine import (
    STAGE_ORDER,
    Action,
    apply_transition,
    can_transition,
    downstream_of,
)

# ---------------------------------------------------------------------- downstream_of


def test_downstream_of_requirements_is_everything_after() -> None:
    assert downstream_of(Stage.requirements) == (
        Stage.design,
        Stage.build,
        Stage.test,
        Stage.deploy,
        Stage.validate,
    )


def test_design_is_downstream_of_requirements_not_upstream() -> None:
    """The 2026-08-06 reorder: you decide *what* the app does before designing its UI, so a
    requirements change invalidates the design — not the other way round."""
    assert downstream_of(Stage.design) == (
        Stage.build,
        Stage.test,
        Stage.deploy,
        Stage.validate,
    )
    assert Stage.requirements not in downstream_of(Stage.design)


def test_downstream_of_validate_is_empty() -> None:
    assert downstream_of(Stage.validate) == ()


def test_stage_order_matches_pipeline() -> None:
    assert STAGE_ORDER == (
        Stage.requirements,
        Stage.design,
        Stage.build,
        Stage.test,
        Stage.deploy,
        Stage.validate,
    )


# ---------------------------------------------------------------------- enter (enter-anywhere)


def test_enter_on_empty_stage_starts_in_progress() -> None:
    result = apply_transition({}, Stage.build, Action.enter)
    assert result.from_status is StageStatus.empty
    assert result.to_status is StageStatus.in_progress
    assert result.stale == ()


def test_enter_on_in_progress_stage_is_a_noop() -> None:
    states = {Stage.build: StageStatus.in_progress}
    result = apply_transition(states, Stage.build, Action.enter)
    assert result.to_status is StageStatus.in_progress


def test_enter_on_complete_stage_does_not_reopen_it() -> None:
    """Viewing a finished stage isn't destructive — reopening requires `refine`."""
    states = {Stage.design: StageStatus.complete}
    result = apply_transition(states, Stage.design, Action.enter)
    assert result.to_status is StageStatus.complete
    assert result.stale == ()


def test_enter_is_legal_regardless_of_other_stages_state() -> None:
    # No requirements/build done at all — entering test directly is still legal.
    assert can_transition({}, Stage.test, Action.enter).allowed is True


# ---------------------------------------------------------------------- skip


def test_skip_any_stage_sets_skipped() -> None:
    for stage in STAGE_ORDER:
        result = apply_transition({}, stage, Action.skip)
        assert result.to_status is StageStatus.skipped


def test_skip_overwrites_in_progress() -> None:
    states = {Stage.requirements: StageStatus.in_progress}
    result = apply_transition(states, Stage.requirements, Action.skip)
    assert result.to_status is StageStatus.skipped


def test_skip_is_not_gated_by_hard_prereqs() -> None:
    # deploy has no build yet, but skipping it (opting out) is still legal.
    assert can_transition({}, Stage.deploy, Action.skip).allowed is True
    assert can_transition({}, Stage.validate, Action.skip).allowed is True


# ---------------------------------------------------------------------- unskip (undo a skip)


def test_skip_records_the_status_it_hid_as_the_restore_point() -> None:
    states = {Stage.build: StageStatus.complete}
    result = apply_transition(states, Stage.build, Action.skip)
    assert result.to_status is StageStatus.skipped
    assert result.restore_point is StageStatus.complete


def test_unskip_restores_the_recorded_status() -> None:
    """The scenario this exists for: a complete build skipped by accident comes back complete —
    without a rebuild, which is the only other route to `complete` for that stage."""
    states = {Stage.build: StageStatus.skipped}
    result = apply_transition(
        states, Stage.build, Action.unskip, restore_point=StageStatus.complete
    )
    assert result.to_status is StageStatus.complete
    assert result.stale == ()  # an undo invalidates nothing downstream


def test_unskip_with_no_restore_point_goes_back_to_empty() -> None:
    states = {Stage.design: StageStatus.skipped}
    result = apply_transition(states, Stage.design, Action.unskip)
    assert result.to_status is StageStatus.empty


def test_unskip_restores_in_progress_as_awaiting_user() -> None:
    """Nothing is still running, so `in_progress` would promise work nobody is doing."""
    states = {Stage.build: StageStatus.skipped}
    result = apply_transition(
        states, Stage.build, Action.unskip, restore_point=StageStatus.in_progress
    )
    assert result.to_status is StageStatus.awaiting_user


def test_unskip_rejected_unless_the_stage_is_skipped() -> None:
    for status in (
        StageStatus.empty,
        StageStatus.in_progress,
        StageStatus.awaiting_user,
        StageStatus.complete,
        StageStatus.stale,
    ):
        result = can_transition({Stage.build: status}, Stage.build, Action.unskip)
        assert result.allowed is False
        assert result.reason is not None and "skipped" in result.reason


def test_unskip_is_not_gated_by_hard_prereqs() -> None:
    """Undoing an opt-out restores a snapshot the project already held; it cannot need a prereq."""
    states = {Stage.deploy: StageStatus.skipped}
    assert can_transition(states, Stage.deploy, Action.unskip).allowed is True


def test_unskip_clears_the_restore_point_it_consumed() -> None:
    states = {Stage.build: StageStatus.skipped}
    result = apply_transition(
        states, Stage.build, Action.unskip, restore_point=StageStatus.complete
    )
    assert result.restore_point is None


def test_re_skipping_keeps_the_original_restore_point() -> None:
    """Otherwise the second skip records `skipped`, and the undo restores… skipped."""
    states = {Stage.build: StageStatus.skipped}
    result = apply_transition(states, Stage.build, Action.skip, restore_point=StageStatus.complete)
    assert result.restore_point is StageStatus.complete


@pytest.mark.parametrize("action", [Action.enter, Action.complete, Action.refine])
def test_every_other_transition_clears_the_restore_point(action: Action) -> None:
    """Once a stage has moved on under its own steam there is nothing left to restore."""
    states = {Stage.build: StageStatus.complete}
    result = apply_transition(states, Stage.build, action, restore_point=StageStatus.complete)
    assert result.restore_point is None


# ---------------------------------------------------------------------- complete


def test_complete_from_empty_is_allowed() -> None:
    result = apply_transition({}, Stage.design, Action.complete)
    assert result.to_status is StageStatus.complete


def test_complete_from_in_progress_is_allowed() -> None:
    states = {Stage.design: StageStatus.in_progress}
    result = apply_transition(states, Stage.design, Action.complete)
    assert result.to_status is StageStatus.complete


# ---------------------------------------------------------------------- refine / jump-back


def test_refine_rejected_from_empty() -> None:
    result = can_transition({}, Stage.design, Action.refine)
    assert result.allowed is False
    assert result.reason is not None


def test_refine_rejected_from_in_progress() -> None:
    states = {Stage.design: StageStatus.in_progress}
    result = can_transition(states, Stage.design, Action.refine)
    assert result.allowed is False


@pytest.mark.parametrize(
    "reopenable_status", [StageStatus.complete, StageStatus.skipped, StageStatus.stale]
)
def test_refine_allowed_from_reopenable_statuses(reopenable_status: StageStatus) -> None:
    states = {Stage.design: reopenable_status}
    assert can_transition(states, Stage.design, Action.refine).allowed is True


def test_refine_reopens_stage_to_in_progress() -> None:
    states = {Stage.design: StageStatus.complete}
    result = apply_transition(states, Stage.design, Action.refine)
    assert result.to_status is StageStatus.in_progress


def test_refine_marks_strictly_downstream_non_empty_stages_stale() -> None:
    states = {
        Stage.requirements: StageStatus.complete,
        Stage.design: StageStatus.complete,
        Stage.build: StageStatus.in_progress,
        Stage.test: StageStatus.empty,  # never touched — must NOT become stale
        Stage.deploy: StageStatus.empty,
        Stage.validate: StageStatus.empty,
    }
    result = apply_transition(states, Stage.requirements, Action.refine)

    assert set(result.stale) == {Stage.design, Stage.build}
    assert result.states[Stage.design] is StageStatus.stale
    assert result.states[Stage.build] is StageStatus.stale
    assert result.states[Stage.test] is StageStatus.empty
    assert result.states[Stage.deploy] is StageStatus.empty


def test_refining_design_does_not_stale_requirements() -> None:
    """A UI tweak does not invalidate the feature list — the cascade only runs downstream."""
    states = {
        Stage.requirements: StageStatus.complete,
        Stage.design: StageStatus.complete,
        Stage.build: StageStatus.complete,
    }
    result = apply_transition(states, Stage.design, Action.refine)

    assert set(result.stale) == {Stage.build}
    assert result.states[Stage.requirements] is StageStatus.complete


def test_refine_does_not_mark_upstream_stale() -> None:
    states = {
        Stage.requirements: StageStatus.complete,
        Stage.design: StageStatus.complete,
        Stage.build: StageStatus.complete,
    }
    result = apply_transition(states, Stage.build, Action.refine)
    assert Stage.design not in result.stale
    assert Stage.requirements not in result.stale
    assert result.states[Stage.design] is StageStatus.complete
    assert result.states[Stage.requirements] is StageStatus.complete


def test_refine_on_last_stage_has_nothing_downstream_to_stale() -> None:
    states = {Stage.validate: StageStatus.complete}
    result = apply_transition(states, Stage.validate, Action.refine)
    assert result.stale == ()


def test_refine_never_removes_artifacts_only_marks_status() -> None:
    """The state machine only ever returns a status snapshot — it has no concept of deleting
    artifacts, which live in a separate collection the service layer never touches on refine."""
    states = {Stage.requirements: StageStatus.complete, Stage.design: StageStatus.complete}
    result = apply_transition(states, Stage.requirements, Action.refine)
    assert result.states[Stage.design] is StageStatus.stale  # not "empty" / removed


# ---------------------------------------------------------------------- hard prereqs


def test_deploy_enter_blocked_without_build_complete() -> None:
    result = can_transition({}, Stage.deploy, Action.enter)
    assert result.allowed is False
    assert "build" in (result.reason or "")


def test_deploy_complete_blocked_without_build_complete() -> None:
    states = {Stage.build: StageStatus.in_progress}
    result = can_transition(states, Stage.deploy, Action.complete)
    assert result.allowed is False


def test_deploy_enter_allowed_with_build_complete() -> None:
    states = {Stage.build: StageStatus.complete}
    result = can_transition(states, Stage.deploy, Action.enter)
    assert result.allowed is True


def test_validate_enter_blocked_without_deploy_complete() -> None:
    states = {Stage.build: StageStatus.complete, Stage.deploy: StageStatus.in_progress}
    result = can_transition(states, Stage.validate, Action.enter)
    assert result.allowed is False
    assert "deploy" in (result.reason or "")


def test_validate_enter_allowed_with_deploy_complete() -> None:
    states = {Stage.build: StageStatus.complete, Stage.deploy: StageStatus.complete}
    result = can_transition(states, Stage.validate, Action.enter)
    assert result.allowed is True


def test_validate_complete_blocked_without_deploy_complete() -> None:
    result = can_transition({}, Stage.validate, Action.complete)
    assert result.allowed is False


def test_hard_prereqs_do_not_affect_unrelated_stages() -> None:
    # design/requirements/build/test have no hard prereqs at all.
    for stage in (Stage.design, Stage.requirements, Stage.build, Stage.test):
        assert can_transition({}, stage, Action.enter).allowed is True
        assert can_transition({}, stage, Action.complete).allowed is True


# ---------------------------------------------------------------------- apply_transition raises


def test_apply_transition_raises_on_illegal_transition() -> None:
    with pytest.raises(ValueError, match="build"):
        apply_transition({}, Stage.deploy, Action.enter)


def test_apply_transition_raises_on_illegal_refine() -> None:
    with pytest.raises(ValueError):
        apply_transition({}, Stage.design, Action.refine)


def test_apply_transition_raises_on_unskip_of_a_stage_that_is_not_skipped() -> None:
    with pytest.raises(ValueError, match="skipped"):
        apply_transition({Stage.build: StageStatus.complete}, Stage.build, Action.unskip)
