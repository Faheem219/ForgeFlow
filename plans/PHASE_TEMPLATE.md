# Phase NN — <Short Title>

> **Reusable template.** Copy this file to `plans/phase-NN-<slug>.md` and fill every section.
> Keep phases **independently shippable**: each must build, pass its own tests, and leave the app runnable.
> Faithfulness check before writing code: re-read the locked decisions (D1–D14) in `IMPLEMENTATION_PLAN.md` §1. If a task forces a deviation, **stop and ask**.

---

## Title & ID

- **ID:** `phase-NN`
- **Title:** <human title>
- **Epic:** <Epic N — name>
- **Milestone:** <M1–M5>

## Goal

One or two sentences: the single outcome this phase delivers. If you cannot state it in two sentences, the phase is too big.

## Context

Why this phase exists now, how it fits the pipeline, and which locked decisions (D1–D14) / invariants it must honor. Reference the relevant `IMPLEMENTATION_PLAN.md` sections.

## Depends on

- **Phase IDs:** `phase-XX`, `phase-YY` (must be ✅ before starting).
- **External:** any provider/account/tool that must exist (e.g., Anthropic key, Atlas cluster, Colima running).

### Prerequisite check

A concrete checklist to run *before* coding (commands to run, files/collections that must exist, services that must boot). If any item fails, stop and report.

## Detailed tasks

Ordered, file-path-level steps. Include, as relevant:

- **Data models** — Beanie/Pydantic docs or Mongoose schemas, field-by-field (name, type, index).
- **API contracts** — method + path + request/response schema + error envelope; note authz.
- **Event schemas** — realtime event `event`, `payload` shape, `seq` semantics.
- **UI components** — component tree, props, state store slices, routes.
- **Agent tools / prompts** — tool JSON schemas, model routing (`MODEL_CODEGEN` vs `MODEL_ROUTING`), token accounting.
- **Config** — new settings resolved through the layered config (`admin panel > env > default`, see `phase-51`); new keys added to `.env.example`.

Number the steps so execution order is unambiguous. Name exact files under the `IMPLEMENTATION_PLAN.md` §5 structure.

## Out of scope for this phase

Explicit list of things a reader might expect here but that belong to another phase (with the phase ID). Prevents scope creep during execution.

## Acceptance criteria

Concrete, testable statements. Each must be verifiable by a test or a manual step below. Cover the happy path **and** the non-linear/failure guarantees this phase touches (skip/stale, bounded loop, secret non-leakage, sandbox isolation, cost cap — whichever apply).

## Tests to write

- **Control plane (`pytest`):** unit/integration tests; mock Anthropic/Stitch/Vercel/Render; disposable test Mongo DB.
- **Frontend (Vitest + Testing Library):** component/store tests.
- **Generated-app fixtures (Vitest/Jest/Playwright):** only where this phase produces or runs generated-app code.
- Name test files and the key cases each must assert.

## Manual verification steps

Copy-pasteable commands and expected results (`make dev`, `curl`, UI click-path, WS trace). What a human does to believe it works.

## Rollback / cleanup notes

How to safely revert: migrations to drop, containers/volumes to reap, feature flags to disable, artifacts/collections to remove. Note anything that is **not** reversible.

## Design notes

(Only where the plan is thin.) Reasonable, plan-consistent decisions made here and their rationale, so a reviewer can challenge them cheaply before code flows.

## Status

⬜ todo
