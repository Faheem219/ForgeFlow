"""Runnable suites (phase-27 acceptance): a generated test actually executes in the sandbox.

Opt-in (needs a Node/pnpm toolchain), mirroring the phase-22 skeleton build test. It drives the real
test-gen agent (mock model) so it instantiates the skeleton and writes a criterion-tagged unit test,
materializes the resulting workspace to disk, and runs `pnpm --filter backend test:unit` — proving
the generated suite uses the skeleton conventions correctly (imports, config) and passes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.agents.anthropic_client import AnthropicClient
from app.agents.testgen import STATUS_GENERATED, TestGenAgent
from app.agents.tools.context import ToolContext
from app.agents.tools.skeleton import skeleton_source_dir
from app.core.config import reset_config
from app.db.models.enums import CriterionKind
from tests.agents.codegen_fakes import make_project_run, tool_use
from tests.agents.testgen_fakes import build_testgen_transport, feature, seed_spec
from tests.agents.tools.conftest import FakeExec, FakePreview, FakeWorkspace

pytestmark = pytest.mark.usefixtures("mongo_db")

_PNPM = shutil.which("pnpm")
_BUILD_ENABLED = os.getenv("FORGEFLOW_SKELETON_BUILD") == "1" and _PNPM is not None

_UNIT_FILE = "backend/src/features/health/health.test.ts"
# A genuinely runnable Jest+supertest test against the skeleton's createApp() + /health route,
# tagged with its criterion id per the traceability convention.
_UNIT_SRC = """// @criteria: ac-health
import request from 'supertest'

import { createApp } from '../../app'

describe('health feature', () => {
  it('[ac-health] GET /health returns ok', async () => {
    const res = await request(createApp()).get('/health')
    expect(res.status).toBe(200)
    expect(res.body.status).toBe('ok')
  })
})
"""


@pytest.mark.docker
@pytest.mark.skipif(
    not _BUILD_ENABLED,
    reason="set FORGEFLOW_SKELETON_BUILD=1 (with pnpm available) to run the real suite",
)
async def test_generated_unit_suite_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert _PNPM is not None  # narrowed by the skip guard
    # Point the skeleton copy at the real template + filesystem blobs (no gridfs).
    monkeypatch.setenv("APP_SKELETON_DIR", str(skeleton_source_dir()))
    monkeypatch.setenv("BLOB_BACKEND", "filesystem")
    monkeypatch.setenv("BLOB_FS_DIR", str(tmp_path / "blobs"))
    reset_config()

    project, run = await make_project_run("Health")
    assert project.id is not None
    await seed_spec(
        project.id, [feature("Health", [("ac-health", "health returns ok", CriterionKind.unit)])]
    )

    ws = FakeWorkspace()
    ctx = ToolContext.build(
        project, run, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )
    transport = build_testgen_transport(
        "Plan: one backend unit test for ac-health.",
        [
            tool_use("write_file", {"path": _UNIT_FILE, "content": _UNIT_SRC}),
            tool_use("git_commit", {"message": "tests"}),
        ],
    )
    report = await TestGenAgent(AnthropicClient(transport)).run(project, run, ctx=ctx)
    assert report.status == STATUS_GENERATED
    assert "ac-health" in report.criteria_covered

    # Materialize the in-memory workspace (skeleton + generated test) to disk and run it for real.
    dest = tmp_path / "app"
    for rel, content in ws.files.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    subprocess.run(["git", "init"], cwd=dest, check=True)
    subprocess.run([_PNPM, "install", "--no-frozen-lockfile"], cwd=dest, check=True)
    subprocess.run([_PNPM, "--filter", "backend", "run", "test:unit"], cwd=dest, check=True)
