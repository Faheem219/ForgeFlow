"""The skeleton path set is DERIVED from the template, never hand-maintained (phase-54).

The drift regression guard: before phase-54, `_existing_code` filtered scaffold using a 7-entry
literal while the skeleton shipped 17+ files under `{frontend,backend}/src`. Files like
`HomePage.tsx`, `Layout.tsx` and `App.test.tsx` were therefore advertised to the codegen model as
"existing feature code — do not rewrite" on the very first build, so the demo placeholder survived
into generated apps. These tests run against the **real** `templates/app-skeleton`, so the set can
never silently fall behind the skeleton again.
"""

from __future__ import annotations

import pytest

from app.agents.codegen import CodegenAgent
from app.agents.tools.context import ToolContext
from app.agents.tools.definitions import default_registry
from app.agents.tools.skeleton import copy_skeleton, skeleton_relpaths, skeleton_source_dir
from tests.agents.codegen_fakes import make_project_run
from tests.agents.tools.conftest import FakeExec, FakePreview, FakeWorkspace


def test_skeleton_relpaths_covers_every_src_file() -> None:
    """`skeleton_relpaths()` ⊇ every UTF-8 file under the real template's feature-code roots."""
    src = skeleton_source_dir()
    assert src.is_dir(), f"real app-skeleton template missing at {src}"
    shipped = skeleton_relpaths()

    on_disk: list[str] = []
    for root in ("frontend/src", "backend/src"):
        base = src / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            try:
                path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue  # binary asset — copy_skeleton skips it too
            on_disk.append(path.relative_to(src).as_posix())

    assert on_disk, "sanity: the template ships source files under frontend/src + backend/src"
    missing = sorted(set(on_disk) - shipped)
    assert not missing, f"skeleton_relpaths() has drifted behind the skeleton; missing: {missing}"


def test_demo_placeholder_files_are_classified_as_scaffold() -> None:
    """The exact files the old literal omitted — the ones that leaked the demo into built apps."""
    shipped = skeleton_relpaths()
    for rel in (
        "frontend/src/pages/HomePage.tsx",
        "frontend/src/components/Layout.tsx",
        "frontend/src/App.test.tsx",
        "frontend/e2e/home.spec.ts",
        "frontend/src/routes.tsx",
        "frontend/index.html",
    ):
        assert rel in shipped, f"{rel} must be treated as scaffold, not prior feature work"


@pytest.mark.usefixtures("mongo_db")
async def test_existing_code_on_a_fresh_skeleton_is_empty() -> None:
    """On a freshly instantiated workspace, `_existing_code` reports NO feature code.

    This is what makes `format_existing` emit "this is the first build on a bare skeleton" and
    stops the "do not rewrite" block from ever firing on placeholder files.
    """
    project, run = await make_project_run()
    ws = FakeWorkspace()
    await copy_skeleton(ws, project)  # the real skeleton, into the in-memory workspace
    ctx = ToolContext.build(
        project, run, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )

    inventory, paths = await CodegenAgent(registry=default_registry())._existing_code(ctx)
    assert (inventory, paths) == (None, [])
