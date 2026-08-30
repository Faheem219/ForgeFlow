"""Incremental rebuilds: a second build must extend what is already on disk, not start over.

The waste this pins down: a build that dies part-way still leaves its files in the workspace, but
the agent was never told they existed — so the next build regenerated everything from scratch. The
fix is an inventory of existing feature code (paths + sizes) fed into both prompts, plus the last
build report; `fresh=True` deliberately withholds it for a full regeneration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.agents.anthropic_client import AnthropicClient, LoopResult
from app.agents.codegen import BuildReport, CodegenAgent
from app.agents.cost import Usage
from app.agents.tools.context import ToolContext
from app.agents.tools.definitions import default_registry
from app.db.models import Project
from app.db.models.enums import ArtifactType, Stage
from app.orchestrator.artifacts import ArtifactService
from tests.agents.codegen_fakes import (
    build_transport,
    configure_env,
    make_project_run,
    make_skeleton,
    tool_use,
)
from tests.agents.test_client_tool_loop import FakeTransport
from tests.agents.tools.conftest import FakeExec, FakePreview, FakeWorkspace

pytestmark = pytest.mark.usefixtures("mongo_db")

EXISTING = "backend/src/features/todo/todo.model.ts"
HALF_DONE = "backend/src/features/todo/todo.routes.ts"


def _prompts(transport: object) -> str:
    """Everything the model was asked, concatenated — the prompts are the contract under test."""
    chunks: list[str] = []
    for request in getattr(transport, "requests", []):
        chunks.append(str(getattr(request, "system", "")))
        for message in getattr(request, "messages", []):
            content = message.get("content")
            if isinstance(content, str):
                chunks.append(content)
            elif isinstance(content, list):
                chunks.extend(str(block.get("text", "")) for block in content)
    return "\n".join(chunks)


def _inventory_block(text: str) -> str:
    """Just the file listing — between the header and the instruction that follows it."""
    start = text.find("ALREADY ON DISK")
    assert start != -1, "no inventory block was sent"
    end = text.find("This is your record", start)
    return text[start : end if end != -1 else len(text)]


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    seed: dict[str, str] | None = None,
    fresh: bool = False,
) -> tuple[BuildReport, FakeTransport, FakeWorkspace, Project]:
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run()
    ws = FakeWorkspace()
    for path, content in (seed or {}).items():
        ws.files[path] = content
    ctx = ToolContext.build(
        project, run, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )
    transport = build_transport(
        "Plan: finish the todo feature.",
        [tool_use("write_file", {"path": HALF_DONE, "content": "export {};"})],
    )
    report = await CodegenAgent(AnthropicClient(transport), default_registry()).run(
        project, run, ctx=ctx, fresh=fresh
    )
    return report, transport, ws, project


async def test_existing_feature_code_is_shown_to_the_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, transport, _, _ = await _run(
        monkeypatch, tmp_path, seed={EXISTING: "export const Todo = 1;"}
    )

    text = _prompts(transport)
    # The path is named, so the model can tell what it already produced…
    assert EXISTING in text
    # …and is told plainly not to redo it.
    assert "ALREADY ON DISK" in text
    assert "do NOT rewrite" in text.lower() or "do not rewrite" in text.lower()


async def test_a_bare_workspace_is_reported_as_a_first_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, transport, _, _ = await _run(monkeypatch, tmp_path)

    text = _prompts(transport)
    assert "none — this is the first build on a bare skeleton" in text


async def test_skeleton_files_are_not_offered_as_prior_feature_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The skeleton lands in the workspace on every build; listing `app.ts` as existing *feature*
    # code would invite the model to treat scaffolding as its own output and edit it.
    _, transport, _, _ = await _run(
        monkeypatch, tmp_path, seed={EXISTING: "export const Todo = 1;"}
    )

    # Scope to the inventory listing itself: `app.ts` is also named in the system prompt (as the
    # file to mount routes in), which is correct and must not fail this assertion.
    inventory = _inventory_block(_prompts(transport))
    assert EXISTING in inventory, "sanity: the feature file should be listed"
    assert "backend/src/app.ts" not in inventory
    assert "frontend/src/routes.tsx" not in inventory


async def test_node_modules_never_reaches_the_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, transport, _, _ = await _run(
        monkeypatch,
        tmp_path,
        seed={EXISTING: "x", "backend/src/node_modules/dep/index.js": "junk"},
    )

    assert "node_modules" not in _prompts(transport)


async def test_fresh_rebuild_withholds_the_inventory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The explicit escape hatch: regenerate rather than extend.
    _, transport, _, _ = await _run(
        monkeypatch, tmp_path, seed={EXISTING: "export const Todo = 1;"}, fresh=True
    )

    text = _prompts(transport)
    assert "ALREADY ON DISK" not in text
    assert "Full regeneration requested" in text


async def test_a_failed_build_still_persists_what_it_wrote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A crash mid-build must not throw away the record of the files it produced."""
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run()
    ws = FakeWorkspace()
    ctx = ToolContext.build(
        project, run, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )

    class Exploding:
        """Writes one file, then the provider dies — the real failure mode users hit."""

        def __init__(self) -> None:
            self.calls = 0

        async def run_tool_loop(self, **kwargs: Any) -> LoopResult:
            self.calls += 1
            if self.calls == 1:  # the plan step succeeds
                return LoopResult(text="Plan: todo.", messages=[], usage=Usage(), turns=1)
            dispatch = kwargs["tool_dispatch"]
            await dispatch("write_file", {"path": HALF_DONE, "content": "export {};"})
            raise RuntimeError("provider exploded")

    agent = CodegenAgent(Exploding(), default_registry())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="provider exploded"):
        await agent.run(project, run, ctx=ctx)

    assert project.id is not None
    artifact = await ArtifactService().get_latest(project.id, Stage.build, ArtifactType.code_change)
    assert artifact is not None, "the failed build must leave a report behind"
    assert artifact.meta.get("failed") is True
    # The file it managed to write is recorded, so the next build can resume from it.
    assert HALF_DONE in artifact.meta.get("files_changed", [])
