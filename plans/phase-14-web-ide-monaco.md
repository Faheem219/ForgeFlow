# Phase 14 — Web IDE (Monaco) + live code streaming

## Title & ID

- **ID:** `phase-14`
- **Title:** Web IDE (Monaco) + live code streaming
- **Epic:** Epic 2 — Sandbox & Web IDE
- **Milestone:** M1

## Goal

An in-browser Monaco editor bound to the workspace FS (file tree, tabs, dirty-state, save) that streams code live as the agent writes files, then hands editing back to the user.

## Context

The IDE surface of D5 and the "watch it build" experience. Live streaming consumes `fs.write` events (`phase-12`) so users see files appear/update during codegen (`phase-23`).

## Depends on

- **Phase IDs:** `phase-09`, `phase-12`
- **External:** —

### Prerequisite check

- `phase-12` ✅ (FS API + `fs.write` events); `phase-09` ✅ (workspace shell to mount into).

## Detailed tasks

1. **IDE feature (`src/features/ide/`).** `@monaco-editor/react` editor; **FileTree** (from `/fs/tree`), **Tabs**, dirty-state tracking, `Ctrl/Cmd-S` → `PUT /fs/file`; language detection by extension; TS/JS/JSON/HTML/CSS themes matching the dark palette.
2. **Live-generation mode.** Subscribe to `fs.write` events for the project; when the agent is writing (`phase-23`), open/focus the file **read-only** ("generating…"), stream content, and reveal cursor progress; on completion, unlock for editing. A mode flag in `workspaceStore` toggles read-only vs editable.
3. **Save + conflict.** Save writes via FS API + emits `fs.write`; if a file changed underneath (agent vs user), show a non-destructive conflict prompt (keep mine / reload).
4. **Wiring.** Mount the IDE as the **Build** stage panel's primary surface (and reusable elsewhere); lazy-loaded (Monaco is heavy).

## Out of scope for this phase

- Actual code generation (`phase-23`), preview pane (`phase-15`), terminal (delivered in `phase-13`, just docked alongside here). No LSP/IntelliSense beyond Monaco defaults.

## Acceptance criteria

- Edits persist to the sandbox (save → reflected on re-read and on disk in the container).
- Live streaming: when files are written server-side, the IDE shows them **appearing/updating in real time** in read-only mode, then becomes editable.
- File tree, tabs, dirty indicators, and save all work; conflicts are handled non-destructively.

## Tests to write

- `frontend/src/features/ide/FileTree.test.tsx` — renders tree; open file → tab.
- `frontend/src/features/ide/editorSave.test.tsx` — dirty-state + save calls FS API (mock).
- `frontend/src/features/ide/liveStream.test.tsx` — `fs.write` events stream content read-only then unlock (mock WS).

## Manual verification steps

1. Open a file, edit, save → reload → change persisted.
2. Trigger server-side writes (a script that writes files + emits `fs.write`) → watch them stream into the editor read-only, then unlock.
3. Edit a file, cause a server write to the same path → conflict prompt appears.

## Rollback / cleanup notes

- UI-only. Reversible.

## Design notes

- Read-only-during-generation avoids edit races with the agent; the explicit unlock hands control back cleanly.

## Status

⬜ todo
