"""Synthetic test results for build verification (phase-55 task 10).

The repair loop's oracle is hard-typed to ``TestRun`` / ``TestResult`` (phase-28), so a build
failure that is *not* a test failure — a red typecheck, a dev server that never booted, a failed
command — must be adapted into that shape before it can reach the loop. This module does exactly
that, and nothing else: pure functions (plus one persistence helper), mirroring the
``_suite_failure`` idiom in :mod:`app.testing.runner`.

The load-bearing piece is :func:`typecheck_failures`. ``tsc`` prints
``src/features/todos/todos.controller.ts(12,5): error TS2339: …`` with paths **relative to the
package it ran in** (``frontend`` / ``backend``). Left unrooted, every path handed to the repair
context analyzer raises ``NotFoundError`` and the loop targets nothing — so they are re-rooted to
``frontend/src/…`` here, giving :func:`app.agents.repair_context._rank_paths` real, editable,
frequency-rankable source paths.
"""

from __future__ import annotations

import re

from beanie import PydanticObjectId

from app.db.blobs import BlobStore, get_blob_store
from app.db.models import TestRun
from app.db.models.enums import TestEnv
from app.sandbox.schemas import PreviewProcess
from app.testing.models import Failure, TestResult, TestStatus
from app.testing.parsers import extract_referenced_files

# Synthetic "frameworks" — a label on the result, never a real reporter. They land outside the
# {playwright, jest, vitest} set the repair fast-path keys on, so they map to the full re-run.
FRAMEWORK_TYPECHECK = "typecheck"
FRAMEWORK_BOOT = "boot"
FRAMEWORK_COMMAND = "command"

#: A tsc diagnostic line: ``<path>(<line>,<col>): error TS<code>: <message>``.
_TSC_LINE = re.compile(r"([\w./\-]+\.(?:tsx?|jsx?|mts|cts))\((\d+),(\d+)\):\s*(error\s+TS\d+:.*)")

_MESSAGE_ERRORS = 3  # how many of a file's errors go into the short message (all go into the stack)


def _pkg_from_prefix(prefix: str, package_dirs: tuple[str, ...]) -> str | None:
    """The package a ``pnpm -r`` line belongs to, read from its leading prefix token."""
    for token in prefix.replace("|", " ").replace(":", " ").split():
        if token in package_dirs:
            return token
    return None


def _reroot(path: str, pkg: str | None, package_dirs: tuple[str, ...]) -> str:
    """Make a package-relative tsc path workspace-relative (``src/x`` → ``frontend/src/x``)."""
    if any(path == d or path.startswith(f"{d}/") for d in package_dirs):
        return path  # already rooted at a package
    if pkg is not None:
        return f"{pkg}/{path}"
    return path  # unattributable — the widening analyzer's fallback (task 11) is the safety net


def typecheck_failures(
    output: str,
    *,
    package_dirs: tuple[str, ...] = ("frontend", "backend"),
    max_results: int = 20,
) -> list[TestResult]:
    """One :class:`TestResult` per file with a typecheck error, paths re-rooted to the workspace.

    Errors for the same file are grouped: the first few into ``failure.message``, all into
    ``failure.stack``. Capped at ``max_results`` files so a flood of errors cannot blow the budget.
    """
    by_file: dict[str, list[str]] = {}
    order: list[str] = []
    for raw in output.splitlines():
        match = _TSC_LINE.search(raw)
        if match is None:
            continue
        path, line, col, message = match.groups()
        prefix = raw[: match.start()]
        rooted = _reroot(path, _pkg_from_prefix(prefix, package_dirs), package_dirs)
        entry = f"{rooted}({line},{col}): {message.strip()}"
        if rooted not in by_file:
            by_file[rooted] = []
            order.append(rooted)
        by_file[rooted].append(entry)

    results: list[TestResult] = []
    for rooted in order[:max_results]:
        errors = by_file[rooted]
        results.append(
            TestResult(
                name=f"typecheck: {rooted}",
                status=TestStatus.failed,
                framework=FRAMEWORK_TYPECHECK,
                file=rooted,
                failure=Failure(
                    message="; ".join(errors[:_MESSAGE_ERRORS]),
                    assertion=None,
                    stack="\n".join(errors),
                    files_referenced=[rooted],
                ),
            )
        )
    return results


def boot_failure(
    process: PreviewProcess,
    log_tail: str,
    *,
    fallback_files: list[str],
) -> TestResult:
    """A dev server that never became healthy, as one failing :class:`TestResult`.

    ``files_referenced`` seeds the repair target set: files named in the log first, then
    ``fallback_files`` (what this build just wrote on the failing surface) — the "seed at source"
    layer of the non-empty-editable-set guarantee (task 11).
    """
    referenced = extract_referenced_files(log_tail)
    for path in fallback_files:
        if path not in referenced:
            referenced.append(path)
    return TestResult(
        name=f"boot: {process} dev server",
        status=TestStatus.failed,
        framework=FRAMEWORK_BOOT,
        file=None,
        failure=Failure(
            message=f"the {process} dev server did not become healthy",
            assertion=None,
            stack=log_tail or None,
            files_referenced=referenced,
        ),
    )


def command_failure(
    name: str,
    cmd: list[str],
    exit_code: int,
    output: str,
    *,
    timed_out: bool = False,
    fallback_files: list[str],
) -> TestResult:
    """A build command that failed (non-typecheck), as one failing :class:`TestResult`."""
    referenced = extract_referenced_files(output)
    for path in fallback_files:
        if path not in referenced:
            referenced.append(path)
    detail = f"`{' '.join(cmd)}` exited {exit_code}"
    if timed_out:
        detail += " (timed out)"
    return TestResult(
        name=f"{name}: {' '.join(cmd)}",
        status=TestStatus.failed,
        framework=FRAMEWORK_COMMAND,
        file=None,
        failure=Failure(
            message=detail,
            assertion=None,
            stack=output or None,
            files_referenced=referenced,
        ),
    )


async def persist_synthetic_run(
    project_id: PydanticObjectId,
    results: list[TestResult],
    *,
    stdout: str = "",
    blobs: BlobStore | None = None,
) -> TestRun:
    """Persist synthetic results as a sandbox ``TestRun`` the repair loop consumes like any run."""
    stdout_ref = await _store_stdout(stdout, blobs)
    return await TestRun(
        project_id=project_id,
        results=[r.model_dump(mode="json") for r in results],
        failures=[r.model_dump(mode="json") for r in results if r.status is TestStatus.failed],
        stdout_ref=stdout_ref,
        env=TestEnv.sandbox,
    ).insert()


async def _store_stdout(stdout: str, blobs: BlobStore | None) -> str | None:
    if not stdout:
        return None
    store = blobs if blobs is not None else get_blob_store()
    try:
        return await store.put(stdout.encode("utf-8"))
    except Exception:  # blob trouble must not invalidate an otherwise-good synthetic run
        return None


__all__ = [
    "FRAMEWORK_BOOT",
    "FRAMEWORK_COMMAND",
    "FRAMEWORK_TYPECHECK",
    "boot_failure",
    "command_failure",
    "persist_synthetic_run",
    "typecheck_failures",
]
