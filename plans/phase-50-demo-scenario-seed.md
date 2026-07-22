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

✅ done

**Implementation notes**

- **Hero scenario** — [eval/specs/hero-todo.yaml](../eval/specs/hero-todo.yaml): a relatable todo app (validated by the product's own loader) engineered with a discriminating criterion — the **"Clear completed"** bulk action, which first-pass generation tends to get subtly wrong (clears the whole list, or only hides completed rows). Those two criteria are the scripted repair moment. Prompt-driven; the live demo drags in screenshots at the design stage (documented, not fabricated as a fake asset).
- **Smoke test** — [tests/demo/test_hero_smoke.py](../backend/tests/demo/test_hero_smoke.py): drives the **real** hero spec through the **real** `EvalRunner`/conductor with only the costly legs (model/sandbox/live deploy) injected as fakes, and asserts every milestone — skeleton instantiated, genuine first-pass failure (2 of 7 criteria), repair fixes it (`outcome=="fixed"`, post-repair green, positive delta), deploy healthy with a live URL + screenshot→URL time, live validation ran, outcome `delivered`, resources torn down. Plus a free-legs-only variant (the CI-cheap path still shows the contribution) and a clean-first-pass sanity check (`not_needed`, not a fake win).
- **Seed & reset** — [scripts/demo.py](../backend/scripts/demo.py) (`seed`/`reset`/`status`): pre-creates the demo account + a "Hero Demo" project with the hero requirements captured and marked complete, so a run starts at design/build; `reset` cascades each project's data and best-effort reaps its sandbox + app DB. The DB cores (`seed_demo`/`reset_demo`) are split from connection lifecycle so they're tested against a disposable DB ([test_demo_seed.py](../backend/tests/demo/test_demo_seed.py), 6 tests). Refuses to seed without an explicit `DEMO_PASSWORD` (§7). Makefile: `demo-seed`/`demo-reset`/`demo-status`/`demo-rehearse`.
- **Runbook** — [docs/demo-runbook.md](../docs/demo-runbook.md): the click-path with timings, the exact line to say at the repair moment, a T-30 checklist, and a **fallback table** for every risky stage (Stitch quota → auto Figma/fake; model hiccup → bounded retry/escalation *shown as a feature*; live deploy flaky → pre-warmed URL; sandbox → volume recovery; all-infra-down → narrate the headless rehearsal).
- Config keys `demo_email`/`demo_password` added to `Settings` + `.env.example` (password ships blank); hero seed listed in the specs `schema.md`.

**Verification:** backend 832 passed (11 new demo tests green), ruff/black/mypy clean, the hero spec validates through `specs_loader`. The 7 remaining failures are the pre-existing Docker-daemon repair tests (identical at HEAD). No frontend changes — the demo drives the existing UI.

**Deferred (need live infra/an audience):** the three manual walkthroughs (clean-state runbook to a live URL, triggering each fallback, and a full live rehearsal) — the headless smoke covers the pipeline *logic*; the checklist covers the live legs it deliberately mocks.
