# Phase 47 — Security & sandbox hardening pass

## Title & ID

- **ID:** `phase-47`
- **Title:** Security & sandbox hardening pass
- **Epic:** Epic 11 — Hardening, PWA polish & Demo
- **Milestone:** M5

## Goal

Close escape/abuse vectors: review sandbox isolation, secret handling, authz on every route, rate limits, and input validation; produce a threat-model doc and a passing hardening checklist.

## Context

Directly enforces the §7 security posture and risk §12 ("sandbox security"). It audits the invariants asserted piecemeal in earlier phases (sandbox isolation `phase-11`, preview networking `phase-15`, secrets `phase-34`, data isolation `phase-41`) as a coherent whole.

## Depends on

- **Phase IDs:** `phase-11`, `phase-34`
- **External:** —

### Prerequisite check

- Sandbox (`phase-11`), preview (`phase-15`), vault (`phase-34`), data browser (`phase-41`) implemented.

## Detailed tasks

1. **Sandbox isolation audit.** Verify at runtime: no host network (except the restricted preview net), `cap_drop=ALL`, `no-new-privileges`, read-only root + writable `/workspace` + `tmpfs`, cpu/mem/**pids** caps, exec timeouts, idle reaping. Add missing controls; document each.
2. **Secret handling audit.** Confirm encryption at rest (Fernet), decrypt-only-at-use, redaction in logs/responses; no secret ever reaches the client (`phase-34`).
3. **AuthZ sweep.** Assert **every** route enforces ownership/role (projects, sandbox, fs, exec, data, deploy, admin); add tests for cross-user/cross-project access → rejected.
4. **Rate limits + input validation.** Limits on auth + expensive endpoints; validate/sanitize all inputs (path safety, query guardrails, payload sizes).
5. **Threat model (`docs/threat-model.md`).** Assets, actors, trust boundaries (control plane vs sandbox vs providers), attack surfaces, mitigations, residual risks. Plus a **hardening checklist** to run before demo/release.
6. **CI security checks.** Dependency/secret scanning; a test suite asserting the checklist's automatable items.

## Out of scope for this phase

- New features. Resilience/failure behavior (`phase-48`, complementary).

## Acceptance criteria

- The hardening **checklist passes**; the threat-model doc exists and is current.
- **No secret leakage** anywhere (logs, responses, errors) — asserted by tests.
- **No cross-project / cross-user access** possible (asserted by tests across sandbox, data, deploy, artifacts).
- Sandbox isolation controls verified present via `docker inspect` + tests.

## Tests to write

- `backend/tests/security/test_authz_matrix.py` — cross-user/cross-project access on every protected route → rejected.
- `backend/tests/security/test_sandbox_isolation.py` — inspect flags: net/caps/limits/no-new-privileges.
- `backend/tests/security/test_secret_nonleak.py` — logs/responses/errors free of secrets.
- `backend/tests/security/test_rate_limits.py` — limits enforced.

## Manual verification steps

1. Run the checklist end-to-end; `docker inspect` a sandbox → all isolation flags present.
2. Attempt cross-project data/artifact/deploy access with a valid token for another project → all rejected.
3. Grep logs after a full run → no secrets.

## Rollback / cleanup notes

- Hardening changes are protective; if a control breaks a flow, gate it via config and file a follow-up rather than removing it silently. Reversible.

## Design notes

- This phase is an **audit + gap-closing** pass, not a rewrite — earlier phases assert their own guarantees; here they're verified together and any gaps closed, with the threat model as the durable artifact.

## Status

⬜ todo
