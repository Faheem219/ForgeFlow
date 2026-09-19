"""Phase-by-phase codegen (phase-56): one model loop, one gate and one commit per phase.

The point of phasing is a *small* context per phase, so the assertions here are as much about what
each prompt does **not** carry (the accumulated transcript of previous phases) as about what it
does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

import pytest

from app.agents.anthropic_client import AnthropicClient
from app.agents.build_phases import BUILD_PLAN_KIND
from app.agents.codegen import BuildReport, CodegenAgent
from app.agents.tools.context import ToolContext
from app.agents.tools.definitions import default_registry
from app.core.config import reset_config
from app.db.models import Project, Run
from app.db.models.enums import ArtifactType, Stage
from app.orchestrator.artifacts import ArtifactService
from app.realtime.hub import get_hub
from tests.agents.codegen_fakes import (
    configure_env,
    make_project_run,
    make_skeleton,
    phase_write,
    phased_transport,
    tool_use,
)
from tests.agents.test_client_tool_loop import FakeTransport
from tests.agents.tools.conftest import FakeExec, FakePreview, FakeWorkspace

pytestmark = pytest.mark.usefixtures("mongo_db")

_PHASES = [
    ("be-core", "Backend API", "backend"),
    ("fe-core", "Frontend pages", "frontend"),
    ("wiring", "Routing and identity", "wiring"),
]


class Built(NamedTuple):
    report: BuildReport
    project: Project
    run: Run
    ws: FakeWorkspace
    transport: FakeTransport


async def _build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Built:
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run()
    ws = FakeWorkspace()
    ctx = ToolContext.build(
        project, run, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )
    transport = phased_transport(
        _PHASES,
        [
            [tool_use("write_file", {"path": "backend/src/features/t/t.ts", "content": "x"})],
            [tool_use("write_file", {"path": "frontend/src/features/t/T.tsx", "content": "x"})],
            [tool_use("write_file", {"path": "frontend/src/routes.tsx", "content": "x"})],
        ],
    )
    report = await CodegenAgent(AnthropicClient(transport), default_registry()).run(
        project, run, ctx=ctx
    )
    return Built(report, project, run, ws, transport)


async def test_one_loop_and_one_commit_per_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report, _project, _run, ws, _transport = await _build(monkeypatch, tmp_path)

    assert [p.id for p in report.phases] == ["be-core", "fe-core", "wiring"]
    assert all(p.status == "done" for p in report.phases)
    assert report.outcome == "complete"

    # One commit per phase, named after it — this is what makes diffs phase-attributable.
    phase_commits = [m for m in ws.commits if m.startswith("build(")]
    assert phase_commits == [
        "build(backend): Backend API",
        "build(frontend): Frontend pages",
        "build(wiring): Routing and identity",
    ]
    assert all(p.commit for p in report.phases)


async def test_each_phase_prompt_is_small_and_not_the_previous_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _report, _project, _run, _ws, transport = await _build(monkeypatch, tmp_path)

    # The transport records every request; a phase loop makes several, all sharing one first
    # message — which is exactly the small context under test.
    openings: list[str] = []
    for request in transport.requests:
        content = str(request.messages[0]["content"])
        if "Phase " in content and content not in openings:
            openings.append(content)
    assert len(openings) == 3

    second = openings[1]
    assert "Phase 2 of 3" in second
    assert "Story so far: Backend API (done)" in second  # a summary…
    assert "Implementing…" not in second  # …NOT the previous phase's transcript
    assert len(second) < 4000  # the whole point: a small context


async def test_phase_events_and_run_progress_track_the_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _report, project, run, _ws, _transport = await _build(monkeypatch, tmp_path)

    events = [e for e in get_hub().replay(str(project.id), 0) if e.event == "build.phase"]
    # Announced twice per phase: running, then its terminal status.
    assert [e.payload["phase_id"] for e in events] == [
        "be-core",
        "be-core",
        "fe-core",
        "fe-core",
        "wiring",
        "wiring",
    ]
    assert [e.payload["status"] for e in events][:2] == ["running", "done"]
    assert all(e.payload["total"] == 3 for e in events)

    # Mirrored onto the Run so a reload reattaches to the right phase.
    assert run.progress.phase_total == 3
    assert run.progress.phase_index == 3
    assert run.progress.phase_id == "wiring"
    assert run.progress.step == "report"  # the build moved on once every phase was written


async def test_the_plan_is_persisted_as_an_artifact_and_into_the_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _report, project, _run, ws, _transport = await _build(monkeypatch, tmp_path)
    assert project.id is not None

    latest = await ArtifactService().get_latest_of_kind(
        project.id, Stage.build, ArtifactType.code_change, BUILD_PLAN_KIND
    )
    assert latest is not None
    assert latest.meta["phase_count"] == 3
    assert latest.meta["phase_ids"] == ["be-core", "fe-core", "wiring"]

    # …and mirrored into the workspace as a sibling of frontend/ and backend/.
    assert "phase-plan/plan.json" in ws.files
    assert "phase-plan/README.md" in ws.files
    payload = json.loads(ws.files["phase-plan/plan.json"])
    # Rewritten after EVERY phase, so the checklist visibly fills in.
    assert payload["outcomes"] == {"be-core": "done", "fe-core": "done", "wiring": "done"}
    assert "- [x] **1. Backend API**" in ws.files["phase-plan/README.md"]


async def test_the_workspace_copy_can_be_turned_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`build_plan_in_workspace=False` keeps the durable artifact but writes no workspace files."""
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)
    monkeypatch.setenv("BUILD_PLAN_IN_WORKSPACE", "false")
    reset_config()

    project, run = await make_project_run()
    ws = FakeWorkspace()
    ctx = ToolContext.build(
        project, run, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )
    transport = phased_transport([_PHASES[0]], [phase_write("backend/src/features/a/a.ts")])
    await CodegenAgent(AnthropicClient(transport), default_registry()).run(project, run, ctx=ctx)

    assert not any(path.startswith("phase-plan/") for path in ws.files)
    assert project.id is not None
    assert (
        await ArtifactService().get_latest_of_kind(
            project.id, Stage.build, ArtifactType.code_change, BUILD_PLAN_KIND
        )
        is not None
    )


