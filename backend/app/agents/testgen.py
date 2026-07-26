"""Test-generation agent (phase-27) — compile a ``RequirementSpec`` into runnable, traceable tests.

Implements D6's second half and feeds the crown-jewel repair loop (Epic 6): the generated tests are
the oracle the repair loop optimizes against, and every test is tagged with the exact
``AcceptanceCriterion.id`` it verifies so a failure maps straight back to a requirement (phase-29).

Flow (mirrors the codegen agent's discipline — minimal context, bounded loop, cost-tracked):

    1. resolve the spec — tolerate a skipped requirements stage (D12): either return a
       capture-or-infer offer (default) or infer a minimal spec from design/build (labeled)
    2. ensure the skeleton is present (so the test toolchain/config exists — non-linearity)
    3. plan     (Haiku)  — which file covers which criterion, unit vs e2e
    4. author   (Sonnet) — write the tests onto the skeleton, each tagged with its criterion id
    5. persist  — a versioned ``TestSuite`` per kind (the join key) + a ``test`` report artifact

The author step runs a restricted tool surface (FS + git only): the agent writes tests but never
runs them or starts a preview — running/parsing is phase-28.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from beanie import PydanticObjectId

from app.agents.anthropic_client import AnthropicClient
from app.agents.models import TaskKind
from app.agents.prompts import system_prompt
from app.agents.prompts.testgen import (
    INFER_SYSTEM,
    TESTGEN_PLAN_SYSTEM_EXTRA,
    TESTGEN_SYSTEM_EXTRA,
    author_user_prompt,
    format_spec,
    infer_user_prompt,
    plan_user_prompt,
)
from app.agents.tools.context import ToolContext
from app.agents.tools.definitions import ALL_TOOLS
from app.agents.tools.registry import ToolDispatch, ToolRegistry
from app.agents.tools.skeleton import copy_skeleton
from app.core.config import get_config
from app.core.errors import NotFoundError, UserError
from app.db.models import Project, RequirementSpec, Run
from app.db.models.enums import ArtifactType, CriterionKind, Stage, TestKind
from app.db.repos import TestSuiteRepo
from app.design.service import DesignService
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.requirements import (
    CriterionInput,
    FeatureInput,
    RequirementSpecInput,
    RequirementsService,
)
from app.realtime.hub import emit
from app.realtime.schemas import EventType

STATUS_GENERATED = "generated"
STATUS_NEEDS_REQUIREMENTS = "needs_requirements"

TESTGEN_REPORT_KIND = "testgen_report"

# The author step gets FS + git only: it writes tests, it never runs them (that's phase-28).
_AUTHOR_TOOL_NAMES = frozenset({"read_file", "write_file", "list_dir", "git_commit"})

# Sentinel files that mean "the skeleton is already here" (→ don't rescaffold).
_INSTANTIATED_MARKERS = ("pnpm-workspace.yaml", "package.json")

_MAX_INFERRED_FEATURES = 3
_MAX_INFERRED_CRITERIA = 5


class MissingRequirements(StrEnum):
    """What to do when no requirements spec exists (the requirements stage was skipped, D12)."""

    ask = "ask"  # default: warn + offer capture/infer, generate nothing
    infer = "infer"  # infer a minimal spec from design/build artifacts, label it, then generate


@dataclass
class TestGenReport:
    """The structured outcome of a test-generation run (persisted as a ``test`` artifact)."""

    status: str
    suites: list[dict[str, Any]]  # [{kind, files, generated_from}]
    files: list[str]
    criteria_covered: list[str]
    criteria_uncovered: list[str]
    inferred_spec: bool
    warning: str | None
    options: list[str]  # user choices when status == needs_requirements
    commit: str | None
    summary: str
    plan: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "suites": self.suites,
            "files": self.files,
            "criteria_covered": self.criteria_covered,
            "criteria_uncovered": self.criteria_uncovered,
            "inferred_spec": self.inferred_spec,
            "warning": self.warning,
            "options": self.options,
            "commit": self.commit,
            "summary": self.summary,
            "plan": self.plan,
        }


def _classify(path: str) -> TestKind | None:
    """Map a written file path to its test kind (or ``None`` when it isn't a test file)."""
    p = path.lower()
    if p.endswith(".spec.ts") or p.endswith(".spec.tsx") or "/e2e/" in p or p.startswith("e2e/"):
        return TestKind.e2e
    if p.endswith(".test.ts") or p.endswith(".test.tsx"):
        return TestKind.unit
    return None


@dataclass
class _TestTracker:
    """Observes tool results during the author loop to assemble the report from real signals.

    Coverage is read from the *file content the model actually wrote*: for each test file, the
    criterion ids it references (from the known set) are its coverage — so traceability reflects the
    real tests, not the model's self-report.
    """

    known_ids: set[str] = field(default_factory=set)
    files: dict[str, tuple[TestKind, set[str]]] = field(default_factory=dict)
    last_commit: str | None = None

    def observe(self, name: str, args: dict[str, Any], result_json: str) -> None:
        try:
            result = json.loads(result_json)
        except json.JSONDecodeError:  # a tool that didn't return JSON — nothing to track
            return
        if not result.get("ok"):
            return
        if name == "write_file":
            path = args.get("path")
            if not isinstance(path, str):
                return
            kind = _classify(path)
            if kind is None:  # not a test file — ignore (e.g. a stray helper)
                return
            content = args.get("content")
            content = content if isinstance(content, str) else ""
            covered = {cid for cid in self.known_ids if cid in content}
            previous = self.files.get(path)
            merged = (previous[1] | covered) if previous is not None else covered
            self.files[path] = (kind, merged)
        elif name == "git_commit":
            sha = result.get("sha")
            if isinstance(sha, str):
                self.last_commit = sha

    def suites(self) -> list[tuple[TestKind, list[str], list[str]]]:
        """Per-kind ``(kind, files, generated_from)`` for kinds that got at least one test."""
        files_by_kind: dict[TestKind, list[str]] = {}
        ids_by_kind: dict[TestKind, set[str]] = {}
        for path, (kind, ids) in sorted(self.files.items()):
            files_by_kind.setdefault(kind, []).append(path)
            ids_by_kind.setdefault(kind, set()).update(ids)
        return [
            (kind, files_by_kind[kind], sorted(ids_by_kind[kind]))
            for kind in (TestKind.unit, TestKind.e2e)
            if kind in files_by_kind
        ]

    def covered_ids(self) -> set[str]:
        covered: set[str] = set()
        for _kind, ids in self.files.values():
            covered |= ids
        return covered


def _testgen_registry() -> ToolRegistry:
    return ToolRegistry([t for t in ALL_TOOLS if t.name in _AUTHOR_TOOL_NAMES])


class TestGenAgent:
    """Drives the plan → author → persist loop and produces a :class:`TestGenReport`."""

    def __init__(
        self,
        client: AnthropicClient | None = None,
        registry: ToolRegistry | None = None,
        artifacts: ArtifactService | None = None,
        requirements: RequirementsService | None = None,
    ) -> None:
        self._client = client or AnthropicClient()
        self._registry = registry or _testgen_registry()
        self._artifacts = artifacts or ArtifactService()
        self._designs = DesignService(self._artifacts)
        self._requirements = requirements or RequirementsService()
        self._suites = TestSuiteRepo()

    async def run(
        self,
        project: Project,
        run: Run,
        *,
        ctx: ToolContext | None = None,
        channel: str | None = None,
        missing: MissingRequirements = MissingRequirements.ask,
        instruction: str | None = None,
    ) -> TestGenReport:
        project_id = project.id
        if project_id is None:  # pragma: no cover - a persisted project always carries an id
            raise UserError("Project is not persisted")
        channel = channel or str(project_id)
        ctx = ctx or ToolContext.build(project, run, channel=channel)

        # Step 1 — resolve the spec, tolerating a skipped requirements stage (D12).
        spec = await self._requirements.latest(project_id)
        inferred = False
        if spec is None or not spec.features:
            if missing is not MissingRequirements.infer:
                return await self._needs_requirements(channel)
            await self._emit(channel, "infer")
            spec = await self._infer_spec(project, project_id, run, channel)
            inferred = True

        # Step 2 — ensure the skeleton (and its test toolchain/config) is present.
        known_ids = {c.id for f in spec.features for c in f.acceptance_criteria}
        tracker = _TestTracker(known_ids=known_ids)
        dispatch = self._make_dispatch(ctx, tracker)
        await self._emit(channel, "skeleton")
        if not await self._is_instantiated(ctx):
            await copy_skeleton(ctx.workspace, ctx.project)
            await ctx.workspace.commit(ctx.project, "skeleton")

        spec_text = format_spec(spec.features)
        notes: list[str] = []
        if inferred:
            notes.append(
                "Requirements were inferred from design/build — review the generated tests."
            )
        if instruction:
            notes.insert(0, f"Focus: {instruction}")

        # Step 3 — plan (Haiku).
        await self._emit(channel, "plan")
        plan = await self._plan(project_id, run, project.name, spec_text, channel)

        # Step 4 — author the tests (Sonnet, bounded FS+git tool loop).
        await self._emit(channel, "author")
        impl = await self._client.run_tool_loop(
            task_kind=TaskKind.testgen,
            project_id=project_id,
            run=run,
            system=system_prompt(TESTGEN_SYSTEM_EXTRA),
            messages=[
                {
                    "role": "user",
                    "content": author_user_prompt(project.name, plan, spec_text, notes),
                }
            ],
            tools=self._registry.anthropic_tools(),
            tool_dispatch=dispatch,
            channel=channel,
        )

        # Step 5 — assemble + persist. A loop that stopped at its bound has still written (and
        # committed) real tests; report it as incomplete rather than presenting partial coverage as
        # the finished job.
        report = self._build_report(
            tracker, inferred, impl.text.strip(), plan, unfinished=not impl.finished
        )
        await self._persist(project_id, report, tracker)
        await emit(
            channel,
            "test.generated",
            {
                "stage": "test",
                "status": report.status,
                "suites": len(report.suites),
                "files": len(report.files),
                "criteria_covered": len(report.criteria_covered),
                "criteria_uncovered": len(report.criteria_uncovered),
                "inferred": report.inferred_spec,
                "commit": report.commit,
            },
            stage=Stage.test,
        )
        return report

    # -- steps ---------------------------------------------------------------------------

    async def _needs_requirements(self, channel: str) -> TestGenReport:
        await self._emit(channel, "needs_requirements")
        return TestGenReport(
            status=STATUS_NEEDS_REQUIREMENTS,
            suites=[],
            files=[],
            criteria_covered=[],
            criteria_uncovered=[],
            inferred_spec=False,
            warning=(
                "No requirements spec found — the requirements stage was skipped. Capture a "
                "minimal spec, or let ForgeFlow infer one from your design/build artifacts."
            ),
            options=["capture", "infer"],
            commit=None,
            summary="",
            plan="",
        )

    async def _plan(
        self,
        project_id: PydanticObjectId,
        run: Run,
        project_name: str,
        spec_text: str,
        channel: str,
    ) -> str:
        result = await self._client.run_tool_loop(
            task_kind=TaskKind.summarize,  # → MODEL_ROUTING (Haiku) — cheap planning
            project_id=project_id,
            run=run,
            system=system_prompt(TESTGEN_PLAN_SYSTEM_EXTRA),
            messages=[{"role": "user", "content": plan_user_prompt(project_name, spec_text)}],
            channel=channel,
        )
        return result.text.strip() or "(no explicit plan produced)"

    async def _infer_spec(
        self, project: Project, project_id: PydanticObjectId, run: Run, channel: str
    ) -> RequirementSpec:
        """Infer a minimal spec from design/build context (Haiku); persist it labeled inferred."""
        design = await self._design_context(project_id)
        build = await self._build_context(project_id)
        result = await self._client.run_tool_loop(
            task_kind=TaskKind.summarize,  # → MODEL_ROUTING (Haiku)
            project_id=project_id,
            run=run,
            system=system_prompt(INFER_SYSTEM),
            messages=[{"role": "user", "content": infer_user_prompt(project.name, design, build)}],
            channel=channel,
        )
        features = _parse_inferred(result.text) or _fallback_features(project.name)
        return await self._requirements.save(
            project_id, RequirementSpecInput(features=features), inferred=True
        )

    # -- context -------------------------------------------------------------------------

    async def _design_context(self, project_id: PydanticObjectId) -> str | None:
        latest = await self._designs.latest(project_id)
        if latest is None:
            return None
        try:
            payload = await self._designs.load_payload(latest)
        except UserError:
            return None
        budget = int(get_config().get("codegen_context_char_budget"))
        return f"HTML:\n{_truncate(payload.html, budget)}\n\nCSS:\n{_truncate(payload.css, budget)}"

    async def _build_context(self, project_id: PydanticObjectId) -> str | None:
        latest = await self._artifacts.get_latest(project_id, Stage.build, ArtifactType.code_change)
        if latest is None:
            return None
        features = latest.meta.get("features_built")
        if isinstance(features, list) and features:
            return "Features built: " + ", ".join(str(f) for f in features)
        content = await self._artifacts.get_content(latest)
        if not content:
            return None
        return _truncate(content, int(get_config().get("codegen_context_char_budget")))

    # -- helpers -------------------------------------------------------------------------

    def _make_dispatch(self, ctx: ToolContext, tracker: _TestTracker) -> ToolDispatch:
        async def dispatch(name: str, raw_args: dict[str, Any]) -> str:
            result = await self._registry.dispatch(ctx, name, raw_args)
            tracker.observe(name, raw_args, result)
            return result

        return dispatch

    async def _is_instantiated(self, ctx: ToolContext) -> bool:
        for marker in _INSTANTIATED_MARKERS:
            try:
                await ctx.workspace.read(ctx.project, marker)
                return True
            except NotFoundError:
                continue
        return False

    def _build_report(
        self,
        tracker: _TestTracker,
        inferred: bool,
        summary: str,
        plan: str,
        *,
        unfinished: bool = False,
    ) -> TestGenReport:
        suites = [
            {"kind": str(kind), "files": files, "generated_from": ids}
            for kind, files, ids in tracker.suites()
        ]
        files = sorted(tracker.files)
        covered = sorted(tracker.covered_ids())
        uncovered = sorted(tracker.known_ids - tracker.covered_ids())
        warning = (
            f"{len(uncovered)} acceptance criteria have no test yet: {', '.join(uncovered)}"
            if uncovered
            else None
        )
        if unfinished:
            stopped = (
                "Test generation did not finish (it hit its tool-turn bound) — the tests written "
                "so far are committed; run it again to continue."
            )
            warning = f"{stopped} {warning}" if warning else stopped
        return TestGenReport(
            status=STATUS_GENERATED,
            suites=suites,
            files=files,
            criteria_covered=covered,
            criteria_uncovered=uncovered,
            inferred_spec=inferred,
            warning=warning,
            options=[],
            commit=tracker.last_commit,
            summary=summary,
            plan=plan,
        )

    async def _persist(
        self, project_id: PydanticObjectId, report: TestGenReport, tracker: _TestTracker
    ) -> None:
        # A versioned TestSuite per kind — the criterion-id join key phase-28/29 read back.
        for kind, files, generated_from in tracker.suites():
            await self._suites.create_version(project_id, kind, files, generated_from)
        # A versioned `test` artifact carrying the traceability report (surfaces in the stage UI).
        await self._artifacts.create_version(
            project_id,
            Stage.test,
            ArtifactType.test,
            text=json.dumps(report.to_dict(), indent=2),
            meta={
                "kind": TESTGEN_REPORT_KIND,
                "inferred": report.inferred_spec,
                "criteria_covered": report.criteria_covered,
                "criteria_uncovered": report.criteria_uncovered,
                "suites": report.suites,
                "commit": report.commit,
            },
        )

    async def _emit(self, channel: str, step: str) -> None:
        await emit(channel, EventType.progress, {"stage": "test", "step": step}, stage=Stage.test)


def _truncate(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    return text[:budget] + "\n…(truncated)…"


def _fallback_features(project_name: str) -> list[FeatureInput]:
    """A last-resort minimal spec so test-gen can still proceed when inference yields nothing."""
    return [
        FeatureInput(
            name=f"{project_name} core".strip() or "Core",
            description="Baseline behavior inferred because requirements were skipped.",
            acceptance_criteria=[
                CriterionInput(text="The app loads without errors", kind=CriterionKind.e2e),
                CriterionInput(
                    text="The backend health endpoint returns ok", kind=CriterionKind.unit
                ),
            ],
        )
    ]


_KIND_VALUES = {k.value for k in CriterionKind}


def _parse_inferred(text: str) -> list[FeatureInput]:
    """Best-effort parse of the infer step's JSON into validated ``FeatureInput``s.

    Drops malformed features and any feature without a criterion (the requirements service requires
    ≥1); an unparseable reply yields ``[]`` so the caller falls back rather than erroring.
    """
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        raw = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return []
    if not isinstance(raw, list):
        return []

    features: list[FeatureInput] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name or name.lower() in seen:
            continue
        criteria = _parse_inferred_criteria(item.get("acceptance_criteria"))
        if not criteria:
            continue
        seen.add(name.lower())
        features.append(
            FeatureInput(
                name=name,
                description=str(item.get("description", "")).strip(),
                acceptance_criteria=criteria,
            )
        )
        if len(features) >= _MAX_INFERRED_FEATURES:
            break
    return features


def _parse_inferred_criteria(raw: Any) -> list[CriterionInput]:
    if not isinstance(raw, list):
        return []
    out: list[CriterionInput] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        kind_raw = str(item.get("kind", "either")).strip().lower()
        kind = kind_raw if kind_raw in _KIND_VALUES else "either"
        out.append(CriterionInput(text=text, kind=CriterionKind(kind)))
        if len(out) >= _MAX_INFERRED_CRITERIA:
            break
    return out


__all__ = [
    "MissingRequirements",
    "TestGenAgent",
    "TestGenReport",
    "STATUS_GENERATED",
    "STATUS_NEEDS_REQUIREMENTS",
    "TESTGEN_REPORT_KIND",
]
