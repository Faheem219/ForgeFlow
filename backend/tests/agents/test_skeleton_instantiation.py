"""Phase-22: the real app-skeleton template instantiates deterministically into a workspace.

The fast tests assert the shipped template is structurally complete and copies deterministically
(no model tokens, no DB) — this is what `instantiate_skeleton` (phase-21) relies on. The heavy
``docker``-marked test actually installs + builds it; it is opt-in (needs a Node/pnpm toolchain) and
mirrors the CI ``skeleton`` job.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from beanie import PydanticObjectId

from app.agents.tools.skeleton import copy_skeleton, skeleton_source_dir
from app.db.models import Project
from tests.agents.tools.conftest import FakeWorkspace

# The scaffold every instantiated app must contain — the parts the codegen agent never regenerates.
_REQUIRED_FILES = [
    # workspace root
    "package.json",
    "pnpm-workspace.yaml",
    ".gitignore",
    ".env.example",
    "README.md",
    # frontend scaffold
    "frontend/package.json",
    "frontend/index.html",
    "frontend/vite.config.ts",
    "frontend/tsconfig.json",
    "frontend/tailwind.config.ts",
    "frontend/postcss.config.js",
    "frontend/playwright.config.ts",
    "frontend/src/main.tsx",
    "frontend/src/index.css",
    "frontend/src/routes.tsx",
    "frontend/src/lib/api.ts",
    "frontend/src/components/Layout.tsx",
    "frontend/src/pages/HomePage.tsx",
    # frontend tests
    "frontend/src/App.test.tsx",
    "frontend/src/test/setup.ts",
    "frontend/e2e/home.spec.ts",
    # backend scaffold
    "backend/package.json",
    "backend/tsconfig.json",
    "backend/tsconfig.build.json",
    "backend/jest.config.js",
    "backend/src/app.ts",
    "backend/src/index.ts",
    "backend/src/config.ts",
    "backend/src/db.ts",
    "backend/src/routes/health.ts",
    "backend/src/middleware/errorHandler.ts",
    "backend/src/middleware/requestLogger.ts",
    "backend/src/lib/validate.ts",
    # backend tests
    "backend/src/app.test.ts",
    "backend/src/db.test.ts",
]


def _project() -> Project:
    # In-memory only; FakeWorkspace ignores the project, so no DB/Beanie init is needed.
    return Project(user_id=PydanticObjectId(), name="skeleton-test", app_db_name="db")


def test_skeleton_source_exists() -> None:
    src = skeleton_source_dir()
    assert src.is_dir(), f"app-skeleton template missing at {src}"


async def test_instantiates_full_scaffold() -> None:
    ws = FakeWorkspace()
    written = await copy_skeleton(ws, _project())

    for rel in _REQUIRED_FILES:
        assert rel in ws.files, f"skeleton is missing scaffold file: {rel}"
    # Deterministic copy → unique paths, README placeholder is gone (real docs shipped).
    assert len(written) == len(set(written))
    assert "node_modules/" not in "".join(ws.files)


async def test_no_feature_code_ships_in_template() -> None:
    ws = FakeWorkspace()
    await copy_skeleton(ws, _project())
    # features/ dirs ship only their README convention guide — never generated app code.
    feature_files = [p for p in ws.files if "/features/" in p]
    assert feature_files, "feature-folder convention docs should ship"
    assert all(p.endswith("README.md") for p in feature_files), feature_files


async def test_env_contract_is_documented() -> None:
    ws = FakeWorkspace()
    await copy_skeleton(ws, _project())
    root_env = ws.files[".env.example"]
    for var in ("MONGODB_URI", "VITE_API_BASE_URL", "PORT", "NODE_ENV"):
        assert var in root_env, f".env.example must document {var}"


async def test_root_scripts_match_run_tests_tool() -> None:
    """Root scripts must satisfy the run_tests tool's commands (agents/tools/definitions.py)."""
    ws = FakeWorkspace()
    await copy_skeleton(ws, _project())
    scripts = json.loads(ws.files["package.json"])["scripts"]
    # run_tests: all→`pnpm test`, unit→`pnpm test:unit`, e2e→`pnpm test:e2e`; preview: `pnpm dev`.
    for required in ("dev", "build", "test", "test:unit", "test:e2e"):
        assert required in scripts, f"root package.json missing script: {required}"


async def test_copy_is_deterministic() -> None:
    ws1, ws2 = FakeWorkspace(), FakeWorkspace()
    written1 = await copy_skeleton(ws1, _project())
    written2 = await copy_skeleton(ws2, _project())
    assert written1 == written2
    assert ws1.files == ws2.files


# --------------------------------------------------------------------- heavy build (opt-in)

_PNPM = shutil.which("pnpm")
_BUILD_ENABLED = os.getenv("FORGEFLOW_SKELETON_BUILD") == "1" and _PNPM is not None


@pytest.mark.docker
@pytest.mark.skipif(
    not _BUILD_ENABLED,
    reason="set FORGEFLOW_SKELETON_BUILD=1 (with pnpm available) to run the real build",
)
def test_skeleton_installs_and_builds(tmp_path: Path) -> None:
    """Copy → git init → pnpm install → build → unit tests, exactly as instantiation does."""
    assert _PNPM is not None  # narrowed by the skip guard
    dest = tmp_path / "app"
    shutil.copytree(skeleton_source_dir(), dest)

    subprocess.run(["git", "init"], cwd=dest, check=True)
    subprocess.run([_PNPM, "install", "--no-frozen-lockfile"], cwd=dest, check=True)
    subprocess.run([_PNPM, "-r", "build"], cwd=dest, check=True)
    subprocess.run([_PNPM, "test:unit"], cwd=dest, check=True)
