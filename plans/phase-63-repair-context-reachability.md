# Phase 63 — Repair context reachability: import-graph targets, per-test coverage, and a bounded file request

## Title & ID

- **ID:** `phase-63`
- **Title:** Repair context reachability — follow the failing test's imports, judge coverage per test, let the agent request a named file, and treat a no-op attempt as its own signal
- **Epic:** Epic 6 — Self-Healing Repair Loop *(amends `phase-29`/`phase-30`/`phase-31` and the widener added with `phase-55`)*
- **Milestone:** M3
- **Status:** ✅ done *(2026-08-30)*

## Goal

Make **Repair** hand the agent the file the failing test actually exercises, so a frontend
component test can be repaired at all — and when it still cannot, fail honestly and cheaply instead
of re-sending an identical context until the iteration budget runs out.

## Context

Reported from a live session on 2026-08-30 (project *Simple Todo App*, 10/13 passing). Pressing
**Repair** in the Test stage produced this, verbatim, from `MODEL_CODEGEN`:

> I cannot fix this from `main.tsx` alone. The bug is that two elements match the label regex:
> 1. `<form aria-label="Add a new task">` — this `<form>` is a landmark with an `aria-label`, so
>    `getByLabel(/new task/i)` finds it because "Add a new task" contains "new task".
> 2. `<input id="new-todo-title">` — found via its associated `<label>` text "New task".
>
> Both currently match. The test asserts `getByLabelText(/new task/i)` should resolve to a single
> element — the input. So one of these must change.
>
> The only way to resolve this from `main.tsx` is… there isn't one. `main.tsx` doesn't render the
> form, doesn't render the input, doesn't render the label. The fix has to live in the component
> that …

