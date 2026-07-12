# Phase 03 — Auth & user management

## Title & ID

- **ID:** `phase-03`
- **Title:** Auth & user management
- **Epic:** Epic 0 — Foundations & Scaffolding
- **Milestone:** M1

## Goal

Email/password authentication with JWT sessions, protected FastAPI routes, an `admin` role check, and frontend auth screens with guarded routes.

## Context

Gates every user-scoped resource (projects, sandboxes, credentials, data). Establishes the `current_user` dependency and the `require_admin` dependency that the admin config dashboard (`phase-52`) and admin APIs (`phase-51`) rely on. Honors §7 security posture.

## Depends on

- **Phase IDs:** `phase-02`
- **External:** `SECRET_KEY` set.

### Prerequisite check

- `phase-02` ✅; `User` doc has `role` field and unique-email index.
- Config resolver returns `SECRET_KEY` + JWT settings.

## Detailed tasks

1. **Service (`app/auth/service.py`).** `passlib[bcrypt]` hashing; `python-jose` JWT issue/verify; access-token TTL from config; register (reject duplicate email), authenticate, `get_user_by_id`.
2. **Router (`app/auth/router.py`).** `POST /auth/register`, `POST /auth/login` (returns `{access_token, token_type, user}`), `POST /auth/logout` (client-side token drop; optional server denylist), `GET /auth/me`. Typed Pydantic request/response schemas; passwords never returned.
3. **Deps (`app/auth/deps.py`).** `get_current_user` (decode + load), `require_admin` (403 if `role != admin`). Register these for reuse.
4. **Rate limiting.** Basic in-memory/Redis-optional limiter on `login`/`register`; configurable via resolver.
5. **Frontend auth (`frontend/src/features/auth/`).** Login + Register screens; `authStore` (Zustand) holding token + user; token persisted (secure storage) and attached by `apiClient`; route guard wrapper; `RequireAuth` and `RequireAdmin` wrappers; redirect unauthenticated users to `/login`.
6. **Wire guards** into the router shell (`phase-04` provides the shell; here provide the guard primitives + login/register pages).

## Out of scope for this phase

- OAuth/social login, SSO, password reset emails (post-v1). Admin config screens themselves (`phase-52`) — only the `require_admin` primitive and `role` plumbing are delivered here.

## Acceptance criteria

- Unauthenticated request to a protected route → 401; wrong role to an admin route → 403.
- Register → login → `GET /auth/me` returns the user (no password hash).
- Passwords stored only as bcrypt hashes; never logged or returned.
- Frontend: login persists session across reload; logout clears it; guarded routes redirect when unauthenticated.

## Tests to write

- `backend/tests/auth/test_auth_flow.py` — register/login/me happy path; duplicate email rejected; bad password 401.
- `backend/tests/auth/test_authz.py` — `get_current_user` rejects bad/expired tokens; `require_admin` enforces role.
- `frontend/src/features/auth/authStore.test.ts` — token persistence + attach; logout clears.
- Frontend guard test — unauthenticated redirect.

## Manual verification steps

1. `curl -X POST /auth/register -d '{email,password}'` → 201; repeat → 409.
2. `curl -X POST /auth/login` → token; `curl /auth/me -H "Authorization: Bearer …"` → user JSON.
3. UI: register → land in app; reload → still authed; logout → back to `/login`.

## Rollback / cleanup notes

- Remove seeded users from `forgeflow_meta.users`. Rotating `SECRET_KEY` invalidates all tokens (intended). Reversible.

## Design notes

- JWT (stateless) chosen for simplicity; a token denylist collection is optional and can be added if server-side logout is required later.
- Promote a user to `admin` via the seed script or a manual DB flag for now; a user-management admin screen is out of scope for v1.

## Status

⬜ todo
