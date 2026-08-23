# Phase 60 — Scope-aware build & OpenAI-first posture

## Title & ID

- **ID:** `phase-60`
- **Title:** Scope-aware build (refine fast path), configurable classifier model, OpenAI-first posture
- **Epic:** Epic 13 — Build quality *(amends `phase-56`)*
- **Milestone:** M5
- **Status:** ✅ done *(2026-08-23)*

## Goal

A one-line change request must be answered with a one-pass edit, not a re-planned multi-phase build.
Route the build by the **scope of what was asked**, on a model that is configurable per provider —
and record, project-wide, that OpenAI-compatible endpoints are now a first-class target rather than
an Anthropic alternative.

## Context

**The reported behaviour.** With a built Todo app on screen, the user typed into *Refine Build*:
*"The home page just has a sign in prompt but no actual button to help me sign in, please fix"* — a
single-component fix. ForgeFlow replanned the whole build into phases and executed them one by one.

**The cause is unconditional.** `CodegenAgent.run` (`app/agents/codegen.py:340-364`) always plans:

```python
await self._emit(channel, "plan", run)
build_plan = await self._planner.plan(..., instruction=instruction, ...)
...
phased = await self._run_phases(...)
```

`instruction` is threaded *into* the planner (`:351`) rather than used to decide **whether** to plan.
The build stage handler already distinguishes the two entry points —
*"A refine carries the change-request instruction; a proceed is the initial build"*
(`app/orchestrator/stages/build.py:70-71`) — and then discards the distinction. `phase-56`'s
phasing is right for a cold build (small context, per-phase typecheck gate, resumability) and is
pure overhead for a two-file edit: planning tokens, per-phase turns, and a `phase-plan/` rewrite for
a change that touches one component.

**Why not a pure heuristic.** *"add dark mode across the whole app"* is also short, also a refine,
and genuinely wants phases. Scope is a property of the request, not of its length — which is exactly
what the cheap routing model is for (D4 / Golden Rule 7).

**Provider posture.** The user's directive, 2026-08-23: the classifier must be configurable when
running against an OpenAI-compatible endpoint, and the project should record that it *"will not be
considering on priority anthropic endpoint only but rather openai endpoint (as this is a more
globally used standard which many providers support)."* `phase-53` made the provider swappable and
`phase-57` made the request shape self-repairing, but every doc still reads Anthropic-first: `CLAUDE.md`
lists the `anthropic` SDK under *Tech stack* and pins `MODEL_CODEGEN`/`MODEL_ROUTING` to Claude ids,
and `.env.example` describes `LLM_PROVIDER=openai` as the alternative. Nothing in the *code* is
Anthropic-only; the **documentation** is, and that is what this task corrects.

## Depends on

- `phase-23` (codegen loop) · `phase-53` (OpenAI provider) · `phase-55` (build verification) ·
  `phase-56` (phase planning) · `phase-57` (param compatibility) — all ✅.
- No new external dependency; the classifier runs on the existing transport seam.

## Detailed tasks

### 1. Scope classifier (`app/agents/build_scope.py`, new)

- `classify_scope(instruction, *, has_build, changed_surface) -> BuildScope` returning
  `small` | `large`, with a confidence and a one-line reason (surfaced to the user, not hidden).
- Runs on `TaskKind.classify` — a **new** kind alongside `summarize`, so classification is priced
  and routed independently of summarisation.
- **Heuristic fallback, always available**: no instruction → `large` (a cold build always plans); an
  explicit *Rebuild*/`fresh` → `large`; classifier error, budget exhaustion or an unparseable reply →
  fall back deterministically and log, never block a build on the classifier (the `phase-56`
  fallback-plan posture).
- Bounded like every other model call: one attempt, small max-tokens, no tool use.

### 2. Configurable classifier model (`app/core/config.py`, `config_registry.py`, `.env.example`)

- `MODEL_CLASSIFY`, defaulting to **`MODEL_ROUTING`** when blank, so existing deployments change
  nothing and an OpenAI operator can point classification at a different (often cheaper) model than
  routing without touching codegen.
- Priced through the existing `MODEL_PRICING_JSON` path; appears in `/admin` like every other field
  (`tests/config/test_registry_complete.py` enforces this automatically).
- `TaskKind.classify` → `MODEL_CLASSIFY` in `app/agents/models.py`.

### 3. Route the build (`app/agents/codegen.py`, `app/orchestrator/stages/build.py`)

