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

✅ done — `app/sandbox/manager.py` `SandboxManager`: `ensure` (create-if-absent / start-if-stopped,
persists `Project.sandbox_id`, idempotent when running), `stop`, `destroy(remove_volume=False)`,
`status`, plus startup `reconcile` (adopt managed containers by label / clear stale mappings) and an
idle `reaper`. Isolation posture lives in a pure `build_run_kwargs` — `network_mode=none`,
`cap_drop=[ALL]`, `security_opt=[no-new-privileges]`, read-only root (config-gated) with a per-project
named volume at `/workspace` + `/tmp` tmpfs, `pids_limit`/`mem_limit`/`nano_cpus` from config. Docker
SDK calls are off-loaded via `asyncio.to_thread`; the client is injectable (tests use an in-memory
Docker double). Containers/volumes carry `forgeflow.managed`/`forgeflow.project` labels for
reconciliation + reaping. `app/sandbox/router.py`: `GET status` + `POST ensure|stop|destroy`
(ownership-checked via `ProjectService.get_owned`). Lifecycle emits `sandbox.status` realtime events
(new `EventType`). Wired into `create_app()` (router) and the lifespan (best-effort `reconcile` +
cancellable `run_reaper`). New config keys `sandbox_read_only_root` (default true) +
`sandbox_reap_interval_s` (60) with `.env.example` entries; `make sandbox-reap-all` (guarded) added.

Verified: `ruff`/`black`/`mypy --strict` clean; **backend 168 passed** (19 new — isolation-flags,
idle-reaper incl. activity-reset + no-op-when-empty, full lifecycle create→start→stop→restart→destroy
with `sandbox_id` persistence, two-projects-never-share-state, reconcile adopt/clear, and the router
happy-path + auth/ownership guards; Docker faked, DB on ephemeral Mongo). **Live boot with Docker
unreachable** (the current dev box): `/health` 200, `reconcile` fails **soft** (INFO log, no crash),
reaper starts + shuts down cleanly, `ensure` unauth → 401 envelope.

**Deferred (scoped):** an opt-in real-daemon integration test (behind a marker) is a no-op without
Colima here — the fake-client lifecycle + isolation-spec assertions cover the acceptance criteria; FS
API (phase-12), exec/terminal (phase-13), and preview networking (phase-15) build on this container.

## Follow-up fix — a writable `$HOME`, an attachable network, and refreshing outdated sandboxes

Three gaps surfaced when the build stage first ran real commands in a sandbox:

1. **`$HOME` was unwritable.** The read-only root left pnpm/npm/corepack with nowhere to write their
   caches or the pnpm store (see the phase-10 follow-up for the resulting corepack `ENOENT`).
   `build_run_kwargs` now mounts a second per-project named volume, `forgeflow-home-<id>`, at
   `/home/app` (`SANDBOX_HOME`). A named volume rather than a tmpfs, deliberately: the pnpm store
   must survive a container restart and must not be charged against the container's memory limit. The
   read-only root, dropped caps, no-network default and limits are all unchanged, and the volume is
   still a named volume — no host path is ever bind-mounted. `destroy(remove_volume=True)` removes
   both volumes; `make sandbox-reap-all` already matches them by label.
2. **`network_mode=none` made every documented network relaxation impossible.** Docker refuses to
   attach a network to a container in `none` mode:

   ```
   container cannot be connected to multiple networks with one of the networks in private (none) mode
   ```

   So `attach_preview_network` (phase-15) always failed — swallowed as a debug log, leaving the proxy
   unable to reach the dev servers — and the egress window (phase-39, and dependency installs) could
   never work either. A sandbox is now created on **its own `internal` per-project network**
   (`forgeflow-sbnet-<id>`, `build_sandbox_network_kwargs`): the same guarantee as `none` — no route
   to the host, no internet — but attachable, and *stronger* than the shared preview network because
   no other sandbox is ever on it. Verified against a real daemon: sealed by default, egress attaches
   and a real `pnpm install` succeeds, egress detaches back to sealed, and a container on the preview
   network reaches the sandbox's vite server by name (HTTP 200). The network is created with the
   container and removed with it (docker's default address pools are finite — roughly 30 concurrent
   networks unless `default-address-pools` is widened in the daemon config).

   The isolation controls in §7 are unchanged: no *host* network, `cap_drop: ALL`,
   `no-new-privileges`, read-only root, pids/mem/cpu caps, named volumes only. `phase-47`'s audit
   (`tests/security/test_sandbox_isolation.py`) now asserts the default network is per-project **and**
   internal, so this cannot silently widen.

3. **A fixed image never reached existing projects.** `ensure` adopted whatever container existed, so
   a sandbox created from the broken image kept failing forever. It now recreates a container whose
   mounts are missing, whose network mode is not the project's own network (i.e. anything still on
   `none`), or whose image id no longer matches the configured tag (a rebuilt `:latest` keeps its tag
   but changes id). The volumes are untouched, so the generated code and its git history survive; if
   the image can't be resolved the sandbox is left alone rather than churned.

Verified: `ruff`/`black`/`mypy` clean at the pre-existing baseline; new tests in
`tests/sandbox/test_environment_refresh.py` (recreate on missing mount / rebuilt image, adopt when
current, never churn on an unresolvable image, home volume removed with `--remove-volume`) plus the
home-volume assertions in `test_isolation_flags.py` and `tests/security/test_sandbox_isolation.py`.

### Address-pool exhaustion (found by running the suite, not by reasoning)

Per-project networks meet a hard docker limit: the default `default-address-pools` only yield ~30
bridge subnets. The test suite hit it for real — repeated runs leave sandbox containers behind (a
pre-existing leak; each now also pins a subnet), and at 32 networks `networks.create` started failing
with *"all predefined address pools have been fully subnetted"*. Three defences:

1. **Self-heal on exhaustion.** `_ensure_network` prunes networks nothing is attached to and retries
   once; if it still fails the error names the fix (`make sandbox-reap-all`, or widen
   `default-address-pools`) instead of surfacing a bare docker 400.
2. **Reap orphans at startup.** `reconcile` removes managed containers whose `Project` row no longer
   exists — unreachable anyway, yet each was holding a subnet — then prunes orphaned networks. Volumes
   are **never** touched: losing generated code must be a deliberate act, not a startup side effect.
   Guarded against the obvious footgun: when the project list is empty (an unreachable or wrong meta
   DB looks exactly like "no projects") nothing is reaped.
3. **`make sandbox-reap-all`** also removes `forgeflow.role=sandbox` networks.

Exercised on the real daemon: 41 orphaned containers (all from dropped test databases) and 27
networks were reclaimed, 77 volumes kept, and network creation worked again immediately after.
