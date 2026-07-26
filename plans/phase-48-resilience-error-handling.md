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

## Follow-up fix — a refine after a fallback went to the wrong provider

This phase's own boundary note said refine "stays single-provider" because an ``external_ref`` is
provider-specific. Correct — but the code then sent every refine to the **active** provider rather
than the one that *issued* the ref. So the fallback this phase introduced set up its own failure:

1. Generate with Stitch active → Stitch fails (quota/auth/down) → the chain serves `fake`, and the
   design artifact stores `external_ref='fake-text-…'` with `provider='fake'`.
2. The user refines → the call goes to the still-active **Stitch** with a `fake` ref → Google answers
   `Requested entity was not found`, surfaced in the UI as *"Could not send"*. Dead end: refine did
   not go through `invoke_with_fallback` either, so nothing recovered it.

`provider_for_refine(project, produced_by)` now resolves the refine target from the artifact's
recorded `META_PROVIDER`, and both call sites use it (`DesignService.refine` and the design stage
handler). When the producer is not the active provider the user is told, in the same
`_summary(...)` line the generate paths use for fallback notes: *"Refined via fake because it
produced this design (the active provider is stitch). Generate a new design to move this project
onto stitch."* Unknown/missing provenance still resolves to the active provider (legacy artifacts
stay refinable) with a note rather than an error.

Defence in depth, so this class of mix-up can never again reach a provider as an opaque not-found:

- **`app/design/stitch.py`** — `_split_ref` rejects a ref that is not Stitch's own
  `{projectId}/{screenId}` shape (or a bare hex Stitch id) as `fatal`, before any HTTP call:
  *"Design reference 'fake-text-…' was not produced by Stitch, so Stitch cannot refine it."*
  `fatal` is deliberate — no other provider could refine it either, so the chain must not spend
  attempts. A genuine `NOT_FOUND` from Stitch (project deleted there, or created under different
  credentials) now carries its own hint — "generate a new design; existing versions are kept" —
  instead of the schema-drift hint, which was misleading advice for that failure.
- **`app/design/fake.py`** — a ref this provider issued stays refinable after a control-plane
  restart (its in-memory map is a cache, not the source of truth). A foreign ref is still rejected.
- **`scripts/stitch_probe.py`** — forces UTF-8 stdout; on Windows it used to die with a
  `UnicodeEncodeError` mid-report, on exactly the platform where someone runs it after a failure.

Verified: 26 new tests (`tests/design/test_refine_provenance.py`,
`tests/design/test_stitch_foreign_ref.py`, plus the handler-level regression in
`tests/orchestrator/stages/test_design_handler.py`), and **against the real failing data**: replaying
the recorded refine on the project that failed now succeeds and routes to `fake`, while the five
Stitch-produced designs in the same DB still route to `stitch`. The live Stitch endpoint was probed
read-only (`tools/list`) to confirm the argument names were never the problem: `edit_screens` accepts
exactly what we send.
