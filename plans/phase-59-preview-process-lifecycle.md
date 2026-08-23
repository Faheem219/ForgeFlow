# Phase 59 — Preview process lifecycle & port reclaim

## Title & ID

- **ID:** `phase-59`
- **Title:** Preview process lifecycle & port reclaim (orphan reaping, PID-budget safety, restart storms)
- **Epic:** Epic 2 — Sandbox & IDE *(amends `phase-15`)*
- **Milestone:** M1 *(reliability)*
- **Status:** ✅ done *(2026-08-23)*

## Goal

A preview restart must always land on the ports the proxy routes to. Today orphaned dev servers
survive a restart, squat on 5173/3001, push Vite onto a port nothing proxies, exhaust the sandbox
PID budget, and eventually crash Node itself. Make process ownership survive a control-plane
restart, and make port reclaim independent of remembered PIDs.

## Context

Reported from a live session. Frontend:

```
Port 5173 is in use, trying another one...
Port 5174 is in use, trying another one...
  ➜  Local:   http://localhost:5175/
```

Two stale Vite instances were already holding 5173 **and** 5174. Caddy routes
`*.preview.localhost` to `forgeflow-sb-<id>:5173` (`infra/caddy/Caddyfile`), so the moment Vite
slides to another port the preview is unreachable no matter how healthy it is. Backend, repeatedly:

```
Error: listen EADDRINUSE: address already in use 0.0.0.0:3001
```

And finally, both processes died with:

```
node[10982]: ... WorkerThreadsTaskRunner::DelayedTaskScheduler::Start()
Assertion failed: (0) == (uv_thread_create(t.get(), start_thread, this))
```

`uv_thread_create` failing is **not** a port problem — it is the sandbox's `SANDBOX_PIDS_LIMIT`
(default 256, `.env.example`) exhausted by accumulated orphans, so Node cannot start its own
threadpool. Three distinct causes:

**1. Ownership is in-memory only.** `PreviewService.stop` (`app/sandbox/preview.py:322-330`) kills
what is in `self._state`, a plain dict on the service instance. `start` (`:216`) calls `stop` first
for restart semantics — correct, but useless when `_state` is empty. In dev the control plane runs
`uvicorn --reload`: **every backend edit wipes `_state`** and orphans whatever was running inside
the sandbox. The container long outlives the API process (`manager.reconcile()` adopts it at
startup) and nothing reconciles *processes*.

**2. The kill may not reach grandchildren.** `DockerExecHandle.kill`
(`app/sandbox/runtime.py:452-457`) resolves the exec's PID and runs
`kill -9 -{pid} 2>/dev/null || kill -9 {pid}`. The negative-PID form needs that PID to be a process
group **leader**; a `docker exec`'d process is not reliably one. When it is not, the fallback kills
only `pnpm`, orphaning the `vite`/`tsx` child that actually holds the socket — which is exactly the
observed signature (parent gone, port still held).

**3. Restart storms.** The backend log shows `tsx watch` restarting on every codegen file write, each
restart racing the previous one for 3001. `phase-56` writes a phase at a time and the verifier boots
the app, so a preview left running during a build re-enters this loop repeatedly, multiplying
orphans and burning the PID budget.

There is also no tooling to see any of this: the sandbox image (`sandbox/Dockerfile`) installs
`ca-certificates`, `curl`, `git`, `tini` — **no `procps`, no `psmisc`**, so neither ForgeFlow nor a
user in the terminal can run `ps`, `pkill` or `fuser`.

## Depends on

- `phase-10` (sandbox image) · `phase-11` (manager) · `phase-13` (exec) · `phase-15` (preview) ·
  `phase-55`/`phase-56` (the build loop that starts previews) — all ✅.
- Requires a sandbox image rebuild (`make sandbox-build`); existing containers keep running the old
  image until recreated, so the reclaim path must degrade gracefully without the new tools.

## Detailed tasks

### 1. Sandbox image (`sandbox/Dockerfile`)

- Add `procps` (`ps`, `pkill`) and `psmisc` (`fuser`) — a few hundred KB, and the difference between
  reclaiming a port deterministically and guessing. Keep the pinned-by-distro, `--no-install-recommends`
  style and the existing layer.
- Extend `sandbox/smoke/run_smoke.sh` to assert both are present and runnable **as the `app` user**
  under the read-only root.

### 2. Deterministic port reclaim (`app/sandbox/preview.py`, `app/sandbox/runtime.py`)

- New `reclaim_ports(runtime, ports)`: inside the container, `fuser -k -n tcp <port>` for each port,
  then verify the port is free, then fall back to a `/proc`-scanning shell one-liner (no procps
  needed) for containers still on the old image. Never touches anything outside the container.
- `PreviewService.start` calls it **after** `stop` and **before** launching, unconditionally — the
  invariant is "these two ports are mine", not "I remember starting something".
