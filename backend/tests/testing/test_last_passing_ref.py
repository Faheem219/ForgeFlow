"""Last-passing ref (phase-28): an all-green run advances the ref the repair loop diffs against;
a red run (or an empty/all-skipped run) leaves it untouched."""

from __future__ import annotations

import json

import pytest

from app.testing.runner import TestRunner
from tests.testing.conftest import (
    FakeRunnerWorkspace,
    ScriptedExec,
    jest_report,
    make_project,
    vitest_report,
)

pytestmark = pytest.mark.usefixtures("mongo_db", "blob_env")

_VITEST = "frontend/vitest-report.json"
_JEST = "backend/jest-report.json"


async def test_green_run_advances_the_ref() -> None:
    project = await make_project()
    workspace = FakeRunnerWorkspace(
        {_VITEST: vitest_report(), _JEST: jest_report(failing=False)}, current_sha="sha-green"
    )
    await TestRunner(exec_service=ScriptedExec(), workspace=workspace).run(project, scope="unit")
    assert workspace.last_passing == "sha-green"


async def test_red_run_does_not_advance_the_ref() -> None:
    project = await make_project()
    workspace = FakeRunnerWorkspace(
        {_VITEST: vitest_report(), _JEST: jest_report(failing=True)}, current_sha="sha-red"
    )
    await TestRunner(exec_service=ScriptedExec(exit_codes={"jest": 1}), workspace=workspace).run(
        project, scope="unit"
    )
    assert workspace.last_passing is None


async def test_all_skipped_run_does_not_advance_the_ref() -> None:
    project = await make_project()
    skipped = json.dumps(
        {
            "testResults": [
                {
                    "name": "src/x.test.ts",
                    "assertionResults": [
                        {"title": "[ac-x] later", "status": "pending", "failureMessages": []}
                    ],
                }
            ]
        }
    )
    workspace = FakeRunnerWorkspace({_JEST: skipped}, current_sha="sha-skip")
    await TestRunner(exec_service=ScriptedExec(), workspace=workspace).run(project, scope="unit")
    # Nothing actually passed → not green → the ref must not move.
    assert workspace.last_passing is None