- `CodegenAgent.run` branches **before** planning:
  - `large` → today's path, unchanged (plan → phases → verify).
  - `small` → a single incremental codegen loop with the change-request context `phase-23` already
    builds, then straight to `phase-55` verification. No `BuildPlanner` call, no `phase-plan/`
    rewrite, no per-phase commits — one commit for the change.
- `BuildReport` records `scope` and the classifier's reason; `phases` stays empty for a small pass,
  which `phases_complete` (`:205-207`) already treats as vacuously true — so the stage-completion
  logic in `build.py:105-108` needs no change.
- Verification is **not** skipped. A small change still installs, typechecks, boots and gets the
  placeholder check, and still self-heals through the bounded repair loop. Cheapness comes from
  skipping *planning*, never from skipping *proof*.
- The existing `phase-plan/` on disk is left intact by a small pass — a later large build resumes
  from it rather than finding it half-rewritten.

### 4. Make the routing visible (`frontend/src/features/build/`)

- The build header states which path ran: *"Incremental change"* vs *"Planned build · N phases"*,
  with the classifier's reason on hover.
- An explicit override on the refine box for when the classifier is wrong: force a planned build.
  Cheap to add, and it means a misclassification is a click to correct rather than a dead end.

### 5. OpenAI-first posture (docs)

- `CLAUDE.md` — *Tech stack* and *Golden rule 7*: state that ForgeFlow targets **OpenAI-compatible
  Chat Completions as the primary interface**, with Anthropic as a fully-supported provider behind
  the same seam; model ids are configuration, not identity. Keep the cost-discipline rule intact
  (cheap model for classification/summaries/routing, capable model for code) but express it in terms
  of `MODEL_ROUTING`/`MODEL_CLASSIFY`/`MODEL_CODEGEN` rather than Haiku/Sonnet.
- `IMPLEMENTATION_PLAN.md` §1 — annotate the model decision (D4) with the amendment and its date.
- `.env.example` — lead the LLM section with the provider-neutral keys; `LLM_PROVIDER` documented as
  a first-class choice rather than an escape hatch.
- `DEVELOPER.md` §3 — fold the OpenAI section into the main configuration flow.

## Out of scope for this phase

- Changing what a *large* build does — `phase-56`'s planner, phase runner and gates are untouched.
- Changing the repair loop, its bounds, or `phase-55`'s classifier.
- Removing Anthropic support, or changing any default model id.
- Multi-turn conversational refinement (each refine remains one costed run).

## Acceptance criteria

1. *"the sign-in button is missing, please fix"* on a built project runs **one** codegen pass with no
   plan artifact, no `phase-plan/` rewrite, and no per-phase events — and still verifies.
2. *"add dark mode across the whole app"* still plans and phases.
3. The initial Build (no instruction) and an explicit Rebuild always plan.
4. A classifier failure falls back deterministically and the build proceeds.
5. `MODEL_CLASSIFY` blank ⇒ classification uses `MODEL_ROUTING`; set ⇒ it is used, priced and
   attributed separately in the cost panel; works on both providers.
6. The UI states which path ran and why, and can force a planned build.
7. No doc presents Anthropic as the only or default-priority endpoint.

## Tests to write

- `tests/agents/test_build_scope.py` — small/large classification on representative instructions
  (fake transport); every fallback route (no instruction, `fresh`, error, unparseable, budget).
- `tests/agents/test_codegen_small_path.py` — a small scope calls neither `BuildPlanner.plan` nor
  `PhaseRunner.run`, emits no phase events, writes no plan artifact, and **still** calls the
  verifier.
- `tests/agents/test_codegen_large_path.py` — a large scope is byte-for-byte today's behaviour
  (regression guard for `phase-56`).
- `tests/config/test_model_classify.py` — `MODEL_CLASSIFY` resolution, blank-default to
  `MODEL_ROUTING`, admin-layer override.
- `tests/orchestrator/stages/test_build_handler.py` (extend) — refine → small, proceed → large,
  forced-plan override → large.
- `frontend/src/features/build/BuildPanel.test.tsx` (extend) — the path indicator and the override.

## Manual verification steps

1. Build the hero todo app; refine with a one-line fix; watch a single pass and one commit.
2. Refine with a cross-cutting request; confirm phases return.
3. Set `MODEL_CLASSIFY` to a different model in `/admin`; confirm the cost panel attributes it.
4. Point `LLM_PROVIDER=openai` at a compatible endpoint and repeat 1–2.

