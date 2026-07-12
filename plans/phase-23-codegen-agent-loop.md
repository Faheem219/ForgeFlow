# Phase 23 — Codegen agent loop

## Title & ID

- **ID:** `phase-23`
- **Title:** Codegen agent loop
- **Epic:** Epic 4 — Build Stage (agentic codegen)
- **Milestone:** M2

## Goal

Generate a working full-stack app from design + requirements **onto the prebuilt skeleton**, streaming code to the IDE and verifying it boots, with a structured build report.

## Context

The heart of the Build stage (D4). Non-linearity (D12): must work with any subset of upstream artifacts (design and/or requirements may be missing/stale). Cost discipline: starts by **copying** the skeleton (`phase-22`) — the LLM never rescaffolds the frontend — then writes only feature code.

## Depends on

- **Phase IDs:** `phase-15`, `phase-19`, `phase-21`, `phase-22`
- **External:** `ANTHROPIC_API_KEY`; active model routing.

### Prerequisite check

- `phase-21` ✅ (tools incl. `instantiate_skeleton`), `phase-22` ✅ (skeleton), `phase-15` ✅ (preview to verify boot). Design/requirements artifacts optional (tolerate absence).

## Detailed tasks

1. **Codegen agent (`app/agents/codegen.py`).** Orchestrate a Sonnet tool-use loop (`phase-20/21`):
   - **Step 0 — instantiate skeleton** (deterministic copy via `instantiate_skeleton`; **never** generate the FE/BE scaffold). Commit "skeleton".
   - **Step 1 — plan** (Haiku-routed summary): read available `design` + `RequirementSpec` artifacts (tolerate missing/stale); produce a short feature/build plan.
   - **Step 2 — implement** (Sonnet): write BE feature code (routes/models/controllers with Zod), FE feature code (pages/components wired via the skeleton's API client), map the approved design's HTML/CSS into React+Tailwind components. Stream every write as `fs.write` (live IDE view, `phase-14`).
   - **Step 3 — verify boot:** `install_deps` → `start_preview` → health-check FE+BE; fix trivial boot errors inline (bounded).
   - **Step 4 — build report:** structured artifact `{features_built, files_changed, boot_status, follow_ups}`.
2. **Context discipline.** Feed the model only what it needs (design/requirements summaries + skeleton conventions), not the whole repo — mirrors the repair loop's minimal-context ethos and controls cost.
3. **Idempotency.** Re-running Build on an existing workspace applies incremental changes (new artifact version), never a blind rescaffold.
4. **Cost/routing.** Plan/summaries → `MODEL_ROUTING`; implementation → `MODEL_CODEGEN`; token/₹ on the `Run`; respect budget caps (halts cleanly).

## Out of scope for this phase

- The Build stage **handler/UI** wrapper (`phase-24`), test generation (`phase-27`), the repair loop (`Epic 6`), deployment (`Epic 7`).

## Acceptance criteria

- For a sample spec, the generated app **builds, boots, and serves a working feature**; the FE calls the BE; code streams live to the IDE.
- Build **starts from the copied skeleton** — no frontend scaffolding tokens spent (verifiable in the trace: first action is `instantiate_skeleton`).
- Works with design-only, requirements-only, and both; missing/stale upstream artifacts are tolerated with a clear note in the build report.
- Token/₹ recorded; budget cap halts cleanly.

## Tests to write

- `backend/tests/agents/test_codegen_smoke.py` — against a **mock** model script, the loop instantiates the skeleton, writes feature files, boots preview, emits a build report (sandbox mocked/integration behind marker).
- `backend/tests/agents/test_codegen_nonlinear.py` — runs with only design / only requirements / neither; tolerates gracefully.
- `backend/tests/agents/test_codegen_skeleton_first.py` — first tool action is `instantiate_skeleton`; no FE scaffold files are model-generated.

## Manual verification steps

1. With a sample design+spec, run Build → watch files stream into the IDE → preview shows a working feature; BE reachable.
2. Inspect the `Run` trace → first action is skeleton instantiation; feature files follow.
3. Re-run Build with a change → incremental new version, not a rescaffold.

## Rollback / cleanup notes

- Revert to the "skeleton" git commit in the workspace to discard generated feature code; artifacts are versioned (older build report retained). Reversible.

## Design notes

- **Skeleton-first (user directive)** is enforced in the agent loop, the tool trace, and the tests — the frontend template is *always* the starting point, per the user's cost requirement.
- Mapping design HTML/CSS → Tailwind React components (rather than raw HTML) keeps the generated FE idiomatic and consistent with the skeleton.

## Status

⬜ todo
