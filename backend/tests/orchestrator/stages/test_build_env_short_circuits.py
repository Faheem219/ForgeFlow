"""Sandbox failures are never forwarded to the repair loop (phase-55 task 12).

The user's explicit requirement, tested directly: a build that fails because the *sandbox* is broken
(disk full, pids exhausted, OOM, dead container) stops with ``stop_reason="environment"`` and spends
**zero** repair iterations — the classifier runs before any synthetic run is built, so the agent's
input never exists. Asserted as ``ScriptedAgent.iterations == []``.

DB-free: the env path returns before persisting anything; the injected steps and repair loop are
never reached.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from beanie import PydanticObjectId

from app.agents.repair import RepairResult
from app.agents.tools.context import ToolContext
from app.db.models.enums import Stage
from app.orchestrator.stages.build_verify import (
    STOP_ENV,
    BootProbe,
    BuildVerificationController,
    StepProbe,
)
from app.orchestrator.stages.repair import RepairLoopController
from tests.orchestrator.stages.repair_loop_fakes import ScriptedAgent, StubAnalyzer


async def _no_feature_findings(project: object, ctx: object, expect: object) -> list[str]:
    return []


class _StubProject:
    def __init__(self) -> None:
        self.id = PydanticObjectId()


def _controller(
    probe: StepProbe, *, container: str, scripted: ScriptedAgent
) -> BuildVerificationController:
    async def install(project: object, ctx: object) -> None:
        return None

    async def typecheck(project: object, ctx: object) -> StepProbe:
        return probe

    async def boot(project: object, ctx: object) -> BootProbe:
        return BootProbe(fe_status="running", be_status="running")

    async def placeholder(project: object, ctx: object) -> list[str]:
        return []

    async def container_state(project_id: str) -> str:
        return container

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
        container_state=container_state,
        analyzer_factory=lambda written: StubAnalyzer(),
        repair_factory=repair_factory,
    )


@pytest.mark.parametrize(
    ("output", "exit_code", "container"),
    [
        ("ENOSPC: no space left on device, write", 1, "running"),  # disk
        ("sh: fork: resource temporarily unavailable", 1, "running"),  # pids limit
        ("<--- JavaScript heap out of memory --->", 137, "running"),  # OOM
        ("", 1, "exited"),  # dead container — its exit code lies, the state does not
    ],
)
async def test_environment_failures_never_reach_the_repair_loop(
    output: str, exit_code: int, container: str
) -> None:
    scripted = ScriptedAgent([RepairResult(note="must never run")])
    controller = _controller(
        StepProbe(output=output, exit_code=exit_code), container=container, scripted=scripted
    )

    report = await controller.run(
        cast("Any", _StubProject()),
        cast("Any", object()),
        ctx=cast(ToolContext, object()),
        channel="ch",
        written_files=[],
    )

    assert report.ok is False
    assert report.stop_reason == STOP_ENV
    assert report.hint  # a fix hint is surfaced to the user
    assert scripted.iterations == []  # THE requirement: zero repair iterations spent
