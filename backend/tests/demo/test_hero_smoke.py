"""Hero-demo smoke test (phase-50) — a headless rehearsal that guards the money moment.

The demo's thesis (D1) is the bounded repair loop turning a build that *almost* works into one that
does. This drives the **real** hero spec through the **real** headless runner, with only the costly
legs (model, sandbox, live deploy) injected as fakes, and asserts each milestone the runbook
promises: the skeleton is instantiated, a genuine first-pass failure is then repair-fixed, the
deploy is healthy, live validation runs, and a final URL comes out. It is the regression guard that
catches a broken hero path before a live audience does.
"""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.db.models.enums import Stage
from app.eval.metrics import OUTCOME_DELIVERED
from app.eval.runner import EvalRunner, Legs
from app.eval.specs_loader import SPECS_DIR, EvalSpec, load_spec
from app.orchestrator.schemas import Intent
from tests.eval.eval_fakes import (
    FakeConductor,
    FakeRepair,
    FakeTests,
    RecordingCleanup,
    StepClock,
    seed_deployment,
)

pytestmark = pytest.mark.usefixtures("mongo_db")

HERO_ID = "hero-todo"
DEMO_URL = "https://hero-todo.forgeflow.app"


def hero_spec() -> EvalSpec:
    return load_spec(SPECS_DIR / f"{HERO_ID}.yaml", specs_dir=SPECS_DIR)


class HeroConductor(FakeConductor):
    """Records intents like the base fake, and simulates a healthy deploy.

    On the deploy intent it inserts a live ``Deployment`` — exactly what the real deploy
    orchestration (phase-37) persists — so the runner's deploy leg surfaces a live URL.
    """

    def __init__(self, url: str = DEMO_URL) -> None:
        super().__init__()
        self._url = url

    async def handle_intent(self, user_id: PydanticObjectId, intent: Intent) -> None:
        await super().handle_intent(user_id, intent)
        if intent.stage is Stage.deploy:
            await seed_deployment(intent.project_id, self._url)
        return None


def _runner(conductor: HeroConductor, cleanup: RecordingCleanup) -> EvalRunner:
    return EvalRunner(
        conductor=conductor,
        # First pass fails the two "Clear completed" criteria (5 of 7 pass); repair fixes all 7.
        tests=FakeTests(passed=5, failed=2),
        repair=FakeRepair(passed=7, failed=0, iterations=2, outcome="fixed"),
        cleanup=cleanup,
        clock=StepClock(),
    )


# --------------------------------------------------------------------- the scenario itself


def test_the_hero_spec_is_a_valid_loadable_benchmark() -> None:
    spec = hero_spec()
    assert spec.id == HERO_ID
    assert spec.inputs.prompt  # the demo has something to hand ForgeFlow
    names = [f.name for f in spec.requirements.features]
    assert names == ["Manage todos", "Outstanding count", "Clear completed"]
    assert spec.expected_features == names


def test_the_hero_spec_contains_the_engineered_repair_moment() -> None:
    """The scenario must actually have the discriminating criterion the demo is built around."""
    spec = hero_spec()
    clear = next(f for f in spec.requirements.features if f.name == "Clear completed")
    texts = " ".join(c.text.lower() for c in clear.acceptance_criteria)
    assert "incomplete" in texts and "untouched" in texts  # the subtle, easy-to-get-wrong rule


# --------------------------------------------------------------------- the end-to-end run


@pytest.mark.asyncio
async def test_hero_run_hits_every_milestone() -> None:
    conductor = HeroConductor()
    cleanup = RecordingCleanup()

    metrics = await _runner(conductor, cleanup).run_spec(
        hero_spec(), legs=Legs.with_deploy(), user_id=PydanticObjectId()
    )

    # 1. Skeleton instantiated — the build leg drove the conductor's build handler.
    assert ("build", "proceed") in conductor.intents
    assert "build" in metrics.stages_run

    # 2. A genuine first-pass failure...
    assert metrics.first_pass.total == 7
    assert metrics.first_pass.failed == 2
    assert metrics.first_pass.green is False

    # 3. ...that the bounded repair loop fixes — the money moment.
    assert metrics.repair_outcome == "fixed"
    assert metrics.repair_iterations == 2
    assert metrics.post_repair.green is True
    assert metrics.repaired is True
    assert metrics.repair_delta is not None and metrics.repair_delta > 0

    # 4. Deploy healthy, with a real live URL...
    assert metrics.live_url == DEMO_URL
    assert metrics.screenshot_to_url_seconds is not None

    # 5. ...and live validation ran.
    assert conductor.stages() == ["build", "deploy", "validate"]
    assert "validate" in metrics.stages_run

    # 6. The pipeline delivered, and the per-spec resources were torn down.
    assert metrics.outcome == OUTCOME_DELIVERED
    assert metrics.project_id in cleanup.cleaned


@pytest.mark.asyncio
async def test_the_free_legs_alone_still_show_the_repair_contribution() -> None:
    """The CI-cheap path (no deploy) still proves the headline: repair moved the needle."""
    conductor = HeroConductor()
    cleanup = RecordingCleanup()

    metrics = await _runner(conductor, cleanup).run_spec(hero_spec(), legs=Legs())

    assert metrics.first_pass.rate is not None and metrics.first_pass.rate < 1.0
    assert metrics.post_repair.green is True
    assert metrics.outcome == OUTCOME_DELIVERED
    # No deploy leg → no URL, and that reads as "unknown" (None), never a misleading zero.
    assert metrics.live_url is None
    assert metrics.screenshot_to_url_seconds is None


@pytest.mark.asyncio
async def test_a_clean_first_pass_needs_no_repair() -> None:
    """Sanity: if generation nails it first time, repair is 'not_needed', not a fake improvement."""
    conductor = HeroConductor()
    runner = EvalRunner(
        conductor=conductor,
        tests=FakeTests(passed=7, failed=0),
        repair=FakeRepair(passed=7, failed=0),
        cleanup=RecordingCleanup(),
        clock=StepClock(),
    )

    metrics = await runner.run_spec(hero_spec(), legs=Legs())

    assert metrics.first_pass.green is True
    assert metrics.repair_outcome == "not_needed"
    assert metrics.repaired is False  # improving on 100% is impossible, not a win
    assert metrics.outcome == OUTCOME_DELIVERED
