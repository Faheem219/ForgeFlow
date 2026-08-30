"""Synthetic test results adapt non-test build failures into the repair oracle (phase-55 task 10).

The critical property is re-rooting: a ``tsc`` path is package-relative, and unless it is re-rooted
to ``frontend/src/…`` the repair context analyzer cannot find it and the loop targets nothing.
"""

from __future__ import annotations

import pytest

from app.sandbox.schemas import PreviewProcess
from app.testing.models import TestStatus
from app.testing.synthetic import (
    boot_failure,
    command_failure,
    persist_synthetic_run,
    typecheck_failures,
)

_TSC = (
    "frontend typecheck: src/features/todos/TodoPage.tsx(12,5): error TS2339: Property 'x' ...\n"
    "frontend typecheck: src/features/todos/TodoPage.tsx(20,1): error TS2304: Cannot find 'y'.\n"
    "backend typecheck: src/features/todos/todos.controller.ts(3,10): error TS2551: ...\n"
)


def test_typecheck_paths_are_rerooted_and_grouped_by_file() -> None:
    results = typecheck_failures(_TSC)

    files = [r.file for r in results]
    assert files == [
        "frontend/src/features/todos/TodoPage.tsx",
        "backend/src/features/todos/todos.controller.ts",
    ]
    assert all(r.status is TestStatus.failed for r in results)

    # The FE file had two errors → one result, both errors retained, the file listed as editable.
    fe = results[0]
    assert fe.failure is not None
    assert fe.failure.stack is not None
    assert "TS2339" in fe.failure.stack and "TS2304" in fe.failure.stack
    assert fe.failure.files_referenced == ["frontend/src/features/todos/TodoPage.tsx"]


def test_already_rooted_paths_are_left_alone() -> None:
    results = typecheck_failures("frontend/src/App.tsx(1,1): error TS2304: Cannot find name.\n")
    assert [r.file for r in results] == ["frontend/src/App.tsx"]


def test_typecheck_results_are_capped() -> None:
    assert len(typecheck_failures(_TSC, max_results=1)) == 1


def test_boot_failure_pulls_source_files_from_the_log() -> None:
    log = "Error: connect ECONNREFUSED\n    at /workspace/backend/src/db.ts:10:5\n"
    result = boot_failure(PreviewProcess.backend, log, fallback_files=[])
    assert result.status is TestStatus.failed
    assert result.failure is not None
    assert result.failure.files_referenced == [
        "backend/src/db.ts"
    ]  # node_modules stripped, rerooted


def test_boot_failure_seeds_from_fallback_when_the_log_names_only_node_modules() -> None:
    log = "at /workspace/backend/node_modules/mongoose/lib/index.js:1:1\n"
    # extract yields nothing (node_modules stripped) → the fallback is the seed.
    result = boot_failure(
        PreviewProcess.backend, log, fallback_files=["backend/src/features/x/x.controller.ts"]
    )
    assert result.failure is not None
    assert result.failure.files_referenced == ["backend/src/features/x/x.controller.ts"]


def test_boot_failure_with_no_evidence_at_all_is_empty() -> None:
    result = boot_failure(PreviewProcess.frontend, "vite: not found\n", fallback_files=[])
    assert result.failure is not None
    assert result.failure.files_referenced == []


def test_command_failure_carries_exit_and_fallback() -> None:
    result = command_failure(
        "install", ["pnpm", "install"], 1, "some output", fallback_files=["backend/src/a.ts"]
    )
    assert result.failure is not None
    assert "exited 1" in result.failure.message
    assert "backend/src/a.ts" in result.failure.files_referenced


@pytest.mark.usefixtures("mongo_db")
async def test_persist_synthetic_run_records_failures() -> None:
    from beanie import PydanticObjectId

    results = typecheck_failures(_TSC)
    run = await persist_synthetic_run(PydanticObjectId(), results)
    assert len(run.failures) == 2
    assert len(run.results) == 2
