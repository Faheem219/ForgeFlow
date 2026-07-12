# Phase 50 — Demo scenario & seed

## Title & ID

- **ID:** `phase-50`
- **Title:** Demo scenario & seed
- **Epic:** Epic 11 — Hardening, PWA polish & Demo
- **Milestone:** M5

## Goal

A flawless hero demo — screenshots → deployed URL — with the meaningful middle back-and-forth (design refine, build, tests+repair, deploy, live validation), plus seed data, reliability fallbacks, and a short runbook.

## Context

The demo is the deliverable that proves the whole thesis (D1): the full non-linear, human-in-the-loop pipeline with the bounded self-healing repair loop as the star. `CLAUDE.md` mandates keeping this hero path working as features land; this phase makes it reliable and scripted.

## Depends on

- **Phase IDs:** all prior (01–49) — this is the capstone.
- **External:** Anthropic key; a design provider (or fake fallback); deploy creds (or a rehearsed mocked path).

### Prerequisite check

- The end-to-end pipeline works: design→build→test→repair→deploy→validate; eval harness available; hardening + resilience passes done.

## Detailed tasks

1. **Hero scenario (`eval/specs/hero-*` + `docs/demo-runbook.md`).** Pick one crisp app (e.g., a todo/notes app) with input screenshots + a structured spec designed to: show a design refine, generate on the skeleton, produce tests, **exhibit a first-pass failure that the repair loop fixes** (the money moment), deploy dual-mode, and pass live validation → final URL.
2. **Seed data & state.** Pre-seed an account/project (optional) so the demo starts fast; a reset script to return to a clean pre-demo state.
3. **Reliability fallbacks.** For each risky stage, a rehearsed fallback: Stitch quota → Figma/fake design; live deploy flaky → a pre-warmed deployment or mocked deploy path; model hiccup → retry/escalation shown as a feature, not a failure. Document decision points.
4. **Runbook (`docs/demo-runbook.md`).** Exact click-path + timings + "if X fails, do Y"; a checklist to run 30 min before a demo.
5. **Smoke test.** A scripted rehearsal (headless where possible via `phase-44`) that runs the hero path and asserts each milestone, catching regressions before a live demo.

## Out of scope for this phase

- New product features. Anything not on the hero path (keep it tight).

## Acceptance criteria

- The **end-to-end hero flow runs reliably start to finish**: screenshots → design refine → build (on skeleton) → tests + **repair fixes a real failure** → dual-mode deploy → live validation → **final live URL**.
- Documented fallbacks exist for every risky stage; the reset script returns to a clean state.
- A scripted rehearsal asserts each milestone (regression guard).

## Tests to write

- `backend/tests/demo/test_hero_smoke.py` — headless hero run asserts: skeleton instantiated, first-pass failure then repair-fixed, deploy healthy, live validation green, final URL present (mock the costly/live legs as configured).
- A rehearsal checklist (doc) validated in CI where feasible.

## Manual verification steps

1. From a clean state, follow the runbook → reach a live URL with the repair moment visible.
2. Trigger each fallback (e.g., disable Stitch) → demo still completes via the documented path.
3. Run the rehearsal smoke → all milestones asserted.

## Rollback / cleanup notes

- Reset script tears down demo projects/sandboxes/DBs/deployments. Clean live provider resources after rehearsals. Reversible.

## Design notes

- The scenario is deliberately engineered so the **repair loop visibly earns its keep** (a real first-pass failure it fixes) — that moment is the demo's thesis (D1), so it's scripted, not left to chance.

## Status

⬜ todo
