"""Build verification self-heals code failures through the bounded loop (phase-55 task 12).

A red typecheck is a ``code`` failure: it is adapted into a synthetic run, repaired through the
*existing* bounded loop, and the failed step is re-run to confirm — a build that fixed itself lands
verified, with no user message in between. A loop that cannot converge escalates with
``stop_reason="repair_escalated"``.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from app.agents.tools.context import ToolContext
from app.db.models import Run
from app.db.models.enums import Stage
from app.orchestrator.stages.build_verify import (
    STOP_REPAIR,
    BootProbe,
    BuildVerificationController,
    StepProbe,
)
from app.orchestrator.stages.repair import RepairLoopController
from tests.orchestrator.stages.repair_loop_fakes import (
    ScriptedAgent,
    StubAnalyzer,
    configure_loop,
    make_project,
    scripted_result,
)

pytestmark = pytest.mark.usefixtures("mongo_db")

_RED_TSC = "frontend/src/features/todos/TodoPage.tsx(12,5): error TS2339: Property 'x' ...\n"


async def _no_feature_findings(project: object, ctx: object, expect: object) -> list[str]:
    return []


def _controller(
    typecheck_probes: list[StepProbe], *, scripted: ScriptedAgent
) -> BuildVerificationController:
    calls = {"typecheck": 0}

    async def install(project: object, ctx: object) -> None:
        return None

    async def typecheck(project: object, ctx: object) -> StepProbe:
        probe = typecheck_probes[min(calls["typecheck"], len(typecheck_probes) - 1)]
        calls["typecheck"] += 1
        return probe

    async def boot(project: object, ctx: object) -> BootProbe:
        return BootProbe(fe_status="running", be_status="running")  # boots fine

    async def placeholder(project: object, ctx: object) -> list[str]:
        return []  # no placeholder left

    async def demo_test(project: object, ctx: object) -> list[str]:
        return []  # the skeleton's example tests were rewritten

    async def container_state(project_id: str) -> str:
        return "running"

    def repair_factory(analyzer: Any) -> RepairLoopController:
        return RepairLoopController(
            agent=scripted, analyzer=analyzer, stage=Stage.build, owns_stage_status=False
        )

    return BuildVerificationController(
        install=install,
        typecheck=typecheck,
        boot=boot,
        placeholder=placeholder,
        feature_code=_no_feature_findings,  # phase-64's structural check: not this file's subject
        demo_test=demo_test,
        container_state=container_state,
        analyzer_factory=lambda written: StubAnalyzer(),
        repair_factory=repair_factory,
    )


async def test_red_typecheck_is_repaired_then_reverified_to_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_loop(monkeypatch, max_iterations=3, stall_threshold=2)
    project = await make_project()
    assert project.id is not None
    run = await Run(project_id=project.id, kind="codegen:build").insert()

    # The agent fixes it in one attempt (green), and the re-run typecheck comes back clean.
    scripted = ScriptedAgent([await scripted_result(project, run, 1, failing_names=[])])
    controller = _controller(
        [StepProbe(output=_RED_TSC, exit_code=1), StepProbe(exit_code=0)], scripted=scripted
    )

    report = await controller.run(
        project, run, ctx=cast(ToolContext, object()), channel=str(project.id), written_files=[]
    )

    assert scripted.iterations == [1]  # the loop actually ran and converged
    assert report.repaired is True
    assert report.ok is True
    assert report.stop_reason is None


async def test_a_loop_that_cannot_converge_escalates(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_loop(monkeypatch, max_iterations=3, stall_threshold=2)
    project = await make_project()
    assert project.id is not None
    run = await Run(project_id=project.id, kind="codegen:build").insert()

    # Every attempt leaves the same failure — the loop stalls and escalates.
    scripted = ScriptedAgent([await scripted_result(project, run, 1, failing_names=["a"])])
    controller = _controller([StepProbe(output=_RED_TSC, exit_code=1)], scripted=scripted)

    report = await controller.run(
        project, run, ctx=cast(ToolContext, object()), channel=str(project.id), written_files=[]
    )

    assert report.ok is False
    assert report.stop_reason == STOP_REPAIR
    assert len(scripted.iterations) >= 1  # it tried before giving up
