# Phase 12 — Workspace filesystem service

## Title & ID

- **ID:** `phase-12`
- **Title:** Workspace filesystem service
- **Epic:** Epic 2 — Sandbox & Web IDE
- **Milestone:** M1

## Goal

Read/write/list workspace files via API over the per-project sandbox, with safe path handling and a **git-backed** workspace (init + commit helpers) so the repair loop can compute diffs.

## Context

The FS layer the IDE (`phase-14`) and agent tools (`phase-21`) use. Git-backing is a hard requirement for the diff-aware repair loop (D7, §9) — "diff since last passing run" needs a git history in the sandbox.

## Depends on

- **Phase IDs:** `phase-11`
- **External:** —

### Prerequisite check

- `phase-11` ✅ (sandbox exists); git present in the image (`phase-10`).

## Detailed tasks

1. **FS service (`app/sandbox/fs.py`).** Operate on the container's `/workspace` (via docker exec/archive APIs):
   - `list_tree(path=".", depth?)` → typed file-tree nodes.
   - `read_file(path)` / `write_file(path, content)` / `delete(path)` / `mkdir(path)` / `move(src,dst)`.
   - **Safe-path handling:** resolve + reject traversal outside `/workspace` (no `..`, no absolute escapes, no symlink escape); enforce max file size from config.
2. **Git helpers (`app/sandbox/git.py`).** `git_init` (on workspace creation), `git_commit(message)`, `git_current_sha`, `git_diff(sha_a, sha_b, paths?)`, `git_last_passing_ref` bookkeeping hook (a tag/ref updated by the test runner in `phase-28`). Used by the failure analyzer (`phase-29`).
3. **API (`app/sandbox/router.py` additions).** `GET /projects/{id}/fs/tree`, `GET /projects/{id}/fs/file?path=`, `PUT /projects/{id}/fs/file`, `DELETE …`, `POST …/git/commit`. Ownership-checked; touches update sandbox last-activity (`phase-11` reaper).
4. **Events.** `fs.write {path}` emitted on writes (drives live IDE updates in `phase-14`).

## Out of scope for this phase

- Monaco editor UI (`phase-14`), command exec/terminal (`phase-13`), diff-aware repair context assembly (`phase-29`) — only the git primitives it needs are here.

## Acceptance criteria

- Files round-trip through the API (write → read identical) inside the correct project's sandbox.
- Path traversal attempts (`../`, absolute, symlink escape) are **rejected**.
- `git_init` on workspace creation; `git_commit` records commits; `git_diff` returns correct diffs between SHAs.
- Writes emit `fs.write` events.

## Tests to write

- `backend/tests/sandbox/test_fs_roundtrip.py` — write/read/list/delete/move.
- `backend/tests/sandbox/test_path_safety.py` — traversal + symlink escape rejected; oversize rejected.
- `backend/tests/sandbox/test_git_helpers.py` — init/commit/sha/diff correctness (integration behind a docker marker).

## Manual verification steps

1. `PUT /fs/file {path:"src/x.ts", content}` then `GET /fs/file?path=src/x.ts` → identical.
2. `PUT /fs/file {path:"../../etc/passwd"}` → rejected.
3. Commit twice; `git_diff(sha1,sha2)` → expected unified diff.

## Rollback / cleanup notes

- Files live in the project volume; destroying the sandbox volume (`phase-11`) clears them. Reversible per project.

## Design notes

- FS operations go **through docker exec/archive**, never host FS — keeps untrusted files off the control plane (§7).
- A moving `last-passing` git ref (updated by the test runner) is the anchor for repair diffs; defined here, updated in `phase-28`.

## Status

⬜ todo
