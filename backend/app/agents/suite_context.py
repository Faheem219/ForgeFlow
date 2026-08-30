"""Test-repair context: every failing test gets something the agent may actually patch.

The gap this closes. :class:`~app.agents.repair.RepairAgent` derives its **editable** set from
``context.target_files`` minus test files, and
:class:`~app.agents.repair_context.RepairContextAnalyzer` builds that set purely from what the
failure's stack *names*. For a thrown error that works — the stack names the source that threw.
For an **assertion** failure it does not: ::

    Error: expected 400, received 201
        at Object.<anonymous> (/workspace/backend/src/features/todos/todos.test.ts:22:18)

Nothing threw in ``todos.controller.ts``, so the stack never names it. The only file implicated is
the *test*, which the read-only-oracle rule bars from editing — so the editable set is empty, the
agent returns "nothing safe to patch", and the loop escalates on iteration 1 **having changed no
code at all**. Assertion mismatches are the common shape of a failing test, which is why pressing
Repair could look like it did nothing.

Phase-55 solved the same problem for the Build stage (:mod:`app.agents.build_context`), where the
fallback is "the files this build just wrote". The Test stage has no such list, so this widener
resolves what a test *exercises* from the workspace itself, in three tiers, each used only when the
one before it left something uncovered:

1. **Companion sources** — per failing test, the same-stem sibling (``todos.test.ts`` →
   ``todos.ts``) and then the other non-test sources in its directory. Applied **per test**, not
   just when the whole set is empty: a run where some failures name their source and others do not
   would otherwise leave the silent ones permanently unrepairable, which is exactly the "it didn't
   fix *all* of them" symptom.
2. **Changed since last-passing** — "what changed since it worked", the same diff-aware tier the
   build widener uses. Empty until a run has actually gone green.
3. **The suite's source tree** — last resort for a failure with no sibling and no green history
   (a Playwright spec under ``frontend/e2e/``): the ``src`` tree of the half of the app that suite
   exercises, and nothing else.

Two invariants hold throughout, so widening can never become "feed the whole repo" (§9) or let the
agent cheat:

- every tier goes through :meth:`RepairContextAnalyzer.add_files`, so ``repair_context_max_chars``
  still bounds the whole context;
- ``is_test_file`` filters every candidate, so the oracle stays read-only and a green run cannot be
  faked by rewriting the test.

Deterministic and model-free — pure path arithmetic over a workspace listing, which is what makes
it exhaustively testable. It matches the ``ContextAnalyzer`` Protocol, so it drops straight into
``RepairLoopController(analyzer=…)``.
"""

from __future__ import annotations

import posixpath
from typing import Protocol

from app.agents.repair import is_test_file
from app.agents.repair_context import (
    FailingTest,
    RepairContext,
    RepairContextAnalyzer,
    editable_paths,
)
from app.db.models import Project, TestRun
from app.sandbox.schemas import FileContent, FileNode, GitInfo

#: Extensions the fixed generated stack (D2) actually compiles. A companion is only a companion if
#: it is source the agent could meaningfully patch.
_SOURCE_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts")

#: Where each half of the generated app keeps its sources — the tier-3 search roots.
_SUITE_ROOTS = {"backend": "backend/src", "frontend": "frontend/src"}

_TEST_INFIXES = (".test", ".spec")


class SuiteRepairWorkspace(Protocol):
    """What the widener needs: the base analyzer's reads, plus a listing and a changed-file set."""

    async def read(self, project: Project, path: str) -> FileContent: ...

    async def git_info(self, project: Project) -> GitInfo: ...

    async def diff(
        self, project: Project, sha_a: str, sha_b: str, paths: list[str] | None = None
    ) -> str: ...

    async def tree(
        self, project: Project, path: str = ".", depth: int | None = None
    ) -> list[FileNode]: ...

    async def changed_paths(self, project: Project, sha_a: str, sha_b: str) -> list[str]: ...


# --------------------------------------------------------------------- pure path helpers


def is_source_path(path: str) -> bool:
    """A non-test file in the generated stack's own languages."""
    return bool(path) and path.endswith(_SOURCE_EXTS) and not is_test_file(path)


def spec_stem(path: str) -> str | None:
    """``a/b/todos.test.ts`` → ``a/b/todos``. ``None`` when the name carries no test marker.

    Only the marker is stripped, never a meaningful part of the name: ``todos.controller.spec.ts``
    yields ``todos.controller``, so the sibling it points at is the controller, not ``todos``.
    """
    base = posixpath.basename(path)
    stem = base.split(".")[0] if "." in base else base
    parts = base.split(".")
    for index, part in enumerate(parts):
        if f".{part}" in _TEST_INFIXES:
            stem = ".".join(parts[:index])
            break
    else:
        return None
    if not stem:
        return None
    directory = posixpath.dirname(path)
    return posixpath.join(directory, stem) if directory else stem


def companion_sources(spec_path: str, tree: list[str]) -> list[str]:
    """The sources ``spec_path`` exercises: same-stem sibling first, then its directory.

    Same-stem first because it is the near-certain answer (``app.test.ts`` tests ``app.ts``);
    directory neighbours follow because a feature's test usually drives several files beside it
    (``todos.controller.ts``, ``todos.service.ts``, ``todos.model.ts``). Deterministically ordered
    so a repair context is reproducible.
    """
    directory = posixpath.dirname(spec_path)
    neighbours = sorted(
        path
        for path in tree
        if is_source_path(path) and posixpath.dirname(path) == directory  # same folder only
    )
    stem = spec_stem(spec_path)
    same_stem = [path for path in neighbours if stem and stem_matches(path, stem)]
    return same_stem + [path for path in neighbours if path not in same_stem]


