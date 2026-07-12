# Phase 48 — Resilience & error handling

## Title & ID

- **ID:** `phase-48`
- **Title:** Resilience & error handling
- **Epic:** Epic 11 — Hardening, PWA polish & Demo
- **Milestone:** M5

## Goal

Graceful behavior under failure everywhere: timeouts/cancellation, container-crash recovery, provider-failure fallbacks (esp. Stitch quota), resumable long tasks, and actionable user-facing error UX.

## Context

Implements §7 error taxonomy + resilience and risk §12 (Stitch quota, cost, non-convergence). Failing soft with clear messaging (per `CLAUDE.md` Do/Don't) is a product requirement, not a nicety.

## Depends on

- **Phase IDs:** `phase-08`, `phase-20`, `phase-35`
- **External:** —

### Prerequisite check

- Conductor (`phase-08`), agent client (`phase-20`), deploy adapters (`phase-35`), realtime resume (`phase-05`) in place.

## Detailed tasks

1. **Timeouts + cancellation.** Ensure every long op (agent calls, exec, tests, deploy, design) is time-bounded and cancellable; cancellation propagates and frees resources (sandbox execs, provider calls).
2. **Container-crash recovery.** Detect sandbox death; `ensure` re-creates from the workspace volume; in-flight stage recovers or re-queues; user notified.
3. **Provider fallbacks.** Standardize `ProviderError` handling with `fallback_hint`: Stitch quota/auth → Figma/fake + clear message (`phase-17/18`); deploy provider transient failure → retry/backoff + partial-failure report (`phase-35/37`).
4. **Resumable long tasks.** Confirm the §7 resumability invariant: tasks emit incremental events and can resume from `seq` (`phase-05`); reconnect mid-build/mid-repair/mid-deploy loses nothing.
5. **User-facing error UX.** Map the error taxonomy (`UserError`/`SystemError`/`ProviderError`) to actionable UI messages (what happened, what to do, retry/fallback affordance); a global error boundary + toast integration.
6. **Fault injection tests.** A harness to inject failures (provider 429, container kill, timeout) and assert graceful degradation.

## Out of scope for this phase

- New features. Security audit (`phase-47`, complementary).

## Acceptance criteria

- Injected failures (provider quota/error, container crash, timeout) **degrade gracefully** with clear, actionable messaging — no crashes, no unbounded retries.
- Long tasks are cancellable and **resumable** (reconnect mid-task loses nothing).
- Stitch quota exhaustion falls back cleanly (Figma/fake) with a user message.

## Tests to write

- `backend/tests/resilience/test_provider_fallback.py` — Stitch quota → fallback + message.
- `backend/tests/resilience/test_container_recovery.py` — killed sandbox → recovered from volume.
- `backend/tests/resilience/test_cancellation.py` — long op cancels + frees resources.
- `backend/tests/resilience/test_resume.py` — reconnect mid-task resumes from `seq`.
- `frontend/.../ErrorBoundary.test.tsx` — taxonomy → actionable messages.

## Manual verification steps

1. Force a Stitch 429 → UI shows a clear message + offers Figma/fallback.
2. Kill a project's container mid-build → system recovers from the volume; user notified.
3. Disconnect during a repair loop → reconnect → loop state intact.

## Rollback / cleanup notes

- Resilience is additive; feature-flag risky recovery paths via config if needed. Reversible.

## Design notes

- Fault injection is a **test harness** (config/env-gated), never a production path — it exercises the real error paths without weakening them.

## Status

⬜ todo
