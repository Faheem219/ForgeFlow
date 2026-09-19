"""Failure analyzer → minimal, diff-aware repair context (phase-29, §9 step 1).

This module encodes the invariant that makes the self-healing loop the project's contribution
(D7, §9): **never feed the whole repo when a diff suffices.** Given a failing ``TestRun`` it
assembles *only*:

- the **failing tests** (name, criterion id, assertion, stack, referenced files) — from phase-28's
  normalized results;
- the **files those tests exercise** (the failing test source + the sources named in its stack);
- the **diff since ``last-passing``** — the moving git ref phase-28 advances on every green run, so
  the diff means "what changed since it worked" rather than "the previous commit" (design note);
- the **requirement snippets** for the failing criteria, joined via ``criterion_id`` (phase-25 →
  27 → 28 → here), so the agent repairs against the acceptance text, not a guess.

Everything else is excluded. The result is hard-capped (config): oversized parts are trimmed by
failure frequency and *what was dropped is recorded*, so a trimmed context is never silently lossy.

Deterministic and model-free — pure assembly over a ``TestRun`` + a workspace snapshot, which is
what makes it exhaustively testable and what the eval harness (D13) measures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import get_config
from app.core.errors import NotFoundError
from app.db.blobs import BlobStore, get_blob_store
from app.db.models import Project, TestRun
from app.db.repos import RequirementSpecRepo
from app.sandbox.offline import OfflineDiagnosis, diagnose_offline
from app.sandbox.schemas import FileContent, GitInfo
from app.testing.models import TestResult, TestStatus

_TRUNCATION_MARKER = "\n…(truncated)…"


# --------------------------------------------------------------------- context shape


@dataclass(frozen=True)
class FailingTest:
    """One failing test, flattened from phase-28's :class:`~app.testing.models.TestResult`."""

    name: str
    framework: str
    criterion_id: str | None
    file: str | None
    message: str
    assertion: str | None
    stack: str | None
    files_referenced: list[str]

    def size(self) -> int:
        return (
            len(self.name) + len(self.message) + len(self.assertion or "") + len(self.stack or "")
        )


@dataclass
class ContextFile:
    path: str
    content: str
    truncated: bool = False

    def size(self) -> int:
        return len(self.path) + len(self.content)


@dataclass(frozen=True)
class RequirementSnippet:
    criterion_id: str
    text: str
    feature: str

    def size(self) -> int:
        return len(self.criterion_id) + len(self.text) + len(self.feature)


@dataclass
class RepairContext:
    """The minimal, auditable input to the repair agent (phase-30)."""

    failing_tests: list[FailingTest] = field(default_factory=list)
    target_files: list[ContextFile] = field(default_factory=list)
    diff: str = ""
    requirement_snippets: list[RequirementSnippet] = field(default_factory=list)
    base_sha: str | None = None  # the last-passing ref
    head_sha: str | None = None
    # Everything the budget dropped or shortened, and why — a trimmed context is never silent.
    trimmed: list[str] = field(default_factory=list)
    # Diagnostics that are not budget-related (no last-passing ref, unreadable file, …).
    notes: list[str] = field(default_factory=list)
    # phase-65: failures explained by the sandbox having no network. Repairable ones become hints in
    # the prompt; an unrepairable one (no mongod for the test database) makes the loop escalate
    # before any attempt, because no patch can supply what the sandbox lacks.
    sandbox_hints: list[str] = field(default_factory=list)
    environment: OfflineDiagnosis | None = None
    ref: str | None = None  # blob ref, set once persisted

    def size(self) -> int:
        return (
            sum(t.size() for t in self.failing_tests)
            + sum(f.size() for f in self.target_files)
            + len(self.diff)
            + sum(s.size() for s in self.requirement_snippets)
        )

    def file_paths(self) -> list[str]:
        return [f.path for f in self.target_files]

    def to_dict(self) -> dict[str, Any]:
        return {
            "failing_tests": [
                {
                    "name": t.name,
                    "framework": t.framework,
                    "criterion_id": t.criterion_id,
                    "file": t.file,
                    "message": t.message,
                    "assertion": t.assertion,
                    "stack": t.stack,
                    "files_referenced": t.files_referenced,
                }
                for t in self.failing_tests
            ],
            "target_files": [
                {"path": f.path, "content": f.content, "truncated": f.truncated}
                for f in self.target_files
            ],
            "diff": self.diff,
            "requirement_snippets": [
                {"criterion_id": s.criterion_id, "text": s.text, "feature": s.feature}
                for s in self.requirement_snippets
            ],
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "trimmed": self.trimmed,
            "notes": self.notes,
            "sandbox_hints": self.sandbox_hints,
            "environment": self.environment.to_dict() if self.environment else None,
            "size": self.size(),
        }


