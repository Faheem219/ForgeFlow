"""Build-repair context: guarantee a non-empty editable set (phase-55 task 11).

*This is the failure mode that would silently break the whole phase.* :class:`RepairAgent` derives
its editable files from ``context.target_files`` minus test files. A boot crash whose stack names
only ``node_modules`` paths → ``extract_referenced_files`` strips them → the ranked set is empty →
``target_files == []`` → the agent has nothing to patch → the loop escalates on **iteration 1,
having patched nothing**.

:class:`BuildRepairContextAnalyzer` wraps :class:`RepairContextAnalyzer` and, when the editable set
comes back empty, **widens** it — first from ``fallback_paths`` (the files this build just wrote on
the failing surface), then from ``changed_paths(last_passing, HEAD)`` ("what changed since it
worked"). It matches the ``ContextAnalyzer`` Protocol exactly, so it drops straight into
``RepairLoopController(analyzer=…)``. The ``repair_context_max_chars`` cap still applies through
:meth:`RepairContextAnalyzer.add_files`, so widening can never become "feed the whole repo".
"""

from __future__ import annotations

from typing import Protocol

from app.agents.repair import is_test_file
from app.agents.repair_context import RepairContext, RepairContextAnalyzer, editable_paths
from app.db.models import Project, TestRun


class _ChangedPathsWorkspace(Protocol):
    """The one git method the widener needs (WorkspaceService supplies it; tests inject a fake)."""

    async def changed_paths(self, project: Project, sha_a: str, sha_b: str) -> list[str]: ...


#: The files the repair agent could actually patch. Shared with the test-stage widener
#: (``app.agents.test_context``) so both stages judge "nothing to patch" identically.
_editable = editable_paths


class BuildRepairContextAnalyzer:
    def __init__(
        self,
        base: RepairContextAnalyzer,
        *,
        fallback_paths: list[str],
        workspace: _ChangedPathsWorkspace | None = None,
    ) -> None:
        self._base = base
        self._fallback = list(fallback_paths)
        if workspace is None:
            from app.sandbox.workspace import WorkspaceService

            workspace = WorkspaceService()
        self._workspace = workspace

    async def analyze(
        self, project: Project, test_run: TestRun, *, persist: bool = True
    ) -> RepairContext:
        # Assemble the normal minimal context first (never persist yet — we may still widen it).
        context = await self._base.analyze(project, test_run, persist=False)

        if not _editable(context):
            await self._widen(project, context, self._fallback)
        if not _editable(context):
            await self._widen(project, context, await self._changed_since_passing(project, context))

        return await self._base.persist(test_run, context, persist=persist)

    async def _widen(self, project: Project, context: RepairContext, candidates: list[str]) -> None:
        """Top up the target set with editable, not-yet-present candidates (budget-respecting)."""
        present = set(context.file_paths())
        wanted = [p for p in candidates if p and p not in present and not is_test_file(p)]
        if wanted:
            context.notes.append(
                "Widened the repair target set from build-authored files "
                "(the failure named no editable source directly)."
            )
            await self._base.add_files(project, context, wanted)

    async def _changed_since_passing(self, project: Project, context: RepairContext) -> list[str]:
        """Files changed between the last-passing ref and HEAD — diff-aware "what changed since it
        worked". Empty when there is no last-passing ref yet, or on any git error."""
        if not context.base_sha or not context.head_sha or context.base_sha == context.head_sha:
            return []
        try:
            return await self._workspace.changed_paths(project, context.base_sha, context.head_sha)
        except Exception:  # a git failure must not sink the analysis — the loop escalates cleanly
            return []


__all__ = ["BuildRepairContextAnalyzer"]
