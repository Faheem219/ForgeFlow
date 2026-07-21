"""Happy path (phase-40): a green live run ends on the final live link.

This is the M4 promise in one test — not just "deployed", but *verified*.
"""

from __future__ import annotations

import json

import pytest

from app.core.config import get_config
from app.db.models import Project
from app.db.models.enums import ArtifactType, Stage, StageStatus
from app.db.repos import StageStateRepo
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.messages import MessageService
from app.orchestrator.schemas import Intent, IntentAction
from app.orchestrator.stages.base import StageContext
from app.orchestrator.stages.validate import (
    OUTCOME_VALIDATED,
    VALIDATION_REPORT_KIND,
    LiveValidationController,
    ValidateStageHandler,
)
from app.realtime.hub import emit
from tests.orchestrator.stages.validate_fakes import (
    FE_URL,
    PASSING,
    FakeDeployer,
    FakeLiveRunner,
    FakeRepairLoop,
    RecordingEmitter,
    project_ready_to_validate,
)

pytestmark = pytest.mark.usefixtures("mongo_db")


async def _ctx(project: Project) -> StageContext:
    assert project.id is not None
    stage_state = await StageStateRepo().get_or_create(project.id, Stage.validate)
    return StageContext(
        project=project,
        stage_state=stage_state,
        messages=MessageService(),
        artifacts=ArtifactService(),
        config=get_config(),
        emit=emit,
    )


async def test_a_green_live_run_validates_and_records_the_final_link() -> None:
    project = await project_ready_to_validate()
    repair = FakeRepairLoop()
    deployer = FakeDeployer()

    report = await LiveValidationController(
        live_runner=FakeLiveRunner(PASSING),
        repair=repair,
        deployer=deployer,
        emitter=RecordingEmitter(),
    ).run(project)

    assert report.outcome == OUTCOME_VALIDATED
    assert report.url == FE_URL  # the link the UI puts in front of the user
    assert len(report.cycles) == 1 and report.cycles[0].green
    # Nothing was repaired or redeployed — a passing site is left alone.
    assert repair.calls == [] and deployer.calls == 0


async def test_the_report_is_persisted_so_the_panel_survives_a_reload() -> None:
    project = await project_ready_to_validate()
    assert project.id is not None

    await LiveValidationController(
        live_runner=FakeLiveRunner(PASSING), emitter=RecordingEmitter()
    ).run(project)

    latest = await ArtifactService().get_latest(
        project.id, Stage.validate, ArtifactType.test_result
    )
    assert latest is not None
    assert latest.meta["kind"] == VALIDATION_REPORT_KIND
    stored = json.loads(await ArtifactService().get_content(latest) or "{}")
    assert stored["outcome"] == OUTCOME_VALIDATED
    assert stored["url"] == FE_URL


async def test_validate_events_stream_the_outcome() -> None:
    project = await project_ready_to_validate()
    emitter = RecordingEmitter()

    await LiveValidationController(live_runner=FakeLiveRunner(PASSING), emitter=emitter).run(
        project
    )

    assert emitter.steps() == ["validating", "validated"]
    assert all(e == "validate.status" for e, _p in emitter.events)


async def test_the_handler_completes_the_stage_on_a_verified_deployment() -> None:
    project = await project_ready_to_validate()
    assert project.id is not None
    controller = LiveValidationController(
        live_runner=FakeLiveRunner(PASSING), emitter=RecordingEmitter()
    )

    result = await ValidateStageHandler(controller).handle(
        Intent(project_id=project.id, stage=Stage.validate, action=IntentAction.proceed),
        await _ctx(project),
    )

    assert result.next_status is StageStatus.complete
    assert FE_URL in result.messages[0].content  # the final live link, in front of the user
    assert "verified" in result.messages[0].content


async def test_skip_leaves_the_deployment_unverified() -> None:
    project = await project_ready_to_validate()
    assert project.id is not None
    live = FakeLiveRunner(PASSING)

    result = await ValidateStageHandler(
        LiveValidationController(live_runner=live, emitter=RecordingEmitter())
    ).handle(
        Intent(project_id=project.id, stage=Stage.validate, action=IntentAction.skip),
        await _ctx(project),
    )

    assert live.calls == 0  # nothing was run
    assert result.next_status is None
    assert "unverified" in result.messages[0].content
