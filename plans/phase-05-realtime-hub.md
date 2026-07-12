# Phase 05 — Realtime event hub

## Title & ID

- **ID:** `phase-05`
- **Title:** Realtime event hub
- **Epic:** Epic 0 — Foundations & Scaffolding
- **Milestone:** M1

## Goal

A WebSocket hub that streams typed, ordered, **resumable** events per project from control plane → frontend, with a matching reconnecting client.

## Context

Implements §7 realtime conventions. Every long-running task (agent tokens, file writes, terminal output, test results, deploy status) rides this bus (D5, §3). Resumability from `seq` is the invariant that makes long tasks safe to reconnect into.

## Depends on

- **Phase IDs:** `phase-03`
- **External:** —

### Prerequisite check

- `phase-03` ✅; a valid JWT can be minted for the connecting user.

## Detailed tasks

1. **Event schema (`app/realtime/schemas.py`).** `Event = {event: str, project_id: str, stage: str|None, payload: dict, seq: int, ts}`. Define an enum/registry of event types (`agent.token`, `fs.write`, `terminal.output`, `test.result`, `deploy.status`, `stage.transition`, `progress`, `ping`). Pydantic-typed.
2. **Hub (`app/realtime/hub.py`).** In-process pub/sub keyed by `project_id`; per-project monotonic `seq` counter; a bounded **ring buffer** (recent N events per project) enabling replay from a client-supplied `last_seq`. `publish(event)` and `subscribe(project_id)`.
3. **WS router (`app/realtime/router.py`).** `GET /ws/projects/{project_id}` (WebSocket): authenticate on connect (JWT in query/subprotocol), authorize project ownership, accept `?last_seq=` to replay missed events, then live-stream. Heartbeat ping/pong; clean disconnect handling.
4. **Publish helper.** A thin `emit(project_id, event, payload, stage=None)` used by services; assigns `seq`, timestamps, persists a lightweight pointer/`events_ref` where relevant (full trace storage is `phase-46`).
5. **Frontend client (`src/lib/wsClient.ts`).** Reconnecting WS with exponential backoff; tracks `last_seq`; on reconnect requests replay; dedupes by `seq`; exposes a subscribe API. Wire into a Zustand `realtimeStore` and a topbar connection indicator.
6. **Demo surface.** A `progress` sample stream endpoint/task + a tiny frontend panel proving ordered arrival and resume.

## Out of scope for this phase

- Terminal PTY bridge (`phase-13`), live code-write streaming into Monaco (`phase-14`), deploy/test event producers (their own phases). This phase delivers the **transport + contract + demo**, not the producers.

## Acceptance criteria

- Events arrive **in `seq` order**; no gaps under normal operation.
- Killing the socket and reconnecting with `last_seq` **replays** missed events (within ring-buffer window) with no duplicates and no loss.
- Connecting without a valid token → rejected; connecting to another user's project → rejected.

## Tests to write

- `backend/tests/realtime/test_hub_ordering.py` — publish N; subscriber receives ordered `seq`.
- `backend/tests/realtime/test_replay.py` — subscribe with `last_seq` mid-stream → correct backfill; beyond-window handled gracefully.
- `backend/tests/realtime/test_ws_authz.py` — no token rejected; foreign project rejected.
- `frontend/src/lib/wsClient.test.ts` — reconnect/backoff + dedupe by `seq`.

## Manual verification steps

1. Open the demo panel; start the sample `progress` stream → counter updates in order.
2. In DevTools, drop the WS connection mid-stream → client reconnects and resumes without missing ticks.
3. Attempt to connect with a tampered token → connection closes with an auth error.

## Rollback / cleanup notes

- Purely in-memory hub; restarting the API clears buffers (acceptable — clients resubscribe). No persistent state to clean. Reversible.

## Design notes

- **In-process hub** is sufficient for single-instance dev/demo. If horizontal scale is ever needed, swap the pub/sub for Redis behind the same `hub` interface — noted, not built (out of scope).
- Ring-buffer window size is config-resolved (`admin > env > default`), default sized for the longest expected task burst.

## Status

✅ done — Backend: typed `Event` + `EventType` schema; in-process `RealtimeHub` (per-project
monotonic `seq`, bounded replay ring buffer, pub/sub queues, `subscription()` CM) + module `emit`
helper; WS route `GET /ws/projects/{id}` (JWT-in-query auth, project/demo-channel authz, initial
`ping` handshake, `last_seq` replay with dedup, heartbeat, clean disconnect) + a `POST
/realtime/demo/progress` sample stream; wired into `create_app()`; ring/heartbeat are
config-resolved. Frontend: reconnecting `RealtimeClient` (exp backoff, `last_seq` resume, seq
dedup, ping-ignore), `realtimeStore`, topbar connection indicator (offline/connecting/live), and a
`/_realtime` demo panel. Verified: `make lint` clean; `make test` = **backend 41** (hub
ordering/replay/beyond-window/per-project-seq, WS auth+authz incl. malformed-id rejection) **+
frontend 23** (wsClient dedup, reconnect+resume-from-last_seq, exp backoff, stop-on-close);
frontend build OK. **Live end-to-end over a real socket (uvicorn + websockets client):** ordered
stream `[1,2,3,4]`, resume `last_seq=6 → [7,8]` (no loss/dupes), and both no-token + foreign-channel
rejections. **Bug fixed en route:** `bson.InvalidId` isn't a `ValueError` subclass here — added it
to the id-parse guards in `authorize_project` + `get_user_by_id` (a malformed id would have 500'd).
**Deferred (scoped):** the TestClient WS test (Starlette teardown deadlock) was replaced by the
live-socket validation; `events_ref` persistence is phase-46; producers (agent/fs/test/deploy
events) are their own phases.
