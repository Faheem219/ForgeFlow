"""Reporter parsers (phase-28): Vitest / Jest / Playwright JSON → unified ``TestResult[]``.

Pure functions (no I/O), so they're fully covered by fixtures. Normalizing three reporters into one
shape isolates the repair loop from framework quirks (§ design note). Two invariants matter for the
repair loop: the **criterion id** is recovered from the test title's ``[<id>]`` tag (phase-27), and
**referenced source files** are pulled out of failure stacks (input to phase-29).
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.testing.models import (
    FRAMEWORK_JEST,
    FRAMEWORK_PLAYWRIGHT,
    FRAMEWORK_VITEST,
    Failure,
    TestResult,
    TestStatus,
)

# The criterion-id tag testgen embeds in each test title, e.g. `it('[ac-1f2e] rejects …')`.
_CRITERION_RE = re.compile(r"\[([A-Za-z][\w.\-]*)\]")
# A source-file reference (optionally with :line:col) in a message/stack.
_FILE_RE = re.compile(r"([\w./\-]*[\w-]\.(?:tsx?|jsx?|mts|cts))(?::\d+(?::\d+)?)?")


def extract_criterion_id(title: str | None) -> str | None:
    """Recover the criterion id from a test title's ``[<id>]`` tag (the first one wins)."""
    if not title:
        return None
    match = _CRITERION_RE.search(title)
    return match.group(1) if match else None


def _normalize_path(path: str) -> str:
    marker = "/workspace/"
    if marker in path:
        path = path.split(marker, 1)[1]
    return path[2:] if path.startswith("./") else path


def extract_referenced_files(text: str | None) -> list[str]:
    """Source files named in a failure message/stack, node_modules stripped, order-preserving."""
    seen: list[str] = []
    for match in _FILE_RE.finditer(text or ""):
        path = _normalize_path(match.group(1))
        if not path or "node_modules" in path:
            continue
        if path not in seen:
            seen.append(path)
    return seen


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return (text or "").strip()


# --------------------------------------------------------------------- Jest / Vitest


def _norm_jest_status(status: str | None) -> TestStatus:
    if status == "passed":
        return TestStatus.passed
    if status == "failed":
        return TestStatus.failed
    return TestStatus.skipped  # pending / todo / skipped / disabled


def _parse_jest_like(json_text: str, framework: str) -> list[TestResult]:
    data = _load(json_text)
    results: list[TestResult] = []
    for suite in _as_list(data.get("testResults")):
        file = _normalize_path(str(suite.get("name", ""))) or None
        for assertion in _as_list(suite.get("assertionResults")):
            title = str(assertion.get("title") or "")
            full = str(assertion.get("fullName") or title)
            status = _norm_jest_status(assertion.get("status"))
            failure = None
            if status is TestStatus.failed:
                messages = [str(m) for m in _as_list(assertion.get("failureMessages"))]
                blob = "\n".join(messages)
                refs = extract_referenced_files(blob)
                if file and file not in refs:
                    refs.append(file)
                failure = Failure(
                    message=_first_line(blob) or "Test failed",
                    assertion=full or None,
                    stack=blob or None,
                    files_referenced=refs,
                )
            results.append(
                TestResult(
                    name=full or title,
                    status=status,
                    framework=framework,
                    criterion_id=extract_criterion_id(title) or extract_criterion_id(full),
                    file=file,
                    duration_ms=_as_float(assertion.get("duration")),
                    failure=failure,
                )
            )
    return results


def parse_jest(json_text: str) -> list[TestResult]:
    return _parse_jest_like(json_text, FRAMEWORK_JEST)


def parse_vitest(json_text: str) -> list[TestResult]:
    # Vitest's JSON reporter emulates Jest's shape, so the same walk applies.
    return _parse_jest_like(json_text, FRAMEWORK_VITEST)


# --------------------------------------------------------------------- Playwright


def _pw_status(
    tests: list[dict[str, Any]],
) -> tuple[TestStatus, float | None, dict[str, Any] | None]:
    """Collapse a spec's test attempts into one status + duration + first error."""
    duration = 0.0
    any_ran = False
    error: dict[str, Any] | None = None
    failed = False
    for test in tests:
        for result in _as_list(test.get("results")):
            any_ran = True
            duration += _as_float(result.get("duration")) or 0.0
            status = result.get("status")
            if status in ("failed", "timedOut", "interrupted"):
                failed = True
                if error is None and isinstance(result.get("error"), dict):
                    error = result["error"]
    if failed:
        return TestStatus.failed, duration, error
    if not any_ran:
        return TestStatus.skipped, None, None
    # A spec whose only status is "skipped" counts as skipped.
    statuses = {r.get("status") for t in tests for r in _as_list(t.get("results"))}
    if statuses == {"skipped"}:
        return TestStatus.skipped, duration, None
    return TestStatus.passed, duration, None


def parse_playwright(json_text: str) -> list[TestResult]:
    data = _load(json_text)
    results: list[TestResult] = []

    def walk(suites: list[dict[str, Any]]) -> None:
        for suite in suites:
            suite_file = str(suite.get("file", "")) or None
            for spec in _as_list(suite.get("specs")):
                title = str(spec.get("title") or "")
                file = _normalize_path(str(spec.get("file") or suite_file or "")) or None
                status, duration, error = _pw_status(_as_list(spec.get("tests")))
                failure = None
                if status is TestStatus.failed:
                    message = str((error or {}).get("message") or "")
                    stack = str((error or {}).get("stack") or "")
                    refs = extract_referenced_files(f"{message}\n{stack}")
                    if file and file not in refs:
                        refs.append(file)
                    failure = Failure(
                        message=_first_line(message) or "Test failed",
                        assertion=_strip_ansi(message) or None,
                        stack=stack or None,
                        files_referenced=refs,
                    )
                results.append(
                    TestResult(
                        name=title,
                        status=status,
                        framework=FRAMEWORK_PLAYWRIGHT,
                        criterion_id=extract_criterion_id(title),
                        file=file,
                        duration_ms=duration,
                        failure=failure,
                    )
                )
            walk(_as_list(suite.get("suites")))

    walk(_as_list(data.get("suites")))
    return results


# --------------------------------------------------------------------- dispatch + helpers


_PARSERS = {
    FRAMEWORK_VITEST: parse_vitest,
    FRAMEWORK_JEST: parse_jest,
    FRAMEWORK_PLAYWRIGHT: parse_playwright,
}


def parse(framework: str, json_text: str) -> list[TestResult]:
    parser = _PARSERS.get(framework)
    if parser is None:  # pragma: no cover - callers pass a known framework constant
        raise ValueError(f"Unknown test framework: {framework!r}")
    return parser(json_text)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text).strip()


def _load(json_text: str) -> dict[str, Any]:
    try:
        data = json.loads(json_text)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None
