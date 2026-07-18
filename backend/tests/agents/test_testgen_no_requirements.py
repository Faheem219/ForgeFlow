"""Non-linearity (phase-27, D12): with the requirements stage skipped, test-gen never guesses
silently. By default it warns and offers *capture or infer*; only when the caller picks ``infer``
does it derive a minimal spec from design/build — and that spec is labeled ``inferred``."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.anthropic_client import AnthropicClient
from app.agents.testgen import (
    STATUS_GENERATED,
    STATUS_NEEDS_REQUIREMENTS,
    MissingRequirements,
    TestGenAgent,
)
from app.agents.tools.context import ToolContext
from app.db.models import Project, Run
from app.db.repos import RequirementSpecRepo, TestSuiteRepo
from app.orchestrator.requirements import RequirementsService
from tests.agents.codegen_fakes import configure_env, make_project_run, make_skeleton, tool_use
from tests.agents.testgen_fakes import build_testgen_transport, unit_test_content
from tests.agents.tools.conftest import FakeExec, FakePreview, FakeWorkspace

pytestmark = pytest.mark.usefixtures("mongo_db")


def _ctx(project: Project, run: Run) -> ToolContext:
    return ToolContext.build(
        project, run, workspace=FakeWorkspace(), exec_service=FakeExec(), preview=FakePreview()
    )


async def test_ask_warns_and_offers_capture_or_infer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run()
    assert project.id is not None

    # Default (ask): no spec → warn + offer, generate nothing (no model turns are consumed).
    report = await TestGenAgent(AnthropicClient(build_testgen_transport("", []))).run(
        project, run, ctx=_ctx(project, run)
    )

    assert report.status == STATUS_NEEDS_REQUIREMENTS
    assert report.options == ["capture", "infer"]
    assert report.warning and "requirements" in report.warning.lower()
    assert report.files == []
    # Nothing was persisted.
    assert await TestSuiteRepo().list_for_project(project.id) == []
    assert await RequirementSpecRepo().latest(project.id) is None


async def test_infer_derives_a_labeled_spec_then_generates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run("Notes App")
    assert project.id is not None

    infer_json = (
        '[{"name": "Notes", "description": "Jot notes", '
        '"acceptance_criteria": [{"text": "a note can be added", "kind": "e2e"}]}]'
    )
    transport = build_testgen_transport(
        "Plan: one e2e for the inferred Notes feature.",
        [
            tool_use(
                "write_file",
                {"path": "frontend/e2e/notes.spec.ts", "content": unit_test_content(["x"])},
            )
        ],
        infer_json=infer_json,
    )

    report = await TestGenAgent(AnthropicClient(transport)).run(
        project, run, ctx=_ctx(project, run), missing=MissingRequirements.infer
    )

    assert report.status == STATUS_GENERATED
    assert report.inferred_spec is True

    # A real, labeled spec now exists with the inferred feature.
    spec = await RequirementsService().latest(project.id)
    assert spec is not None
    assert spec.inferred is True
    assert [f.name for f in spec.features] == ["Notes"]
    # Its criterion got a minted, stable id (the join key) even though requirements were skipped.
    assert spec.features[0].acceptance_criteria[0].id.startswith("ac-")


async def test_infer_falls_back_when_model_returns_nothing_parseable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run("Blog")
    assert project.id is not None

    transport = build_testgen_transport(
        "Plan.",
        [
            tool_use(
                "write_file",
                {
                    "path": "backend/src/features/core/core.test.ts",
                    "content": unit_test_content(["y"]),
                },
            )
        ],
        infer_json="sorry, I cannot help with that",  # no JSON → fallback minimal spec
    )

    report = await TestGenAgent(AnthropicClient(transport)).run(
        project, run, ctx=_ctx(project, run), missing=MissingRequirements.infer
    )

    assert report.status == STATUS_GENERATED
    assert report.inferred_spec is True
    spec = await RequirementsService().latest(project.id)
    assert spec is not None and spec.inferred is True and spec.features  # a fallback spec exists
