"""The structured build plan (phase-56 task 1) — pure model + parsing, no I/O.

A build is no longer one monolithic implement call: the agent first writes a **phase plan** (a
handful of small, independently typecheckable steps), then implements it one phase at a time. This
module owns the *shape* of that plan — parsing the model's JSON, validating it, rendering it for the
workspace, and the deterministic fallback.

:func:`fallback_plan` is **mandatory, not defensive**. D12 (non-linearity) says a build must work
with requirements-only, design-only, both, or neither; a plan step that can return nothing would
break that. So no requirements, no design, unparseable model output, and an empty phase list all
land on the same deterministic three-phase plan, and :func:`parse_build_plan` never raises.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

PhaseKind = Literal["data", "backend", "frontend", "wiring"]

#: The canonical execution order. It is a *heuristic that makes phases independently typecheckable*
#: in the common case (a frontend page importing a not-yet-written API client will not compile), not
#: a dependency solver — `depends_on` refines the order within it and is rejected on cycles.
KIND_ORDER: tuple[PhaseKind, ...] = ("data", "backend", "frontend", "wiring")

_KIND_RANK = {kind: index for index, kind in enumerate(KIND_ORDER)}

SOURCE_MODEL = "model"
SOURCE_FALLBACK = "fallback"

#: Terminal phase statuses. `pending` is the initial state; only `done` counts for resume.
PHASE_PENDING = "pending"
PHASE_RUNNING = "running"
PHASE_DONE = "done"
PHASE_FAILED = "failed"

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_ID_SAFE = re.compile(r"[^a-z0-9\-]+")


@dataclass(frozen=True)
class BuildPhase:
    """One implementable slice of the build: a small goal, its files, and how to know it is done."""

    id: str
    title: str
    kind: PhaseKind
    goal: str
    files: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    done_when: list[str] = field(default_factory=list)

    #: The pnpm package a phase's typecheck gate is scoped to (``None`` ⇒ the whole workspace).
    @property
    def package(self) -> str | None:
        if self.kind == "frontend":
            return "frontend"
        if self.kind in ("backend", "data"):
            return "backend"
        return None  # `wiring` legitimately touches both halves

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "goal": self.goal,
            "files": list(self.files),
            "depends_on": list(self.depends_on),
            "done_when": list(self.done_when),
        }


@dataclass
class BuildPlan:
    phases: list[BuildPhase] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    source: str = SOURCE_MODEL

    def validate(self, *, max_phases: int) -> BuildPlan:
        """Return a plan that is safe to execute: unique ids, no cycles, topo-ordered, capped.

        Never raises — a plan that cannot be repaired into something runnable comes back empty, and
        the caller substitutes :func:`fallback_plan`. That is what keeps the build unblockable.
        """
        seen: set[str] = set()
        kept: list[BuildPhase] = []
        for phase in self.phases:
            pid = _slug(phase.id) or _slug(phase.title)
            if not pid or pid in seen or not phase.title.strip():
                continue  # a duplicate or unidentifiable phase is dropped, not fatal
            seen.add(pid)
            kept.append(
                BuildPhase(
                    id=pid,
                    title=phase.title.strip(),
                    kind=phase.kind if phase.kind in _KIND_RANK else "backend",
                    goal=phase.goal.strip(),
                    files=[f for f in phase.files if isinstance(f, str) and f.strip()],
                    depends_on=[_slug(d) for d in phase.depends_on if _slug(d)],
                    done_when=[d for d in phase.done_when if isinstance(d, str) and d.strip()],
                )
            )

        # Drop dangling dependencies before ordering, so an id the model invented cannot strand a
        # phase behind something that will never run.
        kept = [
            BuildPhase(
                id=p.id,
                title=p.title,
                kind=p.kind,
                goal=p.goal,
                files=p.files,
                depends_on=[d for d in p.depends_on if d in seen and d != p.id],
                done_when=p.done_when,
            )
            for p in kept
        ]

        ordered = _topo_order(kept)
        self.phases = ordered[:max_phases]
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "phases": [p.to_dict() for p in self.phases],
            "assumptions": list(self.assumptions),
            "notes": list(self.notes),
            "source": self.source,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_prose(self) -> str:
        """A compact prose rendering, so ``BuildReport.plan`` keeps its existing string shape."""
        if not self.phases:
            return "(no explicit plan produced)"
        lines = [
            f"{i}. [{p.kind}] {p.title} — {p.goal}".rstrip(" —")
            for i, p in enumerate(self.phases, start=1)
        ]
        if self.assumptions:
            lines.append("Assumptions: " + "; ".join(self.assumptions))
        return "\n".join(lines)


def _slug(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return _ID_SAFE.sub("-", value.strip().lower()).strip("-")[:48]


def _topo_order(phases: list[BuildPhase]) -> list[BuildPhase]:
    """Kahn's algorithm over ``depends_on``, tie-broken by :data:`KIND_ORDER` then input order.

    A cycle cannot be ordered, so the phases inside it are appended after everything orderable
    rather than dropped — a cyclic plan still runs, just without its (self-contradictory) ordering.
    """
    index = {p.id: i for i, p in enumerate(phases)}
    remaining = {p.id: set(p.depends_on) for p in phases}
    by_id = {p.id: p for p in phases}
    out: list[BuildPhase] = []

    while remaining:
        ready = [pid for pid, deps in remaining.items() if not deps]
        if not ready:  # a cycle — emit what is left in the deterministic tie-break order
            ready = list(remaining)
            out += sorted(
                (by_id[pid] for pid in ready), key=lambda p: (_KIND_RANK[p.kind], index[p.id])
            )
            break
        chosen = sorted(ready, key=lambda pid: (_KIND_RANK[by_id[pid].kind], index[pid]))[0]
        out.append(by_id[chosen])
        del remaining[chosen]
        for deps in remaining.values():
            deps.discard(chosen)
    return out


def parse_build_plan(text: str) -> BuildPlan | None:
    """Parse the plan step's output. Returns ``None`` on anything unusable — never raises."""
    payload = _extract_object(text or "")
    if payload is None:
        return None
    raw_phases = payload.get("phases")
    if not isinstance(raw_phases, list) or not raw_phases:
        return None

    phases: list[BuildPhase] = []
    for item in raw_phases:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        kind_raw = str(item.get("kind") or "backend").strip().lower()
        kind: PhaseKind = kind_raw if kind_raw in _KIND_RANK else "backend"
        phases.append(
            BuildPhase(
                id=str(item.get("id") or title),
                title=title,
                kind=kind,
                goal=str(item.get("goal") or "").strip(),
                files=_str_list(item.get("files")),
                depends_on=_str_list(item.get("depends_on")),
                done_when=_str_list(item.get("done_when")),
            )
        )
    if not phases:
        return None
    return BuildPlan(
        phases=phases,
        assumptions=_str_list(payload.get("assumptions")),
        notes=_str_list(payload.get("notes")),
        source=SOURCE_MODEL,
    )


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if isinstance(v, (str, int, float)) and str(v).strip()]