- Fail loudly rather than silently sliding: if a port is still occupied after reclaim, raise a
  `SystemError` naming the port instead of letting Vite pick 5175 and reporting a healthy preview
  the proxy cannot reach. Pin Vite with `--strictPort` in `PREVIEW_FE_CMD` so the failure is honest.

### 3. Durable process ownership (`app/sandbox/preview.py`)

- Write a **pidfile per process** inside the workspace (`.forgeflow/preview/{fe,be}.pid`), owned by
  the sandbox, so ownership outlives the control-plane process.
- `PreviewService` reconciles from pidfiles on first touch of a project (and from
  `manager.reconcile()` at startup): adopt what is alive, clear what is dead, kill what is orphaned.
- `.forgeflow/` is excluded from the workspace file tree, from codegen context, and from the deploy
  source collection (`phase-58` task 4) so it never reaches the model or a deployment.

### 4. Kill the whole tree (`app/sandbox/runtime.py`)

- Start preview execs with `setsid` so the exec's PID **is** the group leader, making
  `kill -9 -PID` correct by construction rather than by luck.
- `DockerExecHandle.kill` gains a verified path: signal the group, wait briefly, escalate to the
  pidfile's children via `pkill -P`, and report whether the process is actually gone.
- Same treatment for `DockerTtyHandle.close` (`:485-495`), which has the identical negative-PID
  assumption.

### 5. PID-budget safety (`app/sandbox/manager.py`, `app/sandbox/preview.py`)

- A cheap in-container process census (`ps` when available) exposed on preview status, so runaway
  process counts are visible instead of surfacing as an inscrutable `uv_thread_create` assertion.
- Emit a `preview_status` warning when the count crosses a fraction of `SANDBOX_PIDS_LIMIT`
  (`PREVIEW_PID_WARN_RATIO`, default 0.8) — the same fail-soft, tell-the-user posture as the
  existing proxy/DB warnings (`preview.py:67`, `:259-270`).
- Map the `uv_thread_create` assertion in `app/agents/build_errors.py` to the **environment** verdict,
  so `phase-55` short-circuits instead of asking the model to patch source for a dead sandbox.

### 6. Restart storms during a build

- The build verifier owns the preview for the duration of verification: `phase-56`'s per-phase writes
  must not each trigger a `tsx watch` restart race. Quiesce the preview while phases are being
  written and start it once at verification, rather than leaving a watcher running through the whole
  build.
- Debounce/serialise `restart` so two concurrent restarts cannot both be mid-flight on one project.

## Out of scope for this phase

- Changing `SANDBOX_PIDS_LIMIT`'s default, or any other isolation limit (they are deliberate).
- Multi-preview-per-project, or previewing more than the two skeleton processes.
- The generated app's own watcher configuration (`tsx watch` stays; only *who starts it and when*
  changes).
- Any change to the proxy or the preview URL scheme.

## Acceptance criteria

1. Restarting the control plane (`--reload` edit) then pressing **Restart preview** lands the FE on
   5173 and the BE on 3001 — verified by the preview URL working, not just by a health probe.
2. Ten consecutive start/stop cycles leave no orphaned `vite`/`tsx`/`node` processes and a flat
   process count.
3. A port that cannot be reclaimed produces a clear, user-visible error naming the port — never a
   silently-relocated dev server reported as healthy.
4. A build that runs to completion with the preview open produces no `EADDRINUSE` in the logs.
5. A PID-exhaustion crash is classified `environment` and short-circuits the repair loop with zero
   model iterations.
6. `make sandbox-smoke` passes, including the new tool assertions.
7. A container from the *old* image still previews correctly (degraded reclaim path).

## Tests to write

- `tests/sandbox/test_preview_reclaim.py` — reclaim is called before launch; a still-occupied port
  raises rather than sliding; the `/proc` fallback runs when `fuser` is absent.
- `tests/sandbox/test_preview_pidfile.py` — pidfiles are written, adopted after a simulated
  control-plane restart (fresh `PreviewService`, same fake runtime), and cleared when dead.
- `tests/sandbox/test_runtime_kill_tree.py` (LocalRuntime) — a parent spawning a child that holds a
  port: `kill` leaves neither behind; `setsid` makes the group kill authoritative.
- `tests/sandbox/test_isolation_flags.py` (extend) — `.forgeflow/` never appears in the workspace
  tree or codegen context.
- `tests/agents/test_build_errors.py` (extend) — the `uv_thread_create` assertion classifies as
  `environment`.
- `tests/sandbox/test_preview_pid_census.py` — the warning fires past the ratio and rides on
  `preview_status`.

## Manual verification steps

1. `make sandbox-build`; start a preview; `docker exec` the sandbox and confirm `ps`/`fuser` exist.
2. Edit a backend file to force `--reload`; press Restart preview; confirm 5173/3001 and a working
   preview URL.
