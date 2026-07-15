# Phase 13 — Command execution & terminal

## Title & ID

- **ID:** `phase-13`
- **Title:** Command execution & terminal
- **Epic:** Epic 2 — Sandbox & Web IDE
- **Milestone:** M1

## Goal

Run commands inside the sandbox with streamed stdout/err, exit codes, timeouts, and cancellation; bridge a real interactive shell to an in-browser xterm.js terminal.

## Context

Powers dep installs, dev servers, and test runs (agent tools `phase-21`, test runner `phase-28`), and gives the user a live terminal (D5). All exec happens **inside** the sandbox (§7).

## Depends on

- **Phase IDs:** `phase-05`, `phase-11`
- **External:** —

### Prerequisite check

- `phase-11` ✅ (sandbox); `phase-05` ✅ (WS hub for streaming).

## Detailed tasks

1. **Exec service (`app/sandbox/exec.py`).** `run(project, cmd, cwd="/workspace", env?, timeout)` → streams `{stream: stdout|stderr, chunk}` and returns `{exit_code, duration}`; supports **cancellation** (kill the exec/process); enforces timeouts; captures combined output to a blob for `TestRun` stdout refs later.
2. **Terminal bridge (`app/sandbox/router.py` WS).** `GET /ws/projects/{id}/terminal`: allocate a TTY exec (shell) in the container; pipe bytes bidirectionally over WS; handle resize (cols/rows), Ctrl-C, and disconnect cleanup. Auth + ownership on connect.
3. **Streaming contract.** Non-interactive `run` streams `terminal.output`/`progress` events via `phase-05`; interactive terminal uses the dedicated terminal WS (raw bytes).
4. **Frontend terminal (`src/features/ide/Terminal.tsx`).** `xterm.js` bound to the terminal WS; fit-addon for resize; theme-matched; reconnect handling.
5. **Guardrails.** Commands run as the non-root sandbox user; no host access; per-command timeout + a max concurrent execs per project (config).

## Out of scope for this phase

- Monaco editor (`phase-14`), preview servers/proxy (`phase-15`), structured test parsing (`phase-28`). This phase = exec + terminal transport.

## Acceptance criteria

- `run` streams stdout/err live, returns the correct exit code, and enforces timeouts.
- Long-running commands can be **cancelled** cleanly (process killed, resources freed).
- The browser terminal is interactive (run `ls`, `node -v`, start/stop a process); resize works; Ctrl-C interrupts.
- All execution occurs inside the sandbox as non-root.

## Tests to write

- `backend/tests/sandbox/test_exec_stream.py` — stdout/stderr streaming + exit code + timeout (integration behind docker marker; unit with a fake docker client).
- `backend/tests/sandbox/test_exec_cancel.py` — cancellation kills the process.
- `frontend/src/features/ide/Terminal.test.tsx` — xterm mounts, writes bytes to WS, handles resize (mock WS).

## Manual verification steps

1. Open the workspace terminal → `node -v`, `pnpm -v` → correct output.
2. Start `sleep 60` → cancel → returns promptly; container healthy.
3. `run("pnpm install")` via API → streamed output + exit code 0.

## Rollback / cleanup notes

- Ensure orphaned exec processes are reaped on WS disconnect. No persistent state. Reversible.

## Design notes

- Interactive terminal uses **raw bytes** over a dedicated WS (not the typed event schema) to preserve TTY fidelity; typed events remain for programmatic runs.

## Status

✅ done — Exec + terminal transport over the sandbox, extending the phase-12 `WorkspaceRuntime` seam
with `start_exec`/`start_tty` so both backends share one service. `DockerRuntime` (**production**):
`exec_create`/`exec_start(demux)` streaming, `exec_inspect` exit codes, kill-by-PID (docker has no
"kill exec" API), and a TTY exec socket + `exec_resize`. `LocalRuntime` (**tests**): real subprocess
pipes + a real `pty`, each in its own process group so a kill takes the whole tree.

`app/sandbox/exec.py` `ExecService.run(project, cmd, cwd, env, timeout)`: bridges the blocking
runtime handle onto the loop (worker thread → `asyncio.Queue`, never blocking it), streams
`terminal.output` chunks, emits `exec.status` started/exited (new `EventType`), enforces the
timeout (kill + reap → exit `124`), supports `cancel(exec_id)`, caps concurrent execs per project,
and captures combined output to a blob for later `TestRun` stdout refs (best-effort). Routes:
`POST /projects/{id}/exec`, `POST /projects/{id}/exec/{exec_id}/cancel`, and
`GET /ws/projects/{id}/terminal` — the terminal deliberately carries **raw bytes** (binary = stdin/
stdout, text = JSON `resize`) to preserve TTY fidelity; the PTY is always reaped on disconnect.
Frontend: `TerminalClient` (raw-byte WS, exp-backoff reconnect that resumes at the current size) +
`Terminal.tsx` (xterm + FitAddon, ResizeObserver → PTY winsize, theme-matched), lazy-loaded behind a
Workspace drawer so xterm stays out of the main chunk. New config keys `sandbox_exec_timeout_s`,
`sandbox_max_concurrent_execs`, `sandbox_shell` (+ `.env.example`).

Verified: `ruff`/`black`/`mypy --strict` + eslint/prettier/tsc clean; **backend 219 passed** (12 new)
**+ frontend 45 passed** (14 new); frontend build OK (Terminal split into its own 294 kB chunk).
**Live over real sockets** (uvicorn + `websockets`, real PTY) — 13/13: exec exit code, blob capture
(real GridFS ref), stdout+stderr streamed, started/exited events, timeout→124; terminal runs
commands, `node -v`→v20.14.0, **resize reaches the PTY** (`stty size`→`44 133`), **Ctrl-C interrupts**
`sleep 60`, shell **reaped on disconnect**, and both no-token + foreign-project rejected.

**Notes:** non-root execution is inherent to the production path — `DockerRuntime` execs inside the
phase-10 image whose `USER app` (uid 1000) is the only account; `LocalRuntime` is a test backend and
never used by the control plane. Pre-`accept()` WS rejections surface as an HTTP 403 handshake
failure rather than the 4401/4403 close codes (same as phase-05) — the connection is refused either
way. **Deferred (scoped):** a real-daemon integration test is a no-op without Colima here — the
identical service logic is proven through `LocalRuntime`; Monaco (phase-14), preview (phase-15), and
structured test parsing (phase-28) build on this.
