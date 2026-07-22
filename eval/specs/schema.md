# Benchmark spec format

A **spec** is one reproducible benchmark for the evaluation harness (D13): what a user would hand
ForgeFlow, the structured requirements that should come out of it, and the features a correct build
must contain. The runner ([phase-44](../../plans/phase-44-headless-pipeline-runner-metrics.md))
drives the whole pipeline from each spec and measures first-pass vs post-repair pass rate, repair
iterations, cost, and screenshot→URL time.

Specs live in this directory as `<id>.yaml` (`.yml` and `.json` also load). They are validated by
[`app/eval/specs_loader.py`](../../backend/app/eval/specs_loader.py).

## One schema, not two

A spec's `requirements` block **is** the request body the requirements API accepts
(`RequirementSpecInput`, [phase-25](../../plans/phase-25-requirements-schema-api.md)), and the loader
validates it with the product's own `validate_features`. A seed the loader accepts is therefore one
the product would accept — the eval schema and the product schema cannot drift apart.

## Fields

| Field | Required | Meaning |
|---|---|---|
| `id` | ✅ | Stable slug. **Must equal the filename stem** — the file is the record. |
| `title` | ✅ | Human-readable name, used in reports. |
| `difficulty` | | `simple` \| `moderate` \| `complex` (default `simple`). See below. |
| `inputs.prompt` | ◐ | What the user would type. |
| `inputs.screenshots` | ◐ | Paths relative to this directory, conventionally `assets/…`. Must exist. |
| `requirements.features[]` | ✅ | `name`, `description`, `inputs[]`, `expected_behaviors[]`, `acceptance_criteria[]`. |
| `…acceptance_criteria[].text` | ✅ | One testable statement. |
| `…acceptance_criteria[].kind` | | `unit` \| `e2e` \| `either` (default `either`) — a hint to test-gen. |
| `expected_features[]` | | Feature names a correct build must implement. Each **must exist** in `requirements`. |
| `tags[]` | | Free-form grouping for reports. |
| `notes` | | Why this spec is in the benchmark and what it is meant to catch. |

◐ At least one of `inputs.prompt` / `inputs.screenshots` is required.

Criterion **ids are not authored** — they are minted at load time by the same code that mints them
in the product, because ids are only stable within a project's own spec history.

## Graded difficulty

Difficulty is not decoration: it is how the eval shows *where* the repair loop earns its keep.

| Difficulty | Shape | Expectation |
|---|---|---|
| `simple` | One entity, plain CRUD | First-pass generation should pass outright. A failure points at the skeleton or codegen, not the task. |
| `moderate` | A few entities, filtering and derived values | First-pass is often *nearly* right — the discriminating criterion is usually a boundary or a combinator. |
| `complex` | Several related entities with cross-cutting rules | First-pass is expected to fall short; this is where repair has to do real work. |

The seeds are chosen so each one has at least one criterion that a plausible-but-wrong
implementation fails — "all of these tags" rather than "any of them", touching time ranges that do
not overlap, scaling that must not persist. Benchmarks made only of criteria that any build passes
measure nothing.

## Adding a spec

1. Write `eval/specs/<id>.yaml` with `id: <id>`.
2. Put any screenshots in `assets/`.
3. Run `uv run python -m app.eval.specs_loader --list` from `backend/` — it validates every spec and
   prints the index. A malformed spec fails the whole load with the file and the reason, rather than
   being skipped: a benchmark that silently shrinks flatters its own results.

## Current seeds

| ID | Difficulty | What it is meant to catch |
|---|---|---|
| `hero-todo` | moderate | The **demo hero** (phase-50): a relatable todo app whose "Clear completed" bulk action first-pass tends to get wrong — the scripted repair moment. See [`docs/demo-runbook.md`](../../docs/demo-runbook.md). |
| `todo-list` | simple | The floor — establishes that the pipeline works at all. |
| `notes-crud` | simple | A query path that must agree between API and UI. |
| `url-shortener` | simple | A non-CRUD route (redirect) and a uniqueness invariant. |
| `expense-tracker` | moderate | Derived totals and a date-window boundary. |
| `bookmark-manager` | moderate | Conjunctive filtering (all tags, not any). |
| `recipe-box` | moderate | Nested documents; a computation that must not persist. |
| `kanban-board` | moderate | Screenshot-driven design; contiguous ordering across moves. |
| `issue-tracker` | complex | A status workflow, edit-locking, and cascade deletes. |
| `event-scheduler` | complex | Overlap detection with a touching-boundary case; capacity limits. |