def editable_paths(context: RepairContext) -> list[str]:
    """The files a repair attempt could actually patch: target files that are not test files.

    The oracle is read-only (``app.agents.repair``), so an *empty* result means the agent has
    nothing to write and the loop can only escalate — which is why the widening analyzers exist.
    """
    from app.agents.repair import is_test_file

    return [f.path for f in context.target_files if not is_test_file(f.path)]


# --------------------------------------------------------------------- workspace seam


class ContextWorkspace(Protocol):
    """The slice of :class:`~app.sandbox.workspace.WorkspaceService` the analyzer needs."""

    async def read(self, project: Project, path: str) -> FileContent: ...

    async def git_info(self, project: Project) -> GitInfo: ...

    async def diff(
        self, project: Project, sha_a: str, sha_b: str, paths: list[str] | None = None
    ) -> str: ...


# --------------------------------------------------------------------- analyzer


class RepairContextAnalyzer:
    def __init__(
        self,
        workspace: ContextWorkspace | None = None,
        blobs: BlobStore | None = None,
        requirements: RequirementSpecRepo | None = None,
    ) -> None:
        if workspace is None:
            from app.sandbox.workspace import WorkspaceService

            workspace = WorkspaceService()
        self._workspace = workspace
        self._blobs = blobs
        self._requirements = requirements or RequirementSpecRepo()

    async def analyze(
        self, project: Project, test_run: TestRun, *, persist: bool = True
    ) -> RepairContext:
        """Assemble the minimal repair context for ``test_run``'s failures."""
        context = RepairContext()

        failures = _failing_results(test_run)
        if not failures:
            context.notes.append("No failing tests in this run — nothing to repair.")
            return context

        context.failing_tests = [_to_failing_test(r) for r in failures]
        _diagnose_offline_failures(context)

        # Rank the files the failures actually implicate: most-referenced first, then first-seen.
        ranked = _rank_paths(context.failing_tests)

        # Diff since the last *passing* run, scoped to those files.
        info = await self._workspace.git_info(project)
        context.base_sha, context.head_sha = info.last_passing, info.current_sha
        context.diff = await self._compute_diff(project, context, ranked)

        context.requirement_snippets = await self._requirement_snippets(project, context)

        await self.add_files(project, context, ranked)
        return await self.persist(test_run, context, persist=persist)

    # -- pieces ---------------------------------------------------------------------------

    async def _compute_diff(
        self, project: Project, context: RepairContext, paths: list[str]
    ) -> str:
        if not context.base_sha or not context.head_sha:
            context.notes.append(
                "No last-passing ref yet — the diff is empty; repairing from the failures alone."
            )
            return ""
        if context.base_sha == context.head_sha:
            context.notes.append("Workspace is at the last-passing commit — no diff to show.")
            return ""
        try:
            raw = await self._workspace.diff(project, context.base_sha, context.head_sha, paths)
        except Exception:  # a diff failure must not sink the whole context
            context.notes.append("Could not compute the diff since last-passing.")
            return ""
        cap = int(get_config().get("repair_context_diff_max_chars"))
        diff, truncated = truncate_head(raw, cap)
        if truncated:
            context.trimmed.append(f"diff truncated to {cap} chars (was {len(raw)})")
        return diff

    async def _requirement_snippets(
        self, project: Project, context: RepairContext
    ) -> list[RequirementSnippet]:
        wanted = {t.criterion_id for t in context.failing_tests if t.criterion_id}
        if not wanted or project.id is None:
            return []
        spec = await self._requirements.latest(project.id)
        if spec is None:
            context.notes.append("No requirement spec — repairing without acceptance text.")
            return []
        snippets = [
            RequirementSnippet(criterion_id=c.id, text=c.text, feature=feature.name)
            for feature in spec.features
            for c in feature.acceptance_criteria
            if c.id in wanted
        ]
        missing = wanted - {s.criterion_id for s in snippets}
        if missing:
            context.notes.append("No acceptance text for criteria: " + ", ".join(sorted(missing)))
        return snippets

    async def add_files(
        self,
        project: Project,
        context: RepairContext,
        ranked: list[str],
        *,
        contents: dict[str, str] | None = None,
    ) -> None:
        """Read ranked files into the context until the whole-context cap is reached.

        Public (phase-55) so the build-repair widening analyzer can top up an empty editable set
        through the same budget-respecting path — widening can never become "feed the whole repo".

        ``contents`` (phase-63) supplies already-read sources: the import walk reads a file to
        follow its imports, so passing its cache spares the sandbox a second read of the same file.
        """
        config = get_config()
        total_cap = int(config.get("repair_context_max_chars"))
        file_cap = int(config.get("repair_context_max_file_chars"))
        used = context.size()  # failing tests + diff + snippets are the core; files fill the rest
        cache = contents or {}

        for path in ranked:
            raw = cache.get(path)
            if raw is None:
                try:
                    raw = (await self._workspace.read(project, path)).content
                except NotFoundError:
                    context.notes.append(f"Referenced file not in the workspace: {path}")
                    continue
                except Exception:
                    context.notes.append(f"Could not read {path}")
                    continue

            content, truncated = truncate_head(raw, file_cap)
            entry = ContextFile(path=path, content=content, truncated=truncated)
            if used + entry.size() > total_cap:
                context.trimmed.append(f"{path} dropped (context budget {total_cap} chars)")
                continue
            if truncated:
                context.trimmed.append(f"{path} truncated to {file_cap} chars (was {len(raw)})")
            context.target_files.append(entry)
            used += entry.size()

    async def persist(
        self, test_run: TestRun, context: RepairContext, *, persist: bool
    ) -> RepairContext:
        """Store the assembled context as a blob and pin it on the run (public since phase-55)."""
        if not persist:
            return context
        try:
            ref = await self._blob_store().put(
                json.dumps(context.to_dict(), indent=2).encode("utf-8")
            )
        except Exception:  # auditability is best-effort; never fail the analysis over a blob
            context.notes.append("Could not persist the repair context blob.")
            return context
        context.ref = ref
        test_run.repair_context_ref = ref
        await test_run.save()
        return context

    def _blob_store(self) -> BlobStore:
        return self._blobs if self._blobs is not None else get_blob_store()


