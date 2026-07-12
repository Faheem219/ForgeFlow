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

⬜ todo
