# Phase 11 — Sandbox manager

## Title & ID

- **ID:** `phase-11`
- **Title:** Sandbox manager
- **Epic:** Epic 2 — Sandbox & Web IDE
- **Milestone:** M1

## Goal

Per-project container lifecycle (create/start/stop/destroy) via the Docker SDK, with isolated workspace volumes, resource caps, no host network, timeouts, and idle reaping.

## Context

Implements D5 + §7 security posture at runtime. This is where the sandbox isolation guarantees are actually enforced. It maps `Project ↔ container` and is the substrate for FS/exec/preview and all agent tools.

## Depends on

- **Phase IDs:** `phase-06`, `phase-10`
- **External:** Docker (Colima); `SANDBOX_IMAGE`, `SANDBOX_CPU_LIMIT`, `SANDBOX_MEM_LIMIT`, `SANDBOX_IDLE_TIMEOUT_S` resolved via config.

### Prerequisite check

- `phase-10` ✅ (image builds); `phase-06` ✅ (projects exist); Docker reachable from the API container (mounted socket or remote).

## Detailed tasks

1. **Manager (`app/sandbox/manager.py`).** Docker SDK wrapper:
   - `ensure(project) -> Sandbox` (create if absent; start if stopped), storing `container_id` on `Project.sandbox_id`.
   - **Isolation flags:** `network_mode="none"` by default (preview networking handled explicitly in `phase-15`), `cap_drop=["ALL"]`, `security_opt=["no-new-privileges"]`, read-only root FS where feasible with a writable `/workspace`, `pids_limit`, `mem_limit`, `nano_cpus`/`cpu_quota`, `tmpfs` for `/tmp`.
   - **Per-project workspace volume** mounted at `/workspace` (named volume keyed by project id) for persistence across restarts.
   - `stop(project)`, `destroy(project)` (remove container + optionally volume), `status(project)`.
2. **Idle reaping.** Background task stops containers idle beyond `SANDBOX_IDLE_TIMEOUT_S`; tracks last-activity via exec/FS/preview touches; emits `sandbox.status` events.
3. **Timeouts + limits.** All docker calls time-bounded; creation failures surfaced as `SystemError`; caps validated against config.
4. **Mapping + registry.** `project_id → container` map persisted (`Project.sandbox_id`) + reconciled on startup (adopt existing, mark missing).
5. **API (`app/sandbox/router.py`).** `POST /projects/{id}/sandbox/(ensure|stop|destroy)`, `GET …/status`. Ownership-checked.

## Out of scope for this phase

- FS API (`phase-12`), exec/terminal (`phase-13`), preview proxy/networking (`phase-15`). Only lifecycle + isolation + reaping here.

## Acceptance criteria

- Each project gets its **own** container + workspace volume; two projects never share state.
- Isolation enforced: no host network (default), caps dropped, cpu/mem/pids limits applied (verifiable via `docker inspect`).
- Idle containers are reaped after the configured timeout; `ensure` restarts them on demand.
- `destroy` removes the container (and volume when requested); startup reconciliation adopts/repairs mappings.

## Tests to write

- `backend/tests/sandbox/test_manager_lifecycle.py` — ensure/start/stop/destroy; `sandbox_id` persisted (Docker mocked + one opt-in integration test behind a marker).
- `backend/tests/sandbox/test_isolation_flags.py` — created container spec has `network_mode=none`, `cap_drop=ALL`, limits set.
- `backend/tests/sandbox/test_idle_reaper.py` — idle → stopped; activity resets the timer.

## Manual verification steps

1. `POST /projects/{id}/sandbox/ensure` → `docker ps` shows the container; `docker inspect` shows caps dropped + limits + no network.
2. Leave idle past timeout → container stops; `ensure` again → restarts, workspace intact.
3. Two projects → two distinct containers/volumes.

## Rollback / cleanup notes

- `destroy` per project; a `make sandbox-reap-all` to remove all forgeflow containers/volumes. **Destroying a volume deletes workspace code** — irreversible; require explicit confirmation.

## Design notes

- Default `network_mode=none` maximizes isolation; `phase-15` introduces a **dedicated, restricted** network only for preview/BE↔FE, never the host network.
- Named volumes (not bind mounts) keep host paths out of the untrusted container.

## Status

⬜ todo
