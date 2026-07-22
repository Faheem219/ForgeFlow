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

✅ done

**Audit — what earlier phases already made resilient**

Timeouts + cancellation exist per op (exec `asyncio.timeout` + kill, agent-client retries, deploy/design HTTP timeouts, bounded repair loop); container-crash recovery is inherent in `SandboxManager.ensure` (workspace on a named volume that outlives the container); provider errors are already classified `ProviderError` + `fallback_hint`; resumability is the realtime hub's seq + replay ring. This phase verifies those together and closes the one real gap.

**Gap closed — automatic design-provider fallback** (the headline). Previously `get_active(project)` picked one provider and a Stitch quota error dead-ended with a hint the user had to act on manually. New [resilient.py](../backend/app/design/resilient.py): a **generate** call tries the active provider, then a configured chain (`figma → fake`), capability-filtered (Figma can't do screenshots) and deduped, and reports which provider actually served so the stage tells the user ("stitch quota is exhausted — generated via figma instead"). Boundaries: `refine` stays single-provider (its `external_ref` is provider-specific); a `fatal` (bad-input) error never falls back. Wired into the design **stage handler** (conductor path) and `DesignService`; config `design_fallback_chain` (default `figma,fake`, `""` disables).

**User-facing error UX** (frontend):
- [errors.ts](../frontend/src/lib/errors.ts) — the taxonomy → actionable-message mapper (`toActionableError`): title / description / hint / retryable per envelope `type`, with a provider's `fallback_hint` winning over the default, network `TypeError`s recognised, and a `toastError()` one-liner panels use in `onError` for consistent toasts. Wired into `DesignPanel` as the reference integration.
- [ErrorBoundary.tsx](../frontend/src/components/ErrorBoundary.tsx) — a global React boundary (branded, recoverable "Try again"/"Reload", `resetKey` clears a crash on navigation) mounted in `providers.tsx`, plus a per-stage boundary in `Workspace` so one panel's crash stays contained.

**Fault-injection harness** (`tests/resilience/conftest.py`, test-only per the design note): scripted design providers that fail on a chosen error kind, and an in-memory hanging exec runtime (no pty/killpg → cross-platform).

**Tests** — backend `tests/resilience/` 29 (provider fallback incl. end-to-end via the conductor; container recovery from volume; timeout + cancel + concurrency cap; reconnect-resume from seq); frontend `errors.test.ts` (11) + `ErrorBoundary.test.tsx` (6). One phase-19 test updated: `test_provider_error_propagates_with_a_fallback_hint` now disables the chain so it still guards the dead-end contract (a failure surfaces its hint) under the new fallback model.

**Verification:** backend 821 passed, ruff/black/mypy clean; frontend 236 passed (my 17 new all green), tsc/eslint/prettier clean, `vite build` succeeds. Remaining reds are pre-existing and unrelated: 7 Docker-daemon repair tests (identical at HEAD), 26 Windows-only sandbox tests, and one phase-45 chart test that assumes US number grouping (fails only under this machine's `en-IN` locale). Deferred (need a real daemon/browser): the live Stitch-429 / container-kill / reconnect manual walkthroughs.
