"""Unified test-result model + API schemas (phase-28).

Three reporters (Vitest, Jest, Playwright) are normalized into one :class:`TestResult` shape so the
failure analyzer + repair agent (Epic 6) see a single schema regardless of framework. The persisted
:class:`~app.db.models.test_run.TestRun` stores these as plain dicts; the API re-validates them.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

# Framework identifiers (also the parser dispatch keys).
FRAMEWORK_VITEST = "vitest"
FRAMEWORK_JEST = "jest"
FRAMEWORK_PLAYWRIGHT = "playwright"

TestScope = Literal["unit", "e2e", "all"]


class TestStatus(StrEnum):
    passed = "passed"
    failed = "failed"
    skipped = "skipped"


class Failure(BaseModel):
    """Actionable failure detail — the input to minimal repair context (phase-29)."""

    message: str
    assertion: str | None = None
    stack: str | None = None
    # Source files named in the message/stack (node_modules stripped) — where a fix likely goes.
    files_referenced: list[str] = Field(default_factory=list)


class TestResult(BaseModel):
    name: str
    status: TestStatus
    framework: str
    # Carried through from the tagged test title `[<id>]` (phase-27) — the requirements join key.
    criterion_id: str | None = None
    file: str | None = None
    duration_ms: float | None = None
    failure: Failure | None = None


class TestRunSummary(BaseModel):
    """Counts derived from a run's results (never persisted — computed on read)."""

    total: int
    passed: int
    failed: int
    skipped: int
    green: bool


def summarize(results: list[TestResult]) -> TestRunSummary:
    passed = sum(1 for r in results if r.status is TestStatus.passed)
    failed = sum(1 for r in results if r.status is TestStatus.failed)
    skipped = sum(1 for r in results if r.status is TestStatus.skipped)
    # Green = something actually passed and nothing failed (an empty/all-skipped run is NOT green,
    # so it never advances the last-passing ref).
    green = failed == 0 and passed > 0
    return TestRunSummary(
        total=len(results), passed=passed, failed=failed, skipped=skipped, green=green
    )


# --------------------------------------------------------------------- API schemas


class RunTestsRequest(BaseModel):
    scope: TestScope = "all"
    filter: str | None = Field(default=None, description="Optional test-name filter")


class TestRunSummaryPublic(BaseModel):
    id: str
    total: int
    passed: int
    failed: int
    skipped: int
    green: bool
    env: str
    created_at: str


class TestRunPublic(TestRunSummaryPublic):
    results: list[TestResult]
    failures: list[TestResult]
    suite_refs: list[str]
    stdout_ref: str | None


class TestStdoutPublic(BaseModel):
    """A run's captured reporter output, resolved from its blob (phase-32 stdout viewer)."""

    id: str
    stdout: str