async def test_the_plan_folder_name_follows_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)
    monkeypatch.setenv("BUILD_PLAN_DIR", "build-phases")
    reset_config()

    project, run = await make_project_run()
    ws = FakeWorkspace()
    ctx = ToolContext.build(
        project, run, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )
    await CodegenAgent(
        AnthropicClient(
            phased_transport([_PHASES[0]], [phase_write("backend/src/features/a/a.ts")])
        ),
        default_registry(),
    ).run(project, run, ctx=ctx)

    assert "build-phases/plan.json" in ws.files
    assert "phase-plan/plan.json" not in ws.files


async def test_an_unparseable_plan_falls_back_and_the_build_still_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D12 in one test: garbage from the planner must not be able to block a build."""
    from app.agents.cost import Usage
    from tests.agents.test_client_tool_loop import FakeTransport, ScriptedTurn

    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run()
    ws = FakeWorkspace()
    ctx = ToolContext.build(
        project, run, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )
    # A prose (non-JSON) plan turn, then one write + one closing turn per fallback phase (a phase
    # that writes nothing is no longer `done`, phase-64 — but that is not what this test is about).
    prose = "Just some prose, no JSON at all."

    def _turn(text: str) -> ScriptedTurn:
        from app.agents.anthropic_client import TurnComplete

        return ScriptedTurn(deltas=[text], turn=TurnComplete(text=text, usage=Usage(10, 5)))

    def _write(path: str) -> ScriptedTurn:
        from app.agents.anthropic_client import TurnComplete

        return ScriptedTurn(
            deltas=[],
            turn=TurnComplete(
                tool_uses=phase_write(path), usage=Usage(10, 5), stop_reason="tool_use"
            ),
        )

    transport = FakeTransport(
        [
            _turn(prose),
            _write("backend/src/features/a/a.ts"),
            _turn("a"),
            _write("frontend/src/features/b/B.tsx"),
            _turn("b"),
            _write("frontend/src/routes.tsx"),
            _turn("c"),
        ]
    )

    report = await CodegenAgent(AnthropicClient(transport), default_registry()).run(
        project, run, ctx=ctx
    )
    assert [p.id for p in report.phases] == ["be-core", "fe-core", "wiring"]
    assert project.id is not None
    latest = await ArtifactService().get_latest_of_kind(
        project.id, Stage.build, ArtifactType.code_change, BUILD_PLAN_KIND
    )
    assert latest is not None and latest.meta["source"] == "fallback"
