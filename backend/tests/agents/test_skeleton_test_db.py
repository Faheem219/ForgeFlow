"""The skeleton ships a working offline test database (phase-65).

Runs against the real ``templates/app-skeleton``. The helper is what lets a model write a real
data test with one import instead of improvising a harness that needs the internet — the failure
that was reported. These pin the wiring a later edit could quietly break: the dependency must be
resolvable offline (lockfile), must not download on install (``-core``), must actually be started by
Jest, must stay out of the production build, and must read as scaffold rather than feature code.
"""

from __future__ import annotations

import json
import re

from app.agents.tools.skeleton import skeleton_relpaths, skeleton_source_dir
from app.orchestrator.stages.build_verify import _FEATURE_ROOT_BE, feature_files
from app.sandbox.offline import TEST_DB_UNAVAILABLE_MARKER

_HELPER_FILES = (
    "backend/src/test/db.ts",
    "backend/src/test/db.test.ts",
    "backend/src/test/globalSetup.ts",
    "backend/src/test/globalTeardown.ts",
)


def _read(rel: str) -> str:
    return (skeleton_source_dir() / rel).read_text(encoding="utf-8")


def test_the_core_package_is_a_backend_dev_dependency() -> None:
    manifest = json.loads(_read("backend/package.json"))

    assert "mongodb-memory-server-core" in manifest["devDependencies"]
    # The non-core package downloads ~105 MB in a postinstall hook — on every install, including
    # the deployed backend's build. It must not be the one the skeleton depends on.
    assert "mongodb-memory-server" not in manifest["devDependencies"]
    assert "mongodb-memory-server" not in manifest.get("dependencies", {})


def test_the_dependency_is_locked() -> None:
    """Resolvable from the lockfile, so a sandbox install is deterministic."""
    lock = _read("pnpm-lock.yaml")

    # The importer entry under `backend`, and the resolved package itself.
    assert re.search(r"^\s+mongodb-memory-server-core:\s*$", lock, re.MULTILINE)
    assert re.search(r"^\s+mongodb-memory-server-core@\d+\.\d+\.\d+:\s*$", lock, re.MULTILINE)


def test_jest_starts_and_stops_one_server_per_run() -> None:
    config = _read("backend/jest.config.js")

    setup = re.search(r"globalSetup:\s*'<rootDir>/([^']+)'", config)
    teardown = re.search(r"globalTeardown:\s*'<rootDir>/([^']+)'", config)
    assert setup and teardown
    for rel in (setup.group(1), teardown.group(1)):
        assert (skeleton_source_dir() / "backend" / rel).is_file(), f"jest points at missing {rel}"


def test_jest_worker_pool_is_bounded_for_the_sandbox() -> None:
    """Jest sizes its pool from the host's cores; the sandbox has one CPU and 1 GB."""
    match = re.search(r"maxWorkers:\s*(\d+)", _read("backend/jest.config.js"))

    assert match and 1 <= int(match.group(1)) <= 2


def test_the_setup_publishes_the_uri_every_launcher_sees() -> None:
    """Agent run_tests, the Test stage and a plain `pnpm test` all land on the same server."""
    setup = _read("backend/src/test/globalSetup.ts")

    assert "process.env.MONGODB_URI" in setup
    assert "process.env.FORGEFLOW_TEST_MONGODB_URI" in setup
    assert "FORGEFLOW_TEST_DB_ERROR" in setup  # fail soft, with the reason


def test_the_helper_exports_use_test_db_and_fails_recognisably() -> None:
    helper = _read("backend/src/test/db.ts")

    assert "export function useTestDb()" in helper
    # The control plane recognises this marker and stops the repair loop (app.sandbox.offline).
    assert TEST_DB_UNAVAILABLE_MARKER in helper


def test_test_tooling_stays_out_of_the_production_build() -> None:
    build = json.loads(_read("backend/tsconfig.build.json"))

    assert "src/test" in build["exclude"]


def test_the_helper_is_scaffold_not_feature_code() -> None:
    shipped = skeleton_relpaths()

    for rel in _HELPER_FILES:
        assert rel in shipped, f"{rel} must be classified as scaffold"
    # phase-64's structural check must never count the helper as the app's own backend code.
    assert feature_files(_HELPER_FILES, _FEATURE_ROOT_BE) == []
