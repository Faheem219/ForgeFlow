# Phase 08 — Conductor (orchestrator)

## Title & ID

- **ID:** `phase-08`
- **Title:** Conductor (orchestrator)
- **Epic:** Epic 1 — Project & Orchestration Core
- **Milestone:** M1

## Goal

The central router that turns a user **intent** (message + target stage + action) into a call to the right stage handler, owns transition legality, persists messages/artifacts, and emits events.

## Context

The "conductor" of §8/domain glossary — the single place that owns transition legality (not the UI). It defines the `StageHandler` interface every stage implements and the cost-accounting hook every agent call passes through. This phase wires **stub** handlers so the whole loop is exercisable before real stages land.

## Depends on

- **Phase IDs:** `phase-05`, `phase-06`, `phase-07`
- **External:** —

### Prerequisite check

- `phase-06` ✅ (state machine), `phase-07` ✅ (messages/artifacts), `phase-05` ✅ (event hub).

## Detailed tasks

1. **Intent schema (`app/orchestrator/schemas.py`).** `Intent = {project_id, stage, action: refine|proceed|skip|unskip, message?, payload?}` (`unskip` added 2026-08-29 — the conductor answers it itself, restoring the status the `skip` recorded, with no handler dispatch); `StageResult = {messages[], artifacts[], next_status, events[]}`.
2. **StageHandler interface (`app/orchestrator/stages/base.py`).** `class StageHandler(Protocol): async def handle(intent, ctx) -> StageResult`. `ctx` bundles project, stage state, repos, `emit`, agent client (later), sandbox handle (later), config resolver.
3. **Conductor (`app/orchestrator/conductor.py`).**
   - Validate the transition via `state_machine.can_transition` **before** dispatch; reject illegal ones with `UserError`.
   - Persist the inbound user `Message`.
   - Dispatch to the registered handler for `intent.stage`.
   - Persist produced messages/artifacts (`phase-07`), apply the resulting state transition (`phase-06`), emit events (`phase-05`).
   - **Cost-accounting hook:** wrap handler execution in a `Run` (start/finish, token/₹ cost) — real accrual lands with `phase-20`; here record start/stop + zero cost.
4. **Handler registry (`app/orchestrator/stages/__init__.py`).** Map stage → handler; register **no-op stubs** (`design/build/requirements/test/deploy/validate`) that echo intent and produce a trivial artifact, so routing is testable now and real handlers drop in later.
5. **API (`app/orchestrator/router.py`).** `POST /projects/{id}/intent` (body = Intent) → runs the conductor, returns `StageResult` summary; streams detailed progress over the WS.

## Out of scope for this phase

- Real stage logic (design/build/etc. in their epics), real agent calls & cost (`phase-20`). This phase is routing + legality + persistence + events with **stub** handlers.

## Acceptance criteria

- A valid intent routes to the correct stub handler; illegal transitions are rejected before dispatch with an actionable `UserError`.
- Inbound + outbound messages persisted; produced artifacts versioned; state transition applied; events emitted.
- Every conductor invocation opens/closes a `Run` (cost hook present, zero for stubs).

## Tests to write

- `backend/tests/orchestrator/test_conductor_routing.py` — intent → correct handler; unknown stage rejected.
- `backend/tests/orchestrator/test_conductor_legality.py` — illegal transition (e.g., `deploy` before `build`) rejected pre-dispatch.
- `backend/tests/orchestrator/test_conductor_persistence.py` — messages/artifacts/state/events all written for a happy-path intent.

## Manual verification steps

1. `POST /projects/{id}/intent {stage:"build", action:"proceed", message:"go"}` → stub build handler responds; WS shows events; `GET /projects/{id}/messages` shows the round.
2. `POST … {stage:"deploy", action:"enter"}` on a fresh project → 4xx `UserError` (build required).

## Rollback / cleanup notes

- Stub handlers are replaced in later phases (no data migration). Reversible.

## Design notes

- Handlers are **Protocol**-typed and registered in one place so real implementations replace stubs with zero conductor changes — keeps stages independently shippable (D-invariant: phases ship independently).

## Status

✅ done

## Follow-up fix — a failed request left no trace, and dead runs stayed "running"

Reported as *"closing my app deleted my design"*. It hadn't: the design artifact and its payload were
intact the whole time. Two separate gaps made lost work the obvious reading, and both are now fixed.

1. **A failed intent answered nothing.** The user's message is persisted *before* the handler runs
   (correctly — the request happened), but on failure only an HTTP error came back. That error lived
   in a toast; after a reload the conversation showed three refine requests in a row with no reply of
   any kind, as though ForgeFlow had ignored them. `Conductor._note_failure` now appends an assistant
   message with the taxonomy's user-facing text plus any `fallback_hint`, so the transcript is a
   truthful record of what happened. An *unexpected* exception gets a summary, never its text — the
   conversation must not become a place where internals (or anything they carry) surface.

2. **`reap_orphaned_runs` only closed build runs.** Every other kind — `conductor:design` here —
   stayed `finished_at=None` forever when its process died, so the cost panel listed work as
   *running* days later. It now closes **every** run left open by a previous process (the
   build-specific reasoning about the concurrency lock still holds, it was just never the only case).
   Four such runs were reclaimed from the live DB.

Verified: 6 new tests in `tests/orchestrator/test_failed_intent_record.py` (provider/user/unexpected
failures each recorded, no secret leakage, a successful intent stays clean, and non-build runs are
reaped) and the phantom runs cleared on the real database.