# --------------------------------------------------------------------- pure helpers


def _failing_results(test_run: TestRun) -> list[TestResult]:
    """The run's failing results (``failures`` is pre-filtered; fall back to scanning results)."""
    raw = test_run.failures or test_run.results
    out: list[TestResult] = []
    for item in raw:
        try:
            result = TestResult.model_validate(item)
        except Exception:  # a malformed stored result must not sink the analysis
            continue
        if result.status is TestStatus.failed:
            out.append(result)
    return out


def _to_failing_test(result: TestResult) -> FailingTest:
    failure = result.failure
    return FailingTest(
        name=result.name,
        framework=result.framework,
        criterion_id=result.criterion_id,
        file=result.file,
        message=failure.message if failure else "Test failed",
        assertion=failure.assertion if failure else None,
        stack=failure.stack if failure else None,
        files_referenced=list(failure.files_referenced) if failure else [],
    )


def _diagnose_offline_failures(context: RepairContext) -> None:
    """Record failures the sandbox's missing network explains (phase-65).

    The first unrepairable finding becomes ``environment``; repairable hints are de-duplicated, so
    ten tests calling the same API produce one sentence, not ten.
    """
    for test in context.failing_tests:
        text = "\n".join(part for part in (test.message, test.assertion, test.stack) if part)
        diagnosis = diagnose_offline(text)
        if diagnosis is None:
            continue
        if not diagnosis.repairable:
            context.environment = context.environment or diagnosis
        elif diagnosis.hint not in context.sandbox_hints:
            context.sandbox_hints.append(diagnosis.hint)


def _rank_paths(failing: list[FailingTest]) -> list[str]:
    """Candidate files ordered by failure frequency, then first appearance (proximity).

    Frequency first because a file implicated by several failures is the likeliest common cause;
    first-seen order breaks ties deterministically.
    """
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for test in failing:
        paths = list(test.files_referenced)
        if test.file and test.file not in paths:
            paths.append(test.file)
        for path in paths:
            if not path:
                continue
            counts[path] = counts.get(path, 0) + 1
            first_seen.setdefault(path, len(first_seen))
    return sorted(counts, key=lambda p: (-counts[p], first_seen[p]))


def truncate_head(text: str, cap: int) -> tuple[str, bool]:
    """Keep the head of ``text`` within ``cap`` chars; returns ``(text, was_truncated)``."""
    if cap <= 0 or len(text) <= cap:
        return text, False
    return text[:cap] + _TRUNCATION_MARKER, True