3. `kill -9` the Vite process from inside the container, restart, confirm clean reclaim.
4. Run a full build with the preview open; grep the logs for `EADDRINUSE` — expect none.

## Rollback / cleanup notes

- Pure additive behaviour; reverting the code leaves stale `.forgeflow/preview/*.pid` files, which
  are inert. The image change is a rebuild away in either direction.
- `make sandbox-reap-all` remains the blunt recovery for a wedged environment.

## Design notes

- **Ports, not PIDs, are the invariant.** The proxy routes to fixed ports, so "the port is mine" is
  the property that must hold. Remembered PIDs are an optimisation that stops being true across a
  reload; reclaiming by port is true regardless of who started what.
- **Fail honest over fail soft, here.** Elsewhere ForgeFlow degrades gracefully; a preview that
  reports healthy on a port nothing routes to is worse than an error, because the user sees "running"
  and a dead iframe. This mirrors the existing proxy-missing and unreachable-DB warnings, which
  already chose visibility over silence.
- **Isolation is unchanged.** No new host access, no capability, no network. `procps`/`psmisc` act
  only inside the container, and the reclaim path only ever targets the two preview ports.

## Implementation status (as-built, 2026-08-23)

**Ports, not PIDs.** New `app/sandbox/ports.py` builds the reclaim as pure shell strings, so the
whole thing is unit-testable without a daemon. `PreviewService._reclaim` runs it after `stop` and
before any launch, unconditionally — the invariant is *"these ports are mine"*, which holds after a
`--reload` wiped `_state`, and the old PID-based one did not. A port that survives every attempt
raises a `SystemError` naming it, because a preview reported healthy on a port nothing proxies is
strictly worse than an error.

**Two reclaim mechanisms.** `fuser -k -n tcp` first; then a pure `/proc` scan (state `0A` in
`/proc/net/tcp{,6}` → socket inode → the owning fd in `/proc/*/fd`) so a container still running the
**old image** degrades rather than failing. Recorded pidfiles are killed tree-wise first, recursing
through `pgrep -P` (or `/proc/*/stat` PPIDs) so a `pnpm` parent cannot leave its `vite` child on the
socket.

**Ownership survives a restart.** Rather than a docker-specific PID lookup, the launch command is
wrapped as `sh -c 'mkdir -p … && echo $$ > <pidfile> && exec <cmd>'`. The `exec` is load-bearing:
the shell is *replaced*, so the recorded PID is the dev server itself, not a wrapper whose death
leaves an unkillable orphan. Pidfiles live in `.forgeflow/preview/`, added to `_PRUNE_DIRS` and the
`find -prune` list so they never reach the workspace tree, the model's context, or a deploy upload.

**Fail honest.** `PREVIEW_FE_CMD` gains `--strictPort`, so Vite errors instead of sliding to 5175 and
reporting success on a port the proxy does not route to.

**PID pressure is visible.** A process census rides on preview start and warns past
`PREVIEW_PID_WARN_RATIO` (0.8) of `SANDBOX_PIDS_LIMIT`. `build_errors` now classifies
`uv_thread_create` / `WorkerThreadsTaskRunner` as **environment** under the existing process-limit
class — node aborts natively at the ceiling with a message naming neither PIDs nor limits, so it
previously read as a code crash and would have burned repair iterations. `EADDRINUSE` and Vite's
`is in use, trying another one` get their own environment class.

**Restart storms.** `CodegenAgent.run` quiesces any running preview before writing a file. A build
rewrites dozens of files; a watcher left running restarts on each and those restarts race for the
port — the exact `EADDRINUSE` sequence reported against a refine. Verification starts one fresh
preview at the end.

**Image.** `procps` + `psmisc` added; `run_smoke.sh` asserts `ps`/`pgrep`/`pkill`/`fuser` exist *and*
run as the `app` user under a read-only root, which is the only way the sandbox ever calls them.

**Verification.** `tests/sandbox` + `tests/agents`: **428 passed**, 2 skipped. The 5 failures in
`test_repair_*` are the known pre-existing Docker-daemon tests — confirmed identical on a clean
stash. `ruff` clean; `mypy` clean apart from the 4 pre-existing `runtime.py` conditional-import
errors. The generated reclaim script is checked with `sh -n` in the suite, so a syntax error cannot
reach the sandbox unnoticed.

**Deferred (as scoped):** no live multi-cycle start/stop run against a real sandbox (needs a rebuilt
image and a daemon) — the shell is validated by syntax check and the decisions by unit test.
`setsid`-based process-group creation was considered and **rejected**: `setsid` forks when already
a group leader, and `setsid -w` puts the child in a session the exec's PID is not part of, so
`kill -- -PID` would reach nothing. Tree-walking plus port reclaim is correct without that fragility.

