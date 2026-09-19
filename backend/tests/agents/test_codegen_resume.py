"""Resume at the first phase that is not `done` (phase-56 task 5).

The **report is authoritative**, not the filesystem: a phase that wrote three of five files leaves
those three on disk, and treating file presence as "done" would skip the remaining two forever.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.anthropic_client import AnthropicClient
from app.agents.build_phases import PhaseOutcome
from app.agents.codegen import BUILD_REPORT_KIND, BuildReport, CodegenAgent
from app.agents.tools.context import ToolContext
from app.agents.tools.definitions import default_registry
from app.db.models.enums import ArtifactType, Stage
from app.orchestrator.artifacts import ArtifactService
from tests.agents.codegen_fakes import (
    configure_env,
    make_project_run,
    make_skeleton,
    phase_write,
    phased_transport,
    tool_use,
)
from tests.agents.tools.conftest import FakeExec, FakePreview, FakeWorkspace

pytestmark = pytest.mark.usefixtures("mongo_db")

_PHASES = [
    ("be-core", "Backend API", "backend"),
    ("fe-core", "Frontend pages", "frontend"),
    ("wiring", "Routing and identity", "wiring"),
]


async def _seed_report(project_id: object, statuses: list[str]) -> None:
    report = BuildReport(
        boot_status="not_started",
        files_changed=["backend/src/features/t/t.ts"],
        features_built=[],
        follow_ups=[],
        notes=[],
        commit="sha1",
        tests_passed=None,
        summary="partial",
        plan="",
        phases=[
            PhaseOutcome(id=pid, title=title, kind=kind, status=status)
            for (pid, title, kind), status in zip(_PHASES, statuses, strict=True)
        ],
        outcome="partial",
        stop_reason="wall_clock",
    )
    await ArtifactService().create_version(
        project_id,  # type: ignore[arg-type]
        Stage.build,
        ArtifactType.code_change,
        text=json.dumps(report.to_dict(), indent=2),
        meta={"kind": BUILD_REPORT_KIND},
    )


async def test_a_rebuild_restarts_at_the_first_unfinished_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run()
    assert project.id is not None
    await _seed_report(project.id, ["done", "failed", "pending"])

    ws = FakeWorkspace()
    # A file on disk from the previous attempt, so the inventory is non-empty (that is what makes
    # the agent look for a previous report at all).
    ws.files["backend/src/features/t/t.ts"] = "export {};"
    ws.files["pnpm-workspace.yaml"] = "packages:\n"
    ctx = ToolContext.build(
        project, run, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )
    # Only TWO phase loops are scripted: if the agent re-ran phase 0 it would exhaust the transport.
    transport = phased_transport(
        _PHASES,
        [
            [tool_use("write_file", {"path": "frontend/src/features/t/T.tsx", "content": "x"})],
            [tool_use("write_file", {"path": "frontend/src/routes.tsx", "content": "x"})],
        ],
    )

    report = await CodegenAgent(AnthropicClient(transport), default_registry()).run(
        project, run, ctx=ctx
    )

    assert [(p.id, p.status) for p in report.phases] == [
        ("be-core", "done"),  # carried over from the previous report, NOT re-run
        ("fe-core", "done"),
        ("wiring", "done"),
    ]
    assert any("Resuming at phase 2" in note for note in report.notes)
    # Only the two resumed phases were committed this run.
    assert [m for m in ws.commits if m.startswith("build(")] == [
        "build(frontend): Frontend pages",
        "build(wiring): Routing and identity",
    ]


async def test_a_crash_mid_build_still_records_the_phases_that_finished(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Otherwise the next build re-runs work that was already written AND committed."""
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run()
    assert project.id is not None
    ws = FakeWorkspace()
    ctx = ToolContext.build(
        project, run, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )
    # Turns for the plan and phase 1 only — phase 2 exhausts the transport and raises.
    transport = phased_transport(_PHASES, [phase_write("backend/src/features/a/a.ts")])

    with pytest.raises(IndexError):
        await CodegenAgent(AnthropicClient(transport), default_registry()).run(
            project, run, ctx=ctx
        )

    previous = await CodegenAgent()._previous_report(project.id)
    assert previous is not None
    assert [(p.id, p.status) for p in previous.phases] == [("be-core", "done")]
    assert previous.stop_reason == "failed"


async def test_a_fresh_rebuild_ignores_the_previous_phase_statuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`fresh` is the regenerate-everything escape hatch — it must not resume."""
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run()
    assert project.id is not None
    await _seed_report(project.id, ["done", "done", "pending"])

    ws = FakeWorkspace()
    ws.files["backend/src/features/t/t.ts"] = "export {};"
    ws.files["pnpm-workspace.yaml"] = "packages:\n"
    ctx = ToolContext.build(
        project, run, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )
    transport = phased_transport(  # all three loops must be consumed
        _PHASES,
        [
            phase_write("backend/src/features/a/a.ts"),
            phase_write("frontend/src/features/b/B.tsx"),
            phase_write("frontend/src/routes.tsx"),
        ],
    )

    report = await CodegenAgent(AnthropicClient(transport), default_registry()).run(
        project, run, ctx=ctx, fresh=True
    )
    assert len([m for m in ws.commits if m.startswith("build(")]) == 3
    assert not any("Resuming" in note for note in report.notes)
