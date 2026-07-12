# Phase 07 — Conversation & artifact model

## Title & ID

- **ID:** `phase-07`
- **Title:** Conversation & artifact model
- **Epic:** Epic 1 — Project & Orchestration Core
- **Milestone:** M1

## Goal

Per-project/per-stage message history and **versioned, never-overwritten** artifacts with a blob-storage strategy and token-usage capture.

## Context

Implements §6 (`Message`, `Artifact`) and the §7 idempotency rule ("artifacts are versioned rather than overwritten"). This backs "revisit/refine" (D12) and feeds the eval harness cost accounting.

## Depends on

- **Phase IDs:** `phase-06`
- **External:** —

### Prerequisite check

- `phase-06` ✅ (projects + stages exist); repos from `phase-02` available.

## Detailed tasks

1. **Message service (`app/projects/` or `app/orchestrator/messages.py`).** Append message `{project_id, stage?, role, content, artifacts[], token_usage}`; list by project and by `(project, stage)`; pagination.
2. **Artifact service (`app/orchestrator/artifacts.py`).** `create_version(project_id, stage, type, payload/ref, meta)` → computes next `version` (per `project+stage+type`), never overwrites; `get_latest`, `get_version`, `list_versions`, `diff(a,b)` (text artifacts). Returns typed `Artifact`.
3. **Blob strategy (`app/db/blobs.py`).** Abstraction for large payloads: **GridFS** (default) or filesystem+ref, chosen by config resolver (`admin > env > default`). `put(bytes) -> ref`, `get(ref) -> bytes`, `delete(ref)`. Small structured artifacts stay inline in the doc; large ones (design HTML, stdout, diffs) go to blobs with a `ref`.
4. **Token usage capture.** Helper to attach `{input_tokens, output_tokens, model, inr}` to messages/runs; consumed by `phase-20`/`phase-46`.
5. **Router (`app/projects/router.py` additions).** `GET /projects/{id}/messages?stage=`, `GET /projects/{id}/artifacts?stage=&type=`, `GET /artifacts/{artifact_id}` (+ `?version=`), `GET /artifacts/{a}/diff/{b}`. Ownership-checked.

## Out of scope for this phase

- Agent-produced content (that's the codegen/testgen/repair phases). Realtime streaming of tokens (`phase-14`/producers). This phase is the **store + retrieval + versioning + diff**.

## Acceptance criteria

- Creating a second artifact of the same `(project, stage, type)` yields `version=2`; both retrievable; nothing overwritten.
- Messages queryable per project and per stage, in order.
- Large payloads persist via the blob layer and round-trip by `ref`; small ones inline.
- `diff(a,b)` returns a correct unified diff for text artifacts.

## Tests to write

- `backend/tests/orchestrator/test_artifact_versioning.py` — monotonic versions per tuple; `get_latest`/`get_version`.
- `backend/tests/orchestrator/test_blobs.py` — put/get/delete round-trip for both backends (parametrized).
- `backend/tests/orchestrator/test_messages.py` — append/list ordering + stage filter.
- `backend/tests/orchestrator/test_artifact_diff.py` — diff correctness.

## Manual verification steps

1. Create two versions of a `design` artifact → `GET /artifacts?type=design` shows both; latest is v2.
2. `GET /artifacts/{a}/diff/{b}` → readable unified diff.
3. Post a large blob artifact → stored via GridFS; `GET` returns identical bytes.

## Rollback / cleanup notes

- Deleting a project should cascade its messages + artifacts + orphaned blobs (add a cleanup helper). Verify no orphan GridFS chunks remain. Reversible.

## Design notes

- **GridFS default** keeps everything in Mongo (one dependency, aligns with D8); filesystem+ref is offered for large-volume local dev. Selectable via config.

## Status

⬜ todo