def _extract_object(text: str) -> dict[str, Any] | None:
    """The first JSON object in ``text`` — bare, fenced, or wrapped in prose."""
    candidates: list[str] = []
    fenced = _FENCE.search(text)
    if fenced is not None:
        candidates.append(fenced.group(1))
    candidates.append(text)
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end <= start:
            continue
        try:
            parsed = json.loads(candidate[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def incremental_plan(instruction: str) -> BuildPlan:
    """A single-phase plan for a small change request (phase-60).

    A refine that touches a few files does not need planning — but it *does* need everything the
    phase runner already provides: a bounded loop, a scoped typecheck gate, a fix sub-loop and a
    commit. So the change becomes one synthetic ``wiring`` phase and reuses that machinery, rather
    than growing a second implementation path that would drift from it.

    ``wiring`` is deliberate: it is the one kind whose typecheck gate covers *both* packages, and a
    small change is not known in advance to stay on one side of the app.
    """
    goal = instruction.strip()
    return BuildPlan(
        phases=[
            BuildPhase(
                id="change",
                title="Apply the requested change",
                kind="wiring",
                goal=goal,
                done_when=[
                    "The requested change is implemented in the existing code.",
                    "Nothing else about the app's behaviour changed.",
                ],
            )
        ],
        assumptions=[],
    )


def fallback_plan(features: list[str], *, has_design: bool) -> BuildPlan:
    """The deterministic plan — what makes a build possible with no upstream artifacts (D12)."""
    feature_text = ", ".join(features[:6]) if features else "a minimal but genuinely useful feature"
    ui_note = (
        "Map the provided design HTML/CSS into idiomatic React + Tailwind components."
        if has_design
        else "Design a clean, minimal Tailwind UI — no design artifact was provided."
    )
    assumptions: list[str] = []
    if not features:
        assumptions.append(
            "No structured requirements were available — inferred a minimal feature set."
        )
    if not has_design:
        assumptions.append("No design artifact was available — the UI is designed from scratch.")

    return BuildPlan(
        phases=[
            BuildPhase(
                id="be-core",
                title="Backend data models and API",
                kind="backend",
                goal=(
                    f"Add the Mongoose models, Zod schemas, controllers and routes for: "
                    f"{feature_text}. Mount each router in backend/src/app.ts."
                ),
                files=["backend/src/features/", "backend/src/app.ts"],
                done_when=[
                    "backend typechecks",
                    "every feature has a model, a schema, a controller and a mounted router",
                ],
            ),
            BuildPhase(
                id="fe-core",
                title="Frontend pages and API calls",
                kind="frontend",
                goal=(
                    f"Add the pages and components for: {feature_text}, calling the backend only "
                    f"through the typed client in frontend/src/lib/api.ts. {ui_note}"
                ),
                files=["frontend/src/features/", "frontend/src/lib/api.ts"],
                depends_on=["be-core"],
                done_when=[
                    "frontend typechecks",
                    "every feature has a page that reads and writes through the API client",
                ],
            ),
            BuildPhase(
                id="wiring",
                title="Routing, layout and app identity",
                kind="wiring",
                goal=(
                    "Make the index route render a real feature page, put the app's name in the "
                    "layout wordmark and the HTML title, and rewrite the placeholder tests to "
                    "assert real app content."
                ),
                files=[
                    "frontend/src/routes.tsx",
                    "frontend/src/pages/HomePage.tsx",
                    "frontend/src/components/Layout.tsx",
                    "frontend/index.html",
                ],
                depends_on=["fe-core"],
                done_when=[
                    "the index route renders a real feature page, not the placeholder HomePage",
                    "no 'ForgeFlow', 'Your app starts here' or 'Learn Vite' string survives",
                ],
            ),
        ],
        assumptions=assumptions,
        source=SOURCE_FALLBACK,
    )


_STATUS_GLYPH = {
    PHASE_DONE: "x",
    PHASE_FAILED: "!",
    PHASE_RUNNING: ">",
    PHASE_PENDING: " ",
}


def render_markdown(plan: BuildPlan, outcomes: dict[str, str] | None = None) -> str:
    """The human checklist written to the workspace, re-rendered after every phase."""
    outcomes = outcomes or {}
    lines = [
        "# Build phase plan",
        "",
        "ForgeFlow implements this app one phase at a time. Each phase is written, typechecked "
        "and committed on its own; this file is rewritten as the build progresses.",
        "",
        f"_Plan source: {plan.source}_",
        "",
    ]
    for index, phase in enumerate(plan.phases, start=1):
        status = outcomes.get(phase.id, PHASE_PENDING)
        lines.append(
            f"- [{_STATUS_GLYPH.get(status, ' ')}] **{index}. {phase.title}** "
            f"(`{phase.kind}`) — {status}"
        )
        if phase.goal:
            lines.append(f"      - {phase.goal}")
        for check in phase.done_when:
            lines.append(f"      - done when: {check}")
        if phase.files:
            lines.append("      - files: " + ", ".join(f"`{f}`" for f in phase.files))
    if plan.assumptions:
        lines += ["", "## Assumptions", ""] + [f"- {a}" for a in plan.assumptions]
    if plan.notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in plan.notes]
    return "\n".join(lines) + "\n"


__all__ = [
    "BuildPhase",
    "BuildPlan",
    "KIND_ORDER",
    "PHASE_DONE",
    "PHASE_FAILED",
    "PHASE_PENDING",
    "PHASE_RUNNING",
    "PhaseKind",
    "SOURCE_FALLBACK",
    "SOURCE_MODEL",
    "fallback_plan",
    "incremental_plan",
    "parse_build_plan",
    "render_markdown",
]
