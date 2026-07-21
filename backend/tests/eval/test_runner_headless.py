"""Headless runner (phase-44): a spec goes through the real pipeline shape with no UI attached.

The conductor, test runner and repair loop are injected, so what is under test is the runner's own
job — driving the legs in order, taking the two measurements, and producing a complete record.
"""

from __future__ import annotations

import pytest

from app.db.models import Project
from app.db.models.enums import Stage, StageStatus
from app.db.repos import StageStateRepo
from app.eval.metrics import OUTCOME_DELIVERED, OUTCOME_ESCALATED, OUTCOME_FAILED
from app.eval.runner import EvalRunner, Legs
from app.orchestrator.requirements import RequirementsService
from tests.eval.eval_fakes import (
    FakeConductor,
    FakeRepair,
    FakeTests,
    RecordingCleanup,
    StepClock,
    make_spec,
    seed_cost,
    seed_deployment,
)

pytestmark = pytest.mark.usefixtures("mongo_db")


def _runner(**overrides: object) -> EvalRunner:
    defaults: dict[str, object] = {
        "conductor": FakeConductor(),
        "tests": FakeTests(),
        "repair": FakeRepair(),
        "cleanup": RecordingCleanup(),
        "clock": StepClock(),
    }
    defaults.update(overrides)
    return EvalRunner(**defaults)  # type: ignore[arg-type]


async def test_a_spec_runs_end_to_end_and_produces_a_complete_record() -> None:
    metrics = await _runner().run_spec(make_spec())

    assert metrics.spec_id == "sample"
    assert metrics.title == "Sample app"
    assert metrics.difficulty == "simple"
    assert metrics.outcome == OUTCOME_DELIVERED
    assert metrics.project_id is not None
    assert metrics.stages_run == ["requirements", "build", "test", "repair"]
    assert metrics.wall_clock_seconds > 0


async def test_the_spec_requirements_are_saved_through_the_products_own_service() -> None:
    spec = make_spec()
    runner = _runner()

    metrics = await runner.run_spec(spec)

    from beanie import PydanticObjectId

    saved = await RequirementsService().latest(PydanticObjectId(metrics.project_id))
    assert saved is not None
    assert [f.name for f in saved.features] == ["Do the thing"]
    # Criterion ids were minted by the product, so results can join back to requirements.
    assert all(c.id.startswith("ac-") for f in saved.features for c in f.acceptance_criteria)


async def test_the_requirements_stage_is_marked_complete_for_downstream_stages() -> None:
    from beanie import PydanticObjectId

    metrics = await _runner().run_spec(make_spec())

    state = await StageStateRepo().get_or_create(
        PydanticObjectId(metrics.project_id), Stage.requirements
    )
    assert state.status is StageStatus.complete


async def test_the_runner_drives_the_real_conductor_intents() -> None:
    """Not a reimplementation of the pipeline — the same entry point the UI uses."""
    conductor = FakeConductor()
    await _runner(conductor=conductor).run_spec(make_spec())

    assert conductor.intents == [("build", "proceed")]


async def test_costly_legs_are_off_unless_asked_for() -> None:
    conductor = FakeConductor()
    metrics = await _runner(conductor=conductor).run_spec(make_spec())

    assert "deploy" not in conductor.stages() and "validate" not in conductor.stages()
    assert metrics.screenshot_to_url_seconds is None  # never fabricated when deploy did not run
    assert metrics.live_url is None


async def test_the_deploy_leg_records_the_live_url_and_the_headline_timing() -> None:
    class DeployingConductor(FakeConductor):
        async def handle_intent(self, user_id: object, intent: object) -> object:
            result = await super().handle_intent(user_id, intent)  # type: ignore[arg-type]
            if str(intent.stage) == "deploy":  # type: ignore[attr-defined]
                await seed_deployment(intent.project_id)  # type: ignore[attr-defined]
            return result

    conductor = DeployingConductor()
    metrics = await _runner(conductor=conductor).run_spec(make_spec(), legs=Legs.with_deploy())

    assert conductor.stages() == ["build", "deploy", "validate"]
    assert metrics.live_url == "https://web.app"
    assert metrics.screenshot_to_url_seconds is not None
    assert metrics.outcome == OUTCOME_DELIVERED


async def test_a_deploy_that_never_goes_live_is_escalated_not_delivered() -> None:
    metrics = await _runner().run_spec(make_spec(), legs=Legs.with_deploy())

    assert metrics.live_url is None
    assert metrics.outcome == OUTCOME_ESCALATED


async def test_a_broken_pipeline_is_a_result_not_a_crashed_harness() -> None:
    """One spec exploding must not stop a corpus run — it becomes a `failed` record."""
    conductor = FakeConductor(fail_on=Stage.build)

    metrics = await _runner(conductor=conductor).run_spec(make_spec())

    assert metrics.outcome == OUTCOME_FAILED
    assert metrics.error is not None and "blew up" in metrics.error
    assert metrics.wall_clock_seconds > 0  # still timed and still recorded


async def test_cost_is_summed_from_every_run_the_spec_produced() -> None:
    class CostingConductor(FakeConductor):
        async def handle_intent(self, user_id: object, intent: object) -> object:
            await seed_cost(intent.project_id, 1200, 3.5)  # type: ignore[attr-defined]
            return await super().handle_intent(user_id, intent)  # type: ignore[arg-type]

    metrics = await _runner(conductor=CostingConductor()).run_spec(make_spec())

    assert metrics.tokens == 1200
    assert metrics.inr_cost == 3.5


async def test_the_corpus_run_produces_one_record_per_spec() -> None:
    specs = [make_spec(), make_spec(id="second", title="Second")]

    report = await _runner().run_all(specs)

    assert [r.spec_id for r in report.records] == ["sample", "second"]
    assert report.finished_at is not None
    assert report.legs == ["build", "test", "repair"]
    assert report.aggregate()["specs"] == 2


async def test_the_report_serializes_for_the_dashboard() -> None:
    import json

    report = await _runner().run_all([make_spec()])
    payload = json.loads(report.to_json())

    assert payload["aggregate"]["specs"] == 1
    assert payload["records"][0]["spec_id"] == "sample"
    assert "first_pass" in payload["records"][0] and "post_repair" in payload["records"][0]


async def test_projects_are_created_per_spec() -> None:
    report = await _runner().run_all([make_spec(), make_spec(id="second")])

    ids = [r.project_id for r in report.records]
    assert len(set(ids)) == 2
    for pid in ids:
        from beanie import PydanticObjectId

        assert await Project.get(PydanticObjectId(pid)) is not None
