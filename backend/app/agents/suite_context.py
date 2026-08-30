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
resolves what a test *exercises* from the workspace itself, in four tiers ordered by proximity to
the failing test:

1. **The same-stem sibling** — ``todos.test.ts`` → ``todos.ts``, the near-certain answer.
2. **What the spec imports** (phase-63) — the module graph rooted at the failing spec, depth 1
   first. This is the tier that makes a *frontend* failure repairable: the generated skeleton keeps
   ``frontend/src/App.test.tsx`` one directory away from the page it renders, so the component
   under test is never a neighbour, but it is always an import.
3. **Directory neighbours** — a feature's test usually drives several files beside it
   (``todos.controller.ts``, ``todos.service.ts``, ``todos.model.ts``).
4. **Transitive imports**, then **changed since last-passing**, then **the suite's source tree** —
   the diff-aware and last-resort tiers, for a failure with no sibling, no resolvable import and no
   green history (a Playwright spec under ``frontend/e2e/``).

Coverage is judged **per failing test, after every tier** (:func:`uncovered`), not once over the
run: with one failure naming its source and another naming only its spec, a run-level check reports
"something is patchable", the loop happily iterates, and the second failure can never be fixed no
matter how many iterations it is given.

Two invariants hold throughout, so widening can never become "feed the whole repo" (§9) or let the
agent cheat:

- every tier goes through :meth:`RepairContextAnalyzer.add_files`, so ``repair_context_max_chars``
  still bounds the whole context (and the import walk is bounded again, before that, by
  ``repair_import_max_depth`` / ``repair_import_max_files``);
- ``is_test_file`` filters every candidate, so the oracle stays read-only and a green run cannot be
  faked by rewriting the test.

Deterministic and model-free — pure path arithmetic over a workspace listing, which is what makes
it exhaustively testable. It matches the ``ContextAnalyzer`` Protocol, so it drops straight into
``RepairLoopController(analyzer=…)``.
"""

from __future__ import annotations

import posixpath
from itertools import zip_longest
from typing import Protocol

from app.agents.import_graph import (
    SOURCE_EXTS,
    at_depth,
    deeper_than,
    reachable_sources,
)
from app.agents.repair import is_test_file
from app.agents.repair_context import (
    FailingTest,
    RepairContext,
    RepairContextAnalyzer,
    editable_paths,
)
from app.core.config import get_config
from app.db.models import Project, TestRun
from app.sandbox.schemas import FileContent, FileNode, GitInfo

#: Where each half of the generated app keeps its sources — the last-resort search roots.
_SUITE_ROOTS = {"backend": "backend/src", "frontend": "frontend/src"}

_TEST_INFIXES = (".test", ".spec")

_NOTE_COMPANIONS = (
    "Widened the repair target set to what the failing tests import and the sources beside them "
    "(their failures named no editable source directly)."
)
_NOTE_CHANGED = "Widened the repair target set to what changed since the last passing run."
_NOTE_SUITE = (
    "Widened the repair target set to the failing suite's source tree "
    "(no companion source and no passing run to diff against)."
)


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
    return bool(path) and path.endswith(SOURCE_EXTS) and not is_test_file(path)


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


def directory_sources(spec_path: str, tree: list[str]) -> list[str]:
    """Every source in ``spec_path``'s own folder, sorted (deterministic, reproducible contexts)."""
    directory = posixpath.dirname(spec_path)
    return sorted(
        path
        for path in tree
        if is_source_path(path) and posixpath.dirname(path) == directory  # same folder only
    )


def same_stem_sources(spec_path: str, tree: list[str]) -> list[str]:
    """The sibling named after the spec (``app.test.ts`` → ``app.ts``) — the near-certain answer."""
    stem = spec_stem(spec_path)
    if not stem:
        return []
    return [path for path in directory_sources(spec_path, tree) if stem_matches(path, stem)]


def companion_sources(spec_path: str, tree: list[str]) -> list[str]:
    """The sources ``spec_path`` exercises: same-stem sibling first, then its directory.

    Same-stem first because it is the near-certain answer (``app.test.ts`` tests ``app.ts``);
    directory neighbours follow because a feature's test usually drives several files beside it
    (``todos.controller.ts``, ``todos.service.ts``, ``todos.model.ts``). Deterministically ordered
    so a repair context is reproducible.
    """
    same_stem = same_stem_sources(spec_path, tree)
    return same_stem + [
        path for path in directory_sources(spec_path, tree) if path not in same_stem
    ]


def stem_matches(source_path: str, stem: str) -> bool:
    """True when ``source_path`` is ``stem`` plus one source extension."""
    return any(source_path == f"{stem}{ext}" for ext in SOURCE_EXTS)


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


def uncovered(
    context: RepairContext, contributed: dict[int, list[str]] | None = None
) -> list[FailingTest]:
    """Failing tests with no editable file of their own in the context.

    A test is *covered* when something it implicates — or something a previous tier contributed
    **for it** (``contributed``, keyed by index into ``context.failing_tests``) — is already
    patchable. Judging this per test rather than over the run as a whole is the point: with one
    failure naming its source and another naming only its spec, the run-level set is non-empty, the
    loop happily iterates, and the second failure can never be fixed no matter how many iterations
    it is given.
    """
    return [context.failing_tests[i] for i in _uncovered_indices(context, contributed or {})]


