"""Reporter parsing (phase-28): three frameworks → one ``TestResult`` shape, with the criterion id
carried through from the test title and referenced source files pulled out of failure stacks."""

from __future__ import annotations

from app.testing.models import FRAMEWORK_JEST, FRAMEWORK_PLAYWRIGHT, FRAMEWORK_VITEST, TestStatus
from app.testing.parsers import (
    extract_criterion_id,
    extract_referenced_files,
    parse_jest,
    parse_playwright,
    parse_vitest,
)
from tests.testing.conftest import jest_report, playwright_report, vitest_report


def test_extract_criterion_id() -> None:
    assert extract_criterion_id("[ac-add] adds a todo") == "ac-add"
    assert extract_criterion_id("todos [ac-1f2e] does the thing") == "ac-1f2e"
    assert extract_criterion_id("handles array[0] access") is None  # digit-led → not an id
    assert extract_criterion_id("no tag here") is None
    assert extract_criterion_id(None) is None


def test_extract_referenced_files_strips_workspace_and_node_modules() -> None:
    text = (
        "at fn (/workspace/backend/src/a.ts:10:5)\n"
        "at node_modules/jest/build/x.js:1:1\n"
        "at ./frontend/src/b.tsx:3:9\n"
        "at /workspace/backend/src/a.ts:12:1"  # dup of a.ts (already seen)
    )
    assert extract_referenced_files(text) == ["backend/src/a.ts", "frontend/src/b.tsx"]


def test_parse_jest_unifies_pass_fail_skip_with_traceability() -> None:
    results = parse_jest(jest_report(failing=True))
    assert [r.framework for r in results] == [FRAMEWORK_JEST, FRAMEWORK_JEST]
    by_crit = {r.criterion_id: r for r in results}

    assert by_crit["ac-add"].status is TestStatus.passed
    failed = by_crit["ac-empty"]
    assert failed.status is TestStatus.failed
    assert failed.file == "backend/src/features/todos/todos.test.ts"
    assert failed.failure is not None
    assert failed.failure.message.startswith("Error: expect(received)")
    # Both the referenced source file and the test file are captured (input to phase-29).
    assert "backend/src/features/todos/todos.controller.ts" in failed.failure.files_referenced
    assert "backend/src/features/todos/todos.test.ts" in failed.failure.files_referenced


def test_parse_jest_marks_pending_as_skipped() -> None:
    import json

    report = json.dumps(
        {
            "testResults": [
                {
                    "name": "src/x.test.ts",
                    "assertionResults": [
                        {"title": "[ac-todo] later", "status": "pending", "failureMessages": []}
                    ],
                }
            ]
        }
    )
    results = parse_jest(report)
    assert results[0].status is TestStatus.skipped
    assert results[0].failure is None


def test_parse_vitest_shares_the_jest_shape() -> None:
    results = parse_vitest(vitest_report())
    assert len(results) == 1
    assert results[0].framework == FRAMEWORK_VITEST
    assert results[0].criterion_id == "ac-inc"
    assert results[0].status is TestStatus.passed


def test_parse_playwright_pass_and_fail() -> None:
    results = parse_playwright(playwright_report(failing=True))
    by_crit = {r.criterion_id: r for r in results}
    assert by_crit["ac-list"].status is TestStatus.passed
    assert by_crit["ac-list"].framework == FRAMEWORK_PLAYWRIGHT

    failed = by_crit["ac-uiadd"]
    assert failed.status is TestStatus.failed
    assert failed.file == "e2e/todos.spec.ts"
    assert failed.failure is not None
    assert "toBeVisible" in failed.failure.message
    assert "e2e/todos.spec.ts" in failed.failure.files_referenced


def test_parse_playwright_timed_out_is_failed() -> None:
    import json

    report = json.dumps(
        {
            "suites": [
                {
                    "title": "s",
                    "file": "e2e/s.spec.ts",
                    "specs": [
                        {
                            "title": "[ac-slow] slow flow",
                            "file": "e2e/s.spec.ts",
                            "tests": [{"results": [{"status": "timedOut", "duration": 30000}]}],
                        }
                    ],
                }
            ]
        }
    )
    results = parse_playwright(report)
    assert results[0].status is TestStatus.failed


def test_parsers_tolerate_garbage_json() -> None:
    assert parse_jest("not json") == []
    assert parse_vitest("") == []
    assert parse_playwright("{}") == []
