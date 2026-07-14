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

1. **Intent schema (`app/orchestrator/schemas.py`).** `Intent = {project_id, stage, action: refine|proceed|skip, message?, payload?}`; `StageResult = {messages[], artifacts[], next_status, events[]}`.
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