## Rollback / cleanup notes

- Reverting restores unconditional planning; no persisted shape changes (`scope` on `BuildReport` is
  additive and optional). `MODEL_CLASSIFY` blank is already the current behaviour.

## Design notes

- **The distinction already existed; it was just discarded.** `build.py:70-71` names the refine/
  proceed split in a comment and then passes both down the same path. This phase makes the comment
  true rather than introducing a new concept.
- **Cost discipline is strengthened, not relaxed** (Golden Rule 7). A cheap classifier call replaces
  a planning call plus N phase loops on the common case. The expensive model still does every line
  of real code.
- **Bounded stays bounded** (Golden Rule 5). The small path runs one codegen loop and the *same*
  `phase-55` verification and repair bounds. Nothing gains an unbounded retry.
- **The docs change, the architecture does not.** `phase-53`'s seam already makes provider a runtime
  choice; `phase-57` removed the last vendor-shaped assumption from the request. Declaring
  OpenAI-compatible primary is recording where the code already is.

## Implementation status (as-built, 2026-08-23)

**The distinction existed; it was discarded.** `build.py` already commented *"A refine carries the
change-request instruction; a proceed is the initial build"* and then passed both down the same
path. `CodegenAgent.run` now decides **before** planning.

**Classifier.** New `app/agents/build_scope.py`: `heuristic_scope` answers without a model wherever
it can (no instruction, `fresh`, no existing build, explicit override), and only a genuine refine on
a built project reaches `ScopeClassifier`. That call is `TaskKind.classify`, `tools=[]`,
`max_turns=1` — a verdict, not a task. Every failure route (`UserError`/`NotFoundError`, unparseable
reply, ambiguous reply naming both words) falls back to `large`, so a build is never blocked on the
classifier. `parse_scope` tolerates `**SMALL**` / `Answer: SMALL` / `SMALL.` but rejects a reply
naming both, because resolving ambiguity by word order would be a coin flip.

**Routing.** `MODEL_CLASSIFY` added, blank ⇒ `MODEL_ROUTING`, so an existing deployment changes
nothing. `route()` in `app/agents/models.py` is the only place that knows.

**The small path reuses the machinery.** Rather than a second implementation, a small change becomes
a **single synthetic `wiring` phase** (`incremental_plan`) run through the existing `PhaseRunner` —
same bounded loop, same typecheck gate, same fix sub-loop, same commit. `wiring` is deliberate: it
is the one kind whose gate spans both packages, and a small change is not known in advance to stay
on one side. Skipped for a small pass: the `BuildPlanner` call and the `phase-plan/` rewrite
(`write_plan_files=False`, so a real plan on disk is not clobbered). **Verification is not skipped** —
install → typecheck → boot → placeholder and the bounded repair loop run exactly as before.

**A resume hazard found while wiring it.** The synthetic phase id is stable (`change`), so
`_resume_point` would have seen the *previous* refine's phase recorded as `done` and skipped the new
request entirely. The small path forces `resume_from=0`; `test_two_small_changes_must_not_share_a_
resume_id` pins the property that makes that necessary.

**UI.** The header states which path ran (*"Incremental change"* / *"Planned build · N phases"*) with
the classifier's reason on hover, and the overflow menu gains **Force a planned build** — a
misclassification costs a click, not a dead end. No badge at all for reports written before scope
routing, rather than guessing.

**Provider posture.** `CLAUDE.md` now states OpenAI-compatible Chat Completions as the **primary**
interface with Anthropic fully supported behind the same seam; Golden Rule 7 is expressed in terms
of `MODEL_CLASSIFY`/`MODEL_ROUTING`/`MODEL_CODEGEN` rather than Haiku/Sonnet, and the *Tech stack*
and testing bullets follow. D4 is **annotated, not changed** — the roles are locked, the vendor is
not. `DEVELOPER.md`'s OpenAI section is folded into the main configuration flow.

**Verification.** `tests/agents/test_build_scope.py` 25, `test_codegen_scope_routing.py` 19,
`tests/config/test_model_classify.py` 6, `BuildPanel.test.tsx` 17 — all passing. `ruff` clean;
`mypy` clean apart from the 4 pre-existing `runtime.py` errors. Frontend typecheck clean.

**Deferred (as scoped):** no live end-to-end refine against a real provider (needs a key and a built
project) — the classifier is covered by a fake transport, and the routing decision by unit test.
`phase-56`'s planner, phase runner and gates are untouched.