def stem_matches(source_path: str, stem: str) -> bool:
    """True when ``source_path`` is ``stem`` plus one source extension."""
    return any(source_path == f"{stem}{ext}" for ext in _SOURCE_EXTS)


def suite_sources(spec_paths: list[str], tree: list[str]) -> list[str]:
    """The ``src`` tree of each half of the app the failing suites belong to — nothing wider.

    Deliberately *not* the whole workspace: a failing backend suite never wants the frontend read
    into its context, and the char budget would be spent before reaching the file that matters.
    """
    roots: list[str] = []
    for path in spec_paths:
        head = path.split("/", 1)[0]
        root = _SUITE_ROOTS.get(head)
        if root and root not in roots:
            roots.append(root)
    return [
        path
        for root in roots
        for path in sorted(tree)
        if is_source_path(path) and path.startswith(f"{root}/")
    ]


def spec_files_of(failing: FailingTest) -> list[str]:
    """Every test file this failure points at — its own file plus any test in its stack."""
    out: list[str] = []
    for path in [failing.file, *failing.files_referenced]:
        if path and is_test_file(path) and path not in out:
            out.append(path)
    return out


def uncovered(context: RepairContext) -> list[FailingTest]:
    """Failing tests with no editable file of their own in the context.

    A test is *covered* when something it implicates is already patchable. Judging this per test
    rather than over the run as a whole is the point: with one failure naming its source and
    another naming only its spec, the run-level set is non-empty, the loop happily iterates, and
    the second failure can never be fixed no matter how many iterations it is given.
    """
    patchable = set(editable_paths(context))
    out: list[FailingTest] = []
    for failing in context.failing_tests:
        implicated = [
            path
            for path in [failing.file, *failing.files_referenced]
            if path and not is_test_file(path)
        ]
        if not any(path in patchable for path in implicated):
            out.append(failing)
    return out


# --------------------------------------------------------------------- analyzer


class SuiteRepairContextAnalyzer:
    def __init__(
        self,
        base: RepairContextAnalyzer | None = None,
        *,
        workspace: SuiteRepairWorkspace | None = None,
    ) -> None:
        if workspace is None:
            from app.sandbox.workspace import WorkspaceService

            workspace = WorkspaceService()
        self._workspace: SuiteRepairWorkspace = workspace
        self._base = base or RepairContextAnalyzer(workspace=workspace)

    async def analyze(
        self, project: Project, test_run: TestRun, *, persist: bool = True
    ) -> RepairContext:
        # Assemble the normal minimal context first, unpersisted — it may still be widened.
        context = await self._base.analyze(project, test_run, persist=False)
        if not context.failing_tests:
            return await self._base.persist(test_run, context, persist=persist)

        missing = uncovered(context)
        if missing:
            tree = await self._source_tree(project)
            await self._widen(
                project,
                context,
                [
                    source
                    for failing in missing
                    for path in spec_files_of(failing)
                    for source in companion_sources(path, tree)
                ],
                note=(
                    "Widened the repair target set to the sources beside the failing tests "
                    "(their failures named no editable source directly)."
                ),
            )

        if not editable_paths(context):
            await self._widen(
                project,
                context,
                await self._changed_since_passing(project, context),
                note="Widened the repair target set to what changed since the last passing run.",
            )

        if not editable_paths(context):
            tree = await self._source_tree(project)
            failing_paths = [p for f in context.failing_tests for p in spec_files_of(f)]
            await self._widen(
                project,
                context,
                suite_sources(failing_paths, tree),
                note=(
                    "Widened the repair target set to the failing suite's source tree "
                    "(no companion source and no passing run to diff against)."
                ),
            )

        return await self._base.persist(test_run, context, persist=persist)

    # -- pieces ---------------------------------------------------------------------------

    async def _widen(
        self, project: Project, context: RepairContext, candidates: list[str], *, note: str
    ) -> None:
        """Top up the target set with editable, not-yet-present candidates (budget-respecting)."""
        present = set(context.file_paths())
        wanted: list[str] = []
        for path in candidates:
            if path and path not in present and path not in wanted and not is_test_file(path):
                wanted.append(path)
        if not wanted:
            return
        context.notes.append(note)
        await self._base.add_files(project, context, wanted)

    async def _source_tree(self, project: Project) -> list[str]:
        """Every source path in the workspace. Empty on any error — the loop escalates cleanly."""
        try:
            nodes = await self._workspace.tree(project, ".", None)
        except Exception:  # a listing failure must not sink the analysis
            return []
        return [node.path for node in nodes if node.type == "file"]

    async def _changed_since_passing(self, project: Project, context: RepairContext) -> list[str]:
        """Files changed between the last-passing ref and HEAD. Empty until a run has gone green."""
        if not context.base_sha or not context.head_sha or context.base_sha == context.head_sha:
            return []
        try:
            return await self._workspace.changed_paths(project, context.base_sha, context.head_sha)
        except Exception:  # a git failure must not sink the analysis
            return []


__all__ = [
    "SuiteRepairContextAnalyzer",
    "companion_sources",
    "is_source_path",
    "spec_files_of",
    "spec_stem",
    "suite_sources",
    "uncovered",
]