def _uncovered_indices(context: RepairContext, contributed: dict[int, list[str]]) -> list[int]:
    patchable = set(editable_paths(context))
    out: list[int] = []
    for index, failing in enumerate(context.failing_tests):
        implicated = [
            path
            for path in [failing.file, *failing.files_referenced]
            if path and not is_test_file(path)
        ]
        implicated.extend(contributed.get(index, []))
        if not any(path in patchable for path in implicated):
            out.append(index)
    return out


def _interleave(rows: list[list[str]]) -> list[str]:
    """Round-robin the per-test candidate lists so every failure's best guess is admitted first.

    Concatenating instead would let one failure with twenty candidates spend the whole char budget
    before the next failure's single obvious target is even considered.
    """
    out: list[str] = []
    for rank in zip_longest(*rows):
        for path in rank:
            if path and path not in out:
                out.append(path)
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

        session = _Widening(self, project)
        missing = _uncovered_indices(context, session.contributed)

        if missing:
            tree = await session.tree()
            per_test = {
                index: await session.candidates_for(context.failing_tests[index], tree)
                for index in missing
            }
            await session.apply(context, per_test, note=_NOTE_COMPANIONS)
            missing = _uncovered_indices(context, session.contributed)

        if missing:
            changed = await self._changed_since_passing(project, context)
            if changed:
                await session.apply(context, dict.fromkeys(missing, changed), note=_NOTE_CHANGED)
                missing = _uncovered_indices(context, session.contributed)

        if missing:
            tree = await session.tree()
            per_test = {
                index: suite_sources(spec_files_of(context.failing_tests[index]), tree)
                for index in missing
            }
            await session.apply(context, per_test, note=_NOTE_SUITE)

        return await self._base.persist(test_run, context, persist=persist)

    # -- pieces ---------------------------------------------------------------------------

    async def _widen(
        self,
        project: Project,
        context: RepairContext,
        candidates: list[str],
        *,
        note: str,
        contents: dict[str, str] | None = None,
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
        await self._base.add_files(project, context, wanted, contents=contents)

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


class _Widening:
    """One analysis pass: the workspace listing, the read cache, and what each tier contributed.

    Kept off the analyzer so two concurrent analyses never share a cache, and so the tiers can be
    read in ``analyze`` as four plain steps.
    """

    def __init__(self, analyzer: SuiteRepairContextAnalyzer, project: Project) -> None:
        self._analyzer = analyzer
        self._project = project
        self._tree: list[str] | None = None
        #: Sources read while walking imports — handed to ``add_files`` so nothing is read twice.
        self.reads: dict[str, str] = {}
        self._unreadable: set[str] = set()
        #: Failing-test index → the paths a tier offered for it (see :func:`uncovered`).
        self.contributed: dict[int, list[str]] = {}

    async def tree(self) -> list[str]:
        if self._tree is None:
            self._tree = await self._analyzer._source_tree(self._project)
        return self._tree

    async def read(self, path: str) -> str | None:
        """Cached workspace read; ``None`` (remembered) when the file cannot be read."""
        if path in self.reads:
            return self.reads[path]
        if path in self._unreadable:
            return None
        try:
            content = (await self._analyzer._workspace.read(self._project, path)).content
        except Exception:  # a read failure skips that node; the walk continues
            self._unreadable.add(path)
            return None
        self.reads[path] = content
        return content

    async def candidates_for(self, failing: FailingTest, tree: list[str]) -> list[str]:
        """What this failing test exercises, nearest evidence first."""
        tree_set = set(tree)
        config = get_config()
        max_depth = int(config.get("repair_import_max_depth"))
        max_files = int(config.get("repair_import_max_files"))

        out: list[str] = []
        for spec in spec_files_of(failing):
            reachable = await reachable_sources(
                spec, self.read, tree_set, max_depth=max_depth, max_files=max_files
            )
            same_stem = same_stem_sources(spec, tree)
            neighbours = directory_sources(spec, tree)
            for path in [
                *same_stem,  # 1. the sibling named after the spec
                *at_depth(reachable, 1),  # 2. what the spec imports directly
                *neighbours,  # 3. the rest of its folder
                *deeper_than(reachable, 1),  # 4. what those imports reach
            ]:
                if path not in out:
                    out.append(path)
        return out

    async def apply(
        self, context: RepairContext, per_test: dict[int, list[str]], *, note: str
    ) -> None:
        """Record one tier's per-test candidates and widen the context with them."""
        for index, paths in per_test.items():
            self.contributed.setdefault(index, []).extend(paths)
        await self._analyzer._widen(
            self._project,
            context,
            _interleave(list(per_test.values())),
            note=note,
            contents=self.reads,
        )


__all__ = [
    "SuiteRepairContextAnalyzer",
    "companion_sources",
    "directory_sources",
    "is_source_path",
    "same_stem_sources",
    "spec_files_of",
    "spec_stem",
    "suite_sources",
    "uncovered",
]