The model is right, and it did exactly what `REPAIR_SYSTEM_EXTRA` asks of it (*"If the fix truly
requires a file you were not given, do not guess — say so plainly in your summary instead"*). The
defect is upstream, in what the context contained.

**Root cause — widening is directory-shaped, not import-shaped.** `RepairContextAnalyzer` builds
`target_files` from the paths a failure's stack *names* (`app/agents/repair_context.py:341-358`).
An assertion failure names only its own spec, which is read-only during repair, so
`SuiteRepairContextAnalyzer` widens (`app/agents/suite_context.py:119-135`) to the **same-stem
sibling and then the other sources in the same folder**. For a backend feature test
(`todos.test.ts` beside `todos.controller.ts`) that is the right answer. For the generated
frontend it is not: the spec lives at `frontend/src/App.test.tsx`, and the only sources in
`frontend/src/` are `main.tsx`, `routes.tsx` and `vite-env.d.ts`. Sorted, `main.tsx` comes first —
which is precisely the file the model was given. The component under test sits in
`frontend/src/pages/` or `frontend/src/features/<feature>/`, a *different directory*, and the one
piece of evidence that names it — the spec's own `import { HomePage } from './pages/HomePage'` — is
never read. The skeleton makes this structural rather than incidental: `templates/app-skeleton/`
ships `frontend/src/App.test.tsx` whose subject is `frontend/src/pages/HomePage.tsx`, so **every**
generated frontend unit test starts one directory away from its subject.

**Aggravator 1 — coverage is judged once, globally.** `uncovered()` (`suite_context.py:172-190`)
correctly asks *per failing test* whether anything patchable is implicated, but it is evaluated
only on the pre-widening set, and tiers 2 and 3 ("changed since last-passing", "the failing suite's
source tree") are gated on `editable_paths(context)` being **globally empty**
(`suite_context.py:236-255`). Tier 1 admitted `main.tsx`, so the set was non-empty, so tier 3 —
which would have pulled in `frontend/src/**` and *did* contain the answer — never ran. A tier that
contributes an irrelevant file therefore permanently suppresses the tiers that would have
contributed the relevant one.

**Aggravator 2 — an attempt that writes nothing is scored as ordinary no-progress.** The controller
(`app/orchestrator/stages/repair.py:283-348`) only special-cases `result.attempt is None` (no
editable files at all). Here there *was* an editable file, so an attempt is recorded with
`target_files: []`, the failing count is unchanged, `stall` increments, and the loop re-assembles a
**deterministically identical** context and asks again. The escalation the user finally sees says
"the failing tests stopped shrinking" and "I patched: no files", while the model's actual
explanation — the most useful sentence in the whole run — is dropped on the floor.

**Directive (2026-08-30).** Asked how the agent should get the file it needs, the user chose
**Both**: deterministic import-graph widening *and* a bounded request-a-file escape hatch. Asked
what a no-op attempt should do, the user chose **retry once, then escalate** with the model's own
words.

## Depends on

- **Phase IDs:** `phase-28` (normalized results + `files_referenced`), `phase-29` (minimal context
  + `add_files` budget), `phase-30` (patch agent + minimality guard), `phase-31` (loop controller),
  `phase-51` (layered config + registry), `phase-32` (Test-stage UI).
- **External:** —

### Prerequisite check

All ✅. This phase edits `app/agents/repair_context.py`, `app/agents/suite_context.py`,
`app/agents/repair.py`, `app/agents/prompts/repair.py`, `app/orchestrator/stages/repair.py`,
`app/core/config.py`, `app/core/config_registry.py` and one frontend label map; it adds one new
module (`app/agents/import_graph.py`).

## Detailed tasks

### 1. Import resolver — `app/agents/import_graph.py` (new, pure)

Deterministic, model-free, no I/O of its own (reads come through an injected `read` callable), so
it is exhaustively unit-testable — the same property that makes `repair_context` testable.

- `import_specifiers(source: str) -> list[str]` — every module specifier in a TS/JS source, in
  source order, deduped: `import x from 's'`, `import 's'`, `import type … from 's'`,
  `export … from 's'`, `await import('s')`, `require('s')`. Regex-based on purpose (a parser is not
  worth the dependency, and a missed exotic form degrades to today's behaviour).
- `resolve_specifier(from_path, spec, tree: set[str]) -> str | None` — resolve **relative**
  (`./`, `../`) and `@/` alias specifiers against the workspace listing; bare specifiers (`react`,
  `express`, `@testing-library/react`) return `None`, so `node_modules` can never enter the
  context. Candidates, in order: the literal path (when it already carries a source extension),
  `p + ext` for each of `.ts .tsx .js .jsx .mts .cts`, then `p/index + ext`. A `.js`/`.jsx`
  specifier that does not exist also probes the `.ts`/`.tsx` sibling (TS ESM style). `@/x` resolves
  against `<half>/src/x`, where `<half>` is the importing file's first path segment.
- `async reachable_sources(entry, read, tree, *, max_depth, max_files) -> list[tuple[str, int]]` —
  breadth-first from `entry`, returning `(path, depth)` in BFS order: depth 1 = imported by the
  spec itself. Already-visited paths are skipped, `max_depth` and `max_files` bound the walk, and a
  read failure skips that node instead of raising. `read` is
  `Callable[[str], Awaitable[str | None]]` so the caller owns caching. Test files are *reported*
  rather than filtered here — a spec that imports a shared spec must not lose its own subject, and
  `is_test_file` in the widener (and in `request_file`) is what decides admission, so the oracle
  stays read-only either way.

### 2. Wire the graph into `SuiteRepairContextAnalyzer` (`app/agents/suite_context.py`)

Candidate order per uncovered failing test — proximity to the test, strongest evidence first:

1. the **same-stem sibling** (`todos.test.ts` → `todos.ts`) — unchanged, still near-certain;
2. the spec's **direct imports** (depth 1) — this is the tier that fixes the report;
3. the spec's **other directory neighbours** — unchanged behaviour for backend feature tests;
4. **transitive imports** (depth 2…`repair_import_max_depth`) in BFS order.

`companion_sources` splits into `same_stem_sources` + `directory_sources` so the graph can be
interleaved between them; `companion_sources` itself stays exported with today's semantics (its
unit tests are unchanged). Every candidate still passes through `is_test_file` and
`RepairContextAnalyzer.add_files`, so the oracle stays read-only and `repair_context_max_chars`
still bounds the result. A single `dict[str, str]` read cache spans the walk and is handed to
`add_files` (new optional `contents=` parameter) so a file the walker already read is not read from
the sandbox twice.

### 3. Judge coverage per failing test, after every tier

- `uncovered(context, contributed=None)` gains an optional `dict[int, list[str]]` (failing-test
  index → paths widened *for that test*); a test counts as covered when any implicated **or
  contributed** path is in `editable_paths(context)`. The one-argument call keeps today's meaning.
- `analyze()` re-evaluates after each tier and runs the next tier **for the tests still
  uncovered**, instead of gating on the global editable set being empty. Tier 2 (changed since
  last-passing) and tier 3 (the failing suite's source tree, scoped to that spec's half of the app)
  therefore still fire for a test the earlier tiers missed, even when some *other* failure already
  contributed an editable file. Notes still record each widening, once.

### 4. `request_file` — the bounded escape hatch (`app/agents/repair.py`, `prompts/repair.py`)

A per-attempt tool, built as a closure and appended to the attempt's `ToolRegistry` (so schema
validation, the `Run` tool-call trace and the JSON error envelope all come for free):

- **Args:** `path` (workspace-relative) and `reason` (one line, recorded).
- **Grants** read *and* write on success, returning the file's contents in the same result so no
  follow-up `read_file` is needed; appends a `ContextFile` to `context.target_files` and a note, so
  the persisted context blob shows exactly what the attempt widened and why.
- **Refuses**, with a message the model can act on: a test file (the oracle stays read-only), a
  non-source path, a path that is not in the workspace, or a request past
  `repair_request_file_max` (default 3 per attempt). Refusals are counted in
  `RepairResult.blocked_writes`' sibling `requested_files` trail and surfaced in the
  `repair.attempt` event.
- **Discoverability:** the model names a path it has *seen* — in an import statement of a file it
  was given, or in a stack. No directory listing is added; `list_dir` stays out of the repair tool
  surface, so "never feed the whole repo" holds.
- `REPAIR_SYSTEM_EXTRA` replaces "do not guess — say so plainly" with: call `request_file` with the
  path (up to N per attempt), and only if that still leaves you stuck, say so plainly.

### 5. A no-op attempt is its own terminal signal (`app/orchestrator/stages/repair.py`)

- New `REASON_NO_PATCH = "no_patch"` with reason text *"I could not fix it from the files I was
  given"*.
- The controller counts **consecutive** attempts with an empty `files_written`; at
  `repair_max_noop_attempts` (default 2) it escalates with `REASON_NO_PATCH` and passes the
  agent's own `summary` (capped at 600 chars) as the escalation detail, so the user reads the
  model's explanation rather than "I patched: no files". A writing attempt resets the counter.
- Checked after the green and regression guards, before the stall guard — a no-patch diagnosis is
  strictly more precise than "stopped shrinking" and must win when both trip on the same iteration.
- `ConvergenceMetrics.noop_attempts` records the total (the loop is the contribution — its failure
  modes are measured, D7/D13).

### 6. Config (`app/core/config.py`, `app/core/config_registry.py`)

Four knobs, all under `Cat.repair`, all registered (`tests/config/test_registry_complete.py`
requires every `Settings` field to be admin-visible):

| Key | Default | Meaning |
|---|---|---|
| `repair_import_max_depth` | 3 | How far the import walk follows a failing spec. |
| `repair_import_max_files` | 24 | Hard cap on files visited per walk. |
| `repair_request_file_max` | 3 | Files the agent may request per attempt. |
| `repair_max_noop_attempts` | 2 | Consecutive attempts writing nothing before escalating. |

### 7. Frontend (`frontend/src/features/testing/Escalation.tsx`)

One entry in `REASON_LABELS`: `no_patch` → *"Couldn't fix it from the files it was given"*. The
summary body already renders the detail, so nothing else changes.

## Out of scope for this phase

- Changing what the *test generator* emits (the ambiguous `getByLabelText` matcher in the report is
  a legitimate assertion; the component is the right place to fix it).
- Relaxing the read-only-oracle rule, in any form.
- `list_dir`, tree browsing, or any "search the repo" tool in the repair surface.
- The Build-stage widener (`app/agents/build_context.py`) — its fallback is the file list the build
  just wrote, which does not have this failure mode.
- Re-running repair automatically after an escalation.

## Acceptance criteria

1. For a failing `frontend/src/App.test.tsx` whose spec imports `./pages/HomePage`, the assembled
   repair context contains `frontend/src/pages/HomePage.tsx` and it is **editable**.
2. Bare specifiers never enter the context — no `node_modules` path, and no `react`-like entry.
3. The import walk is bounded: no more than `repair_import_max_files` files visited and no deeper
   than `repair_import_max_depth`; `repair_context_max_chars` still caps the whole context and
   trimming is still recorded.
4. A test file is never editable, whether it arrived via the stack, the graph, or `request_file`.
5. A failing test left uncovered by the companion/import tier still reaches the changed-since-passing
   and suite-source tiers, even when another failing test already contributed an editable file.
6. `request_file` grants a named source file (read + write) within its cap, records it on the
   context, and refuses a test file, an unknown path, and the (N+1)th request.
7. Two consecutive attempts that write nothing end the loop with `outcome=escalated`,
   `reason=no_patch`, and an escalation summary containing the agent's own explanation. One no-op
   followed by a writing attempt does **not** escalate on that basis.
8. Backend feature tests keep their existing behaviour: the same-stem companion is still the first
   candidate for `todos.test.ts`, and the existing repair suites pass unchanged.
9. `uv run ruff check . && uv run black --check . && uv run mypy .` clean; `pnpm lint` and
   `pnpm test` clean.

## Tests to write

**Backend**

- `backend/tests/agents/test_import_graph.py` — specifier extraction across `import` / `import
  type` / side-effect / `export … from` / dynamic `import()` / `require`; relative, `..`, alias and
  bare resolution; `index` files; the `.js` → `.ts` probe; depth and file caps; an unreadable node
  skipped rather than raised.
- `backend/tests/agents/test_suite_repair_context.py` (extend) — the reported case end-to-end
  through the analyzer: `App.test.tsx` → `pages/HomePage.tsx` editable, `main.tsx` not the only
  candidate; ordering (same-stem before direct imports before directory neighbours); a second
  failing test still reaching tiers 2/3 while another failure is already covered; existing tests
  unchanged.
- `backend/tests/agents/test_repair_request_file.py` — grant within cap returns contents and makes
  the path writable; refuses a test file, a missing path and the over-cap request; the granted file
  appears in the persisted context.
- `backend/tests/orchestrator/test_repair_loop_noop.py` — two no-op attempts escalate with
  `no_patch` and the agent's summary; a no-op then a fixing attempt converges normally;
  `noop_attempts` is recorded.
- `backend/tests/config/…` — covered by the existing completeness test once the four keys are
  registered.

**Frontend**

- `frontend/src/features/testing/Escalation.test.tsx` (extend) — the `no_patch` reason renders its
  label and the summary text.

## Manual verification steps

1. Seed the reported failure: a generated todo app where `frontend/src/App.test.tsx` renders the
   page and asserts `getByLabelText(/new task/i)`, with the form carrying
   `aria-label="Add a new task"`.
2. Run the tests (red), press **Repair**. The attempt's editable list must name the page/component
   file, and the patch must land there.
3. Inspect the persisted repair-context blob for the run: the component is present, `node_modules`
   is absent, and the notes record which tier contributed it.
4. Force the hatch: temporarily drop `repair_import_max_depth` to 0, press **Repair**, and confirm
   the agent calls `request_file` for the component and patches it.
5. Force a no-op: point the tests at a genuinely unpatchable expectation and confirm the loop stops
   after two attempts showing **"Couldn't fix it from the files it was given"** plus the model's
   explanation, rather than five iterations of stall.

## Rollback / cleanup notes

Additive and reversible. Setting `repair_import_max_files` to 0 disables the graph tier, and
`repair_request_file_max` to 0 disables the hatch, returning the analyzer to phase-55 behaviour
without a deploy. The new module is standalone; no schema or migration is involved, and existing
persisted contexts/attempts stay readable.

## Design notes

- **Why the import graph is the right widener.** §9's invariant is *never feed the whole repo when
  a diff suffices* — minimality, not smallness for its own sake. "The files a test exercises" is a
  question the workspace can answer exactly: it is the module graph rooted at the spec. Directory
  adjacency was a proxy for it that happens to hold on the backend and to fail on the frontend,
  where the skeleton deliberately separates `src/` shell files from `pages/`, `components/` and
  `features/`. Following imports makes the context *more* precise, not larger — and the walk is
  bounded twice over (depth/file caps, then the char budget).
- **Why coverage must be per test.** The run-level gate is a false economy: one failure that names
  its source makes the whole run look covered, and every other failure becomes permanently
  unrepairable no matter how many iterations it is given. Per-test judgement was already the stated
  intent of `uncovered()`; this phase extends it across the tiers rather than only into the first.
- **The escape hatch, and why it does not weaken the invariant.** `request_file` is bounded (≤3 per
  attempt), justified (a `reason` string, recorded), audited (the granted file lands in the context
  blob and the attempt event), and filtered (never a test file). The agent still cannot browse: it
  can only name a path it has already seen in an import or a stack. That is a deliberate, recorded
  relaxation of "the context is assembled deterministically before the call", authorised by the
  user on 2026-08-30, and it exists for the residual case the graph cannot reach — a subject that
  is registered rather than imported, e.g. via a route table.
- **Why a no-op attempt escalates rather than retrying to the cap.** The context is assembled
  deterministically from the current failing run; if the agent wrote nothing, the next iteration
  poses a byte-identical question. Retrying once absorbs sampling variance; retrying five times
  buys nothing and spends `MODEL_CODEGEN` tokens to reproduce the same refusal (Golden Rule 7).
  Escalating with the model's own explanation is what "prefer escalation over silent failure"
  means when the agent has already diagnosed the problem in plain English.
- **No locked decision is deviated from.** The generated-app stack is untouched; no code runs
  outside the sandbox; the loop stays bounded, with two new caps and one new terminal reason rather
  than a longer leash; the oracle stays read-only, so a green run still cannot be faked; artifacts
  are still versioned; classification/summaries still route to the cheap model and only the patch
  step to `MODEL_CODEGEN`.

## Status

✅ done

### Completion notes

- **Import walk** [`app/agents/import_graph.py`](../backend/app/agents/import_graph.py) — new, pure,
  no I/O of its own (reads arrive through an injected callable). `import_specifiers` covers
  `import`/`import type`/side-effect/`export … from`/dynamic `import()`/`require`;
  `resolve_specifier` resolves relative and `@/` specifiers with extension, `index` and TS-ESM
  `.js`→`.ts` probing; `reachable_sources` walks breadth-first returning `(path, depth)`.
  **A bare specifier resolves to `None`**, which is what keeps `node_modules` out of a repair
  context structurally rather than by filtering afterwards.
- **Widener** [`app/agents/suite_context.py`](../backend/app/agents/suite_context.py) —
  `companion_sources` split into `same_stem_sources` + `directory_sources` so the graph slots
  between them; the candidate order per failing test is now **same-stem → direct imports →
  directory neighbours → transitive imports**. A `_Widening` session object owns the workspace
  listing, the read cache and the per-test contribution map (kept off the analyzer so two
  concurrent analyses never share state), and `_interleave` round-robins the per-test lists so one
  failure with many candidates cannot spend the whole char budget before another failure's obvious
  target is considered.
- **Per-test coverage** — `uncovered(context, contributed=None)` now accepts what earlier tiers
  offered *for each test*, and `analyze` re-evaluates after every tier. The changed-since-passing
  and suite-source tiers fire for a **still-uncovered test**, not only when the run as a whole has
  nothing editable — the aggravator that suppressed the tier holding the answer in the report.
- **Escape hatch** [`app/agents/repair.py`](../backend/app/agents/repair.py) — `request_file` is
  built per attempt as a closure and appended to that attempt's `ToolRegistry`, so schema
  validation, the `Run` tool-call trace and the JSON error envelope all come from the existing
  machinery. It grants read **and** write, returns the contents in the same result, refuses test
  files / non-sources / unknown paths / over-budget requests, and **re-persists the context blob**
  when it grants — so the stored trail is what the model actually saw.
- **No-op guard** [`app/orchestrator/stages/repair.py`](../backend/app/orchestrator/stages/repair.py)
  — consecutive attempts with an empty `files_written` are counted; at `repair_max_noop_attempts`
  the loop escalates as `no_patch` carrying the agent's own summary (clipped to 600 chars), checked
  **after** the green and regression guards and **before** the stall guard. `noop_attempts` joins
  `ConvergenceMetrics` for the eval harness.
- **Config** — four registered knobs (`repair_import_max_depth`, `repair_import_max_files`,
  `repair_request_file_max`, `repair_max_noop_attempts`), documented in `.env.example` and
  admin-visible via phase-51. Depth 0 and request-max 0 restore pre-phase-63 behaviour without a
  deploy, and both are pinned by tests.
- **Frontend** — one `REASON_LABELS` entry (`no_patch`) plus an optional `noop_attempts` on
  `RepairMetrics`; the summary body already rendered the detail.
- **Verification** — `ruff` / `black` / `mypy` clean; the full backend suite is green
  (**1696 passed**, 3 environment-gated skips); `pnpm lint` and the frontend suite pass. 35 tests
  are new or extended: `test_import_graph.py` (21), `test_suite_repair_context.py` (+5, including
  the reported `App.test.tsx` → `pages/HomePage.tsx` case and the per-test tier fall-through),
  `test_repair_request_file.py` (5), `test_repair_no_patch.py` (4).
- **Out of scope, as planned** — the test generator's matcher choice, the Build-stage widener, any
  repo-browsing tool, and automatic re-runs after an escalation.
