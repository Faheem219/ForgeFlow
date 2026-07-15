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

✅ done — `src/features/ide/`: Monaco editor bound to the phase-12 FS API with a **FileTree**
(`/fs/tree`, flat→nested via a pure `buildTree` that also synthesises intermediate dirs),
**EditorTabs** (dirty dots, generating badge, close), per-extension language detection, a
theme-matched dark editor, and save via **Ctrl/Cmd-S** or button → `PUT /fs/file`. State lives in a
pure `ideStore` (tabs/dirty/generating/conflict), so the interesting logic is testable without
mounting Monaco. Mounted as the **Build** stage's primary surface, lazy-loaded.

**Live-generation mode** (`liveWrites.ts`): `fs.write` → re-read the file (the event carries only a
path) → open/focus it **read-only** with a "generating…" badge → unlock after a quiet period
(`UNLOCK_QUIET_MS`; phase-23 can emit an explicit completion signal later). **Save + conflict:** a
server write landing on unsaved edits raises a non-destructive keep-mine / reload prompt; a clean
open file just reloads in place.

Verified: eslint/prettier/tsc clean; **frontend 67 passed** (35 new — buildTree nesting/synthesised
dirs, tree render+expand/collapse+open, open→tab, dirty→save→clean, Ctrl/Cmd-S, no-op save,
save-failure keeps dirty, self-write marker, tab close; live stream read-only→unlock, successive
writes, echo suppression, conflict raised + resolved both ways, foreign-project ignored, backlog not
replayed); build OK. **Live FS contract check against the real backend — 11/11**: the exact URLs
`api.ts` builds work, response shapes match the TS types (incl. `size: null` for dirs), nested files
report their ancestor dirs, and **an edit persists across a re-read and on disk in the workspace**.

**Design notes / decisions:**
- **Monaco is bundled locally** (`monacoSetup.ts` points the loader at the bundled copy and wires
  the language workers). The default CDN loader would break the PWA offline and add a third-party
  origin.
- **Monaco is excluded from the PWA precache** (`globIgnores` in `vite.config.ts`). `ts.worker` is
  ~6 MB (it *is* the TypeScript compiler) and the IDE chunk ~3.4 MB, but the IDE cannot work offline
  regardless — it reads/writes the sandbox over the API — so precaching it would cost megabytes of
  service-worker install for zero offline benefit. The app shell stays precached (1.3 MB); the IDE
  loads on demand. Trimming Monaco's language set is a possible future win (phase-49).
- **Bug caught en route:** `useLiveWrites` started from `processed = 0`, so mounting the IDE would
  replay the realtime store's whole buffered backlog (up to 200 events) and spray open a tab per past
  write. It now baselines to the current event count (and re-baselines on project switch); covered by
  a regression test.

**Deferred (scoped):** codegen itself is phase-23, preview is phase-15; no LSP/IntelliSense beyond
Monaco defaults. `act()` warnings in the IDE tests come from react-query refetches resolving outside
`act` — a known async-testing artifact; `waitFor` keeps the tests deterministic.
