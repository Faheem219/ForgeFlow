"""Diff-aware repair agent (phase-30, §9 steps 2–3).

One bounded, minimal-context patch attempt: given a failing ``TestRun`` it assembles the phase-29
:class:`~app.agents.repair_context.RepairContext`, asks Sonnet for the smallest fix, applies it
through the sandbox tools, commits, re-runs (**affected suite first, then the full suite**), and
reports what actually changed.

Three properties make an attempt trustworthy on its own (the design note: an attempt is
independently meaningful, not just a step the controller interprets):

- **Minimality is enforced, not merely requested.** The tool surface is `read_file` / `write_file` /
  `git_commit`, and writes are rejected unless the path is in the context's editable set. Blocked
  attempts are recorded rather than silently swallowed.
- **The oracle is read-only.** Test files are supplied as context but never writable — an agent that
  cannot edit the tests cannot fake a green run. If the tests themselves are wrong the attempt
  stalls, which is exactly the signal the controller (phase-31) escalates on.
- **Regressions are computed here.** The new full run is compared against the prior one, yielding
  ``newly_failing`` / ``newly_passing`` / ``still_failing`` — the guards phase-31 consumes.

The loop itself (iteration, stall detection, escalation) is **not** here; that is phase-31.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.agents.anthropic_client import AnthropicClient
from app.agents.models import TaskKind
from app.agents.prompts import system_prompt
from app.agents.prompts.repair import REPAIR_SYSTEM_EXTRA, repair_user_prompt
from app.agents.repair_context import (
    ContextFile,
    RepairContext,
    RepairContextAnalyzer,
    truncate_head,
)
from app.agents.tools.context import ToolContext
from app.agents.tools.definitions import ALL_TOOLS
from app.agents.tools.registry import Tool, ToolDispatch, ToolRegistry, tool_err, tool_ok
from app.core.config import get_config
from app.core.errors import NotFoundError, UserError
from app.db.blobs import BlobStore, get_blob_store
from app.db.models import Project, RepairAttempt, Run, TestRun
from app.db.models.enums import Stage
from app.realtime.hub import emit
from app.realtime.schemas import EventType
from app.sandbox.paths import require_rel_path
from app.sandbox.schemas import FileContent, GitInfo
from app.testing.models import (
    FRAMEWORK_JEST,
    FRAMEWORK_PLAYWRIGHT,
    FRAMEWORK_VITEST,
    TestResult,
    TestScope,
    TestStatus,
    summarize,
)

# The patch step gets FS-read + FS-write + commit. No list_dir (no repo browsing), no run_tests
# (the re-run is deterministic, below), no preview/install — minimal surface, minimal blast radius.
# `request_file` (phase-63) is added per attempt, since it closes over that attempt's context.
_PATCH_TOOL_NAMES = frozenset({"read_file", "write_file", "git_commit"})

_REQUEST_FILE_DESCRIPTION = (
    "Pull ONE more source file into this repair attempt when the fix clearly belongs in a file you "
    "were not given — name a path you have actually seen (in an import statement of a file above, "
    "or in a stack). Returns the file's contents and makes it editable. Bounded per attempt; test "
    "files are never granted."
)

_TEST_SUFFIXES = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")


def is_test_file(path: str) -> bool:
    """True for the generated oracle (never writable during repair)."""
    lowered = path.lower()
    return lowered.endswith(_TEST_SUFFIXES) or "/e2e/" in lowered or lowered.startswith("e2e/")


def _repair_registry() -> ToolRegistry:
    return ToolRegistry([t for t in ALL_TOOLS if t.name in _PATCH_TOOL_NAMES])


class RequestFileArgs(BaseModel):
    path: str = Field(min_length=1, description="Workspace-relative path of the source file")
    reason: str = Field(default="", description="One line: why the fix belongs in this file")


class SuiteRunner(Protocol):
    """The slice of :class:`~app.testing.runner.TestRunner` the agent re-runs through."""

    async def run(
        self, project: Project, scope: TestScope = "all", name_filter: str | None = None
    ) -> TestRun: ...


class RepairWorkspace(Protocol):
    """The git surface the agent needs (the tool-facing ``WorkspaceApi`` is FS-only).

    ``read`` is here because the same workspace is handed to the default context analyzer — one
    injected seam, so a fake workspace fakes the *whole* attempt (assembly included), not just the
    git half of it.
    """

    async def read(self, project: Project, path: str) -> FileContent: ...

    async def commit(self, project: Project, message: str) -> tuple[str | None, bool]: ...

    async def git_info(self, project: Project) -> GitInfo: ...

    async def diff(
        self, project: Project, sha_a: str, sha_b: str, paths: list[str] | None = None
    ) -> str: ...


# --------------------------------------------------------------------- result shape


@dataclass
class RepairResult:
    """The outcome of one patch attempt — enough to judge it without the controller."""

    attempt: RepairAttempt | None = None
    test_run: TestRun | None = None  # authoritative full re-run
    fast_run: TestRun | None = None  # the affected-suite fast path (None when it *is* the full run)
    newly_failing: list[str] = field(default_factory=list)  # regressions
    newly_passing: list[str] = field(default_factory=list)  # fixes
    still_failing: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    blocked_writes: list[str] = field(default_factory=list)  # minimality guard rejections
    requested_files: list[str] = field(default_factory=list)  # granted via `request_file`
    diff: str = ""
    diff_ref: str | None = None
    commit: str | None = None
    #: HEAD *before* this attempt patched anything — the anchor the loop resets to when an
    #: attempt turns out to have made things strictly worse.
    before_sha: str | None = None
    summary: str = ""
    green: bool = False
    note: str | None = None  # set when the attempt could not proceed

    def to_dict(self) -> dict[str, Any]:
        return {
            "newly_failing": self.newly_failing,
            "newly_passing": self.newly_passing,
            "still_failing": self.still_failing,
            "files_written": self.files_written,
            "blocked_writes": self.blocked_writes,
            "requested_files": self.requested_files,
            "commit": self.commit,
            "green": self.green,
            "summary": self.summary,
            "note": self.note,
            "test_run_id": str(self.test_run.id) if self.test_run else None,
        }


@dataclass
class _PatchTracker:
    """Observes the patch loop: what was written, what the guard blocked, and the commit."""

    writable: set[str] = field(default_factory=set)
    files_written: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    granted: list[str] = field(default_factory=list)  # files pulled in via `request_file`
    last_commit: str | None = None

    def observe(self, name: str, args: dict[str, Any], result_json: str) -> None:
        try:
            result = json.loads(result_json)
        except ValueError:
            return
        if not result.get("ok"):
            return
        if name == "write_file":
            path = args.get("path")
            if isinstance(path, str) and path not in self.files_written:
                self.files_written.append(path)
        elif name == "git_commit":
            sha = result.get("sha")
            if isinstance(sha, str):
                self.last_commit = sha


# --------------------------------------------------------------------- agent


class RepairAgent:
    def __init__(
        self,
        client: AnthropicClient | None = None,
        registry: ToolRegistry | None = None,
        runner: SuiteRunner | None = None,
        analyzer: RepairContextAnalyzer | None = None,
        workspace: RepairWorkspace | None = None,
        blobs: BlobStore | None = None,
    ) -> None:
        if workspace is None:
            from app.sandbox.workspace import WorkspaceService

            workspace = WorkspaceService()
        self._client = client or AnthropicClient()
        self._registry = registry or _repair_registry()
        self._runner = runner
        self._analyzer = analyzer or RepairContextAnalyzer(workspace=workspace)
        self._workspace = workspace
        self._blobs = blobs

    def _suite_runner(self) -> SuiteRunner:
        if self._runner is None:
            from app.testing.runner import TestRunner

            self._runner = TestRunner()
        return self._runner

    async def run(
        self,
        project: Project,
        run: Run,
        test_run: TestRun,
        *,
        context: RepairContext | None = None,
        ctx: ToolContext | None = None,
        channel: str | None = None,
        iteration: int = 1,
    ) -> RepairResult:
        """Patch the failures in ``test_run`` once, then re-run and report the delta."""
        project_id = project.id
        if project_id is None:  # pragma: no cover - a persisted project always carries an id
            raise UserError("Project is not persisted")
        channel = channel or str(project_id)
        ctx = ctx or ToolContext.build(project, run, channel=channel)

        await self._emit(channel, "context")
        if context is None:
            context = await self._analyzer.analyze(project, test_run)

        if not context.failing_tests:
            return RepairResult(note="Nothing to repair — the run has no failing tests.")

        editable = {f.path for f in context.target_files if not is_test_file(f.path)}
        if not editable:
            return RepairResult(
                note=(
                    "No editable source files in the repair context — only test files were "
                    "implicated. This needs a human: the tests may be wrong, or the failure "
                    "points outside the analyzed scope."
                )
            )

        before_sha = (await self._workspace.git_info(project)).current_sha

        # -- patch (Sonnet, guarded tool surface) ----------------------------------------
        tracker = _PatchTracker(writable=set(editable))
        readable = {f.path for f in context.target_files}
        # The bounded escape hatch (phase-63): the analyzer resolves what a test exercises, but a
        # subject that is registered rather than imported can still elude it. The agent may name
        # such a file — capped, justified, recorded, and never a test.
        budget = max(0, int(get_config().get("repair_request_file_max")))
        registry = self._registry
        if budget:
            registry = ToolRegistry(
                [*registry.tools(), self._request_file_tool(context, tracker, readable, budget)]
            )
        dispatch = self._make_dispatch(ctx, tracker, readable, registry)

        await self._emit(channel, "patch")
        patch = await self._client.run_tool_loop(
            task_kind=TaskKind.repair,
            project_id=project_id,
            run=run,
            system=system_prompt(REPAIR_SYSTEM_EXTRA),
            messages=[
                {
                    "role": "user",
                    "content": repair_user_prompt(
                        context, editable, iteration, request_budget=budget
                    ),
                }
            ],
            tools=registry.anthropic_tools(),
            tool_dispatch=dispatch,
            channel=channel,
        )
        summary = patch.text.strip()

        # An attempt that widened its own context re-persists it, so the stored blob is what the
        # model actually saw — a granted file must never be invisible in the trail.
        if tracker.granted:
            await self._analyzer.persist(test_run, context, persist=True)

        # Always commit (a no-op when the model already did) so every attempt is a git anchor.
        commit_sha, _ = await self._workspace.commit(
            project, f"repair: attempt {iteration} — {summary[:60] or 'patch'}"
        )
        commit_sha = commit_sha or tracker.last_commit

        diff = await self._patch_diff(project, before_sha, commit_sha)
        diff_ref = await self._store_diff(diff)

        # -- re-run: affected suite first (fast path), then the full suite ----------------
        before_results = _results_of(test_run)
        affected = _affected_scope(context)
        fast_run: TestRun | None = None
        if affected != "all":
            await self._emit(channel, f"rerun:{affected}")
            fast_run = await self._suite_runner().run(project, scope=affected)
        await self._emit(channel, "rerun:all")
        full_run = await self._suite_runner().run(project, scope="all")

        delta = compare_runs(before_results, _results_of(full_run))
        attempt = await RepairAttempt(
            project_id=project_id,
            run_id=run.id,
            iteration=iteration,
            target_files=sorted(tracker.files_written),
            diff_ref=diff_ref,
            resulting_run_id=full_run.id,
            # `outcome` is deliberately left unset — the controller (phase-31) judges it.
        ).insert()

        result = RepairResult(
            attempt=attempt,
            test_run=full_run,
            fast_run=fast_run,
            newly_failing=delta.newly_failing,
            newly_passing=delta.newly_passing,
            still_failing=delta.still_failing,
            files_written=sorted(tracker.files_written),
            blocked_writes=sorted(set(tracker.blocked)),
            requested_files=list(tracker.granted),
            diff=diff,
            diff_ref=diff_ref,
            commit=commit_sha,
            before_sha=before_sha,
            summary=summary,
            green=summarize(_results_of(full_run)).green,
        )
        await emit(
            channel,
            "repair.attempt",
            {"iteration": iteration, **result.to_dict()},
            stage=Stage.test,
        )
        return result

    # -- pieces ---------------------------------------------------------------------------

    def _request_file_tool(
        self, context: RepairContext, tracker: _PatchTracker, readable: set[str], budget: int
    ) -> Tool:
        """One bounded grant of a file the analyzer did not reach (phase-63).

        Built per attempt because it closes over *that* attempt's context and guard sets. Every
        grant is appended to ``context.target_files``, so the persisted context blob records
        exactly what the attempt widened — a widened repair is auditable, never invisible.
        """
        # Imported lazily: `suite_context` imports this module for `is_test_file`.
        from app.agents.suite_context import is_source_path

        async def handler(ctx: ToolContext, args: RequestFileArgs) -> str:
            path = require_rel_path(args.path)  # tool-level guardrail: workspace-relative only
            if is_test_file(path):
                return tool_err(
                    f"Refusing to grant {path}: it is a test file — tests are the specification, "
                    "read-only during repair."
                )
            if not is_source_path(path):
                return tool_err(f"Refusing to grant {path}: it is not a source file to patch.")
            if path in readable:
                tracker.writable.add(path)
                return tool_err(
                    f"{path} is already in your context.", editable=sorted(tracker.writable)
                )
            if len(tracker.granted) >= budget:
                return tool_err(
                    f"Request limit reached ({budget} file(s) per attempt). Fix what you have, or "
                    "explain in your summary which file the fix needs.",
                    granted=list(tracker.granted),
                )
            try:
                found = await ctx.workspace.read(ctx.project, path)
            except NotFoundError:
                return tool_err(
                    f"{path} is not in the workspace. Name a path you have seen in an import "
                    "statement or a stack."
                )

            cap = int(get_config().get("repair_context_max_file_chars"))
            content, truncated = truncate_head(found.content, cap)
            context.target_files.append(
                ContextFile(path=path, content=content, truncated=truncated)
            )
            context.notes.append(
                f"Agent requested {path}"
                + (f" — {args.reason.strip()}" if args.reason.strip() else "")
            )
            tracker.granted.append(path)
            tracker.writable.add(path)
            readable.add(path)
            return tool_ok(path=path, content=content, truncated=truncated, editable=True)

        return Tool(
            "request_file",
            _REQUEST_FILE_DESCRIPTION,
            RequestFileArgs,
            handler,
            guardrails=f"Source files only, never a test; at most {budget} per attempt.",
        )

    def _make_dispatch(
        self,
        ctx: ToolContext,
        tracker: _PatchTracker,
        readable: set[str],
        registry: ToolRegistry,
    ) -> ToolDispatch:
        """Wrap the registry with the minimality guard: writes only inside the editable set."""

        async def dispatch(name: str, raw_args: dict[str, Any]) -> str:
            path = raw_args.get("path")
            if name == "write_file" and isinstance(path, str) and path not in tracker.writable:
                tracker.blocked.append(path)
                reason = (
                    "it is a test file — tests are the specification, read-only during repair"
                    if is_test_file(path)
                    else (
                        "it is outside the minimal repair context — call `request_file` with that "
                        "path if the fix belongs there"
                    )
                )
                return tool_err(
                    f"Refusing to write {path}: {reason}.",
                    editable=sorted(tracker.writable),
                )
            if name == "read_file" and isinstance(path, str) and path not in readable:
                tracker.blocked.append(path)
                return tool_err(
                    f"Refusing to read {path}: it is outside the minimal repair context. "
                    "If the fix needs it, call `request_file` with that path.",
                    available=sorted(readable),
                )
            result = await registry.dispatch(ctx, name, raw_args)
            tracker.observe(name, raw_args, result)
            return result

        return dispatch

    async def _patch_diff(self, project: Project, before: str | None, after: str | None) -> str:
        """The patch this attempt actually applied (empty when nothing was committed)."""
        if not before or not after or before == after:
            return ""
        try:
            return await self._workspace.diff(project, before, after)
        except Exception:  # a diff failure must not invalidate an otherwise-good attempt
            return ""

    async def _store_diff(self, diff: str) -> str | None:
        if not diff:
            return None
        try:
            return await self._blob_store().put(diff.encode("utf-8"))
        except Exception:  # auditability is best-effort
            return None

    def _blob_store(self) -> BlobStore:
        return self._blobs if self._blobs is not None else get_blob_store()

    async def _emit(self, channel: str, step: str) -> None:
        await emit(
            channel,
            EventType.progress,
            {"stage": "test", "step": f"repair:{step}"},
            stage=Stage.test,
        )


# --------------------------------------------------------------------- pure helpers


@dataclass(frozen=True)
class RunDelta:
    newly_failing: list[str]
    newly_passing: list[str]
    still_failing: list[str]


def test_key(result: TestResult) -> str:
    """Stable identity for a test across runs (file + name; frameworks reuse names)."""
    return f"{result.file or ''}::{result.name}"


def compare_runs(before: list[TestResult], after: list[TestResult]) -> RunDelta:
    """Classify the new run against the previous one.

    A **regression** is strictly "was passing, is now failing" — a test that did not exist before is
    not counted as a regression, so adding coverage can never look like breakage.
    """
    prior = {test_key(r): r.status for r in before}
    newly_failing: list[str] = []
    newly_passing: list[str] = []
    still_failing: list[str] = []
    for result in after:
        key = test_key(result)
        was = prior.get(key)
        if result.status is TestStatus.failed:
            if was is TestStatus.passed:
                newly_failing.append(key)
            elif was is TestStatus.failed:
                still_failing.append(key)
        elif result.status is TestStatus.passed and was is TestStatus.failed:
            newly_passing.append(key)
    return RunDelta(
        newly_failing=sorted(newly_failing),
        newly_passing=sorted(newly_passing),
        still_failing=sorted(still_failing),
    )


def _affected_scope(context: RepairContext) -> TestScope:
    """The narrowest suite covering the failing frameworks — the fast path before the full run."""
    frameworks = {t.framework for t in context.failing_tests}
    has_e2e = FRAMEWORK_PLAYWRIGHT in frameworks
    has_unit = bool(frameworks & {FRAMEWORK_JEST, FRAMEWORK_VITEST})
    if has_e2e and not has_unit:
        return "e2e"
    if has_unit and not has_e2e:
        return "unit"
    return "all"


def _results_of(test_run: TestRun) -> list[TestResult]:
    out: list[TestResult] = []
    for item in test_run.results:
        try:
            out.append(TestResult.model_validate(item))
        except Exception:  # a malformed stored row must not sink the comparison
            continue
    return out


__all__ = [
    "RepairAgent",
    "RepairResult",
    "RunDelta",
    "SuiteRunner",
    "compare_runs",
    "is_test_file",
    "test_key",
]
