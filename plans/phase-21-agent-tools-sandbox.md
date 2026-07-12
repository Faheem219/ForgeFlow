# Phase 21 — Agent tools bound to the sandbox

## Title & ID

- **ID:** `phase-21`
- **Title:** Agent tools bound to the sandbox
- **Epic:** Epic 4 — Build Stage (agentic codegen)
- **Milestone:** M2

## Goal

The strictly-schemaed tool surface the codegen/test/repair agents use — all operating **only** on the project sandbox, never the host.

## Context

Turns the client's tool-use loop (`phase-20`) into real actions over the FS (`phase-12`) and exec (`phase-13`) layers. Guardrails here are a load-bearing part of the §7 security posture: agents act exclusively through these tools.

## Depends on

- **Phase IDs:** `phase-12`, `phase-13`, `phase-20`
- **External:** —

### Prerequisite check

- `phase-12` ✅ (FS+git), `phase-13` ✅ (exec), `phase-20` ✅ (tool-use client).

## Detailed tasks

1. **Tool definitions (`app/agents/tools/`).** JSON-schema'd tools, each bound to a project sandbox context:
   - `read_file(path)`, `write_file(path, content)`, `list_dir(path)` → via `phase-12` FS (path-safe).
   - `run_command(cmd, cwd?, timeout?)`, `install_deps(manager=pnpm, args)` → via `phase-13` exec (streamed, bounded).
   - `run_tests(scope=unit|e2e|all, filter?)` → thin wrapper (full runner in `phase-28`; here returns raw pass/fail + output).
   - `start_preview()` / `restart_preview()` → via `phase-15`.
   - `git_commit(message)` → via `phase-12` git.
   - `instantiate_skeleton()` → copies `templates/app-skeleton/` into `/workspace` (see `phase-22`; used by codegen so the FE/BE scaffold is **never regenerated** by the LLM).
2. **Dispatcher (`app/agents/tools/registry.py`).** Map tool name → handler; validate args against schema; enforce per-tool guardrails (path safety, timeouts, no host access); record tool calls on the `Run` trace; emit `fs.write`/`terminal.output` events.
3. **Safety.** Every tool resolves the sandbox from the project context; reject any path/command that could touch the host; size/time/concurrency caps from config.
4. **Docs.** A tool catalog (schemas + guardrails) for prompt authors.

## Out of scope for this phase

- The agents that *use* the tools (codegen `phase-23`, testgen `phase-27`, repair `phase-30`), the full test runner/parsers (`phase-28`), the skeleton contents (`phase-22`).

## Acceptance criteria

- An agent (driven by a mock model script) can scaffold, modify, install, run, test, preview, and commit **purely through tools** — no direct host access anywhere.
- Tool args are schema-validated; invalid/unsafe calls are rejected with structured errors fed back to the model.
- All tool actions target the correct project sandbox and appear on the `Run` trace + event stream.

## Tests to write

- `backend/tests/agents/tools/test_tool_schemas.py` — schema validation; bad args rejected.
- `backend/tests/agents/tools/test_fs_tools.py` / `test_exec_tools.py` — tools call FS/exec correctly (mock sandbox).
- `backend/tests/agents/tools/test_tool_guardrails.py` — path traversal / host-access attempts rejected.
- `backend/tests/agents/tools/test_instantiate_skeleton.py` — copies the skeleton into the workspace.

## Manual verification steps

1. Drive a scripted mock tool loop that writes files, installs deps, runs a command, commits → all reflected in the sandbox + event stream.
2. Attempt a tool call with `path:"/etc/x"` → rejected; error fed back to the model.

## Rollback / cleanup notes

- Tools are stateless; effects live in the sandbox (reset via `phase-11` destroy). Reversible.

## Design notes

- `instantiate_skeleton` exists as a **tool** (and a direct service call) so the agent can request scaffold instantiation but the actual copy is a deterministic FS op — saving tokens per the user's "always use a prebuilt template" directive (`phase-22`/`phase-23`).

## Status

⬜ todo
