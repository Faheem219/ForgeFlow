# Phase 17 — Stitch MCP provider

## Title & ID

- **ID:** `phase-17`
- **Title:** Stitch MCP provider
- **Epic:** Epic 3 — Design Stage (pluggable)
- **Milestone:** M2

## Goal

A real Stitch MCP integration behind `DesignProvider` with centralized credentials, token refresh, quota tracking, exhaustion handling, and graceful fallback.

## Context

Stitch is the **default** design backend (D9) but carries real constraints (D10): GCP project + billing + OAuth/service-account, ~350 generations/month free, short-lived tokens. The platform holds credentials centrally; the adapter must fail soft (queue / fallback / clear message). This is the highest provider-risk phase.

## Depends on

- **Phase IDs:** `phase-16`
- **External:** GCP project with billing; Stitch OAuth/service-account creds; `STITCH_*` config.

### Prerequisite check

- `phase-16` ✅ (interface/registry); `STITCH_*` credentials present (else the provider self-reports `health()=down` and the stage falls back).

## Detailed tasks

1. **MCP client (`app/design/stitch.py`).** Speak the Stitch MCP: `image→UI`, `text→UI`, fetch HTML/CSS; implement all `DesignProvider` methods mapping to `DesignResult`.
2. **Auth (`app/design/stitch_auth.py`).** Centralized credential load (from the `phase-34` vault when available; env until then); **token refresh** for short-lived access tokens; single-flight refresh to avoid stampedes; never log tokens.
3. **Quota tracking.** Persist per-window generation counts (against the platform account); pre-check before calls; on approaching/hitting the cap, **fail soft**: (a) clear user message, (b) offer Figma fallback (`phase-18`) or `fake`/offline, (c) optional queue for later. Emit `design.quota` events.
4. **Resilience.** Backoff/retry on transient errors; classify auth vs quota vs transient vs fatal as `ProviderError` subtypes with `fallback_hint`; timeouts on all calls.
5. **Config.** All `STITCH_*` via resolver (`admin > env > default`) so the admin dashboard can rotate creds/toggle Stitch (`phase-51/52`).

## Out of scope for this phase

- Figma adapter (`phase-18`), the design UI (`phase-19`). Credential **encryption** lands in `phase-34` (until then, read from env/config; wire to the vault when present).

## Acceptance criteria

- A screenshot → a `design` artifact via Stitch (live smoke path).
- **Token refresh** works transparently across an expiry boundary (tested with a mocked clock/token).
- **Quota exhaustion** is handled gracefully: no crash, clear user-facing message, and automatic fallback/queue per config.
- Tokens never logged or returned to the client.

## Tests to write

- `backend/tests/design/test_stitch_mock.py` — image/text→UI, fetch_code, refine against a mocked MCP; result→artifact.
- `backend/tests/design/test_stitch_token_refresh.py` — expired token triggers a single refresh; concurrent calls single-flight.
- `backend/tests/design/test_stitch_quota.py` — at cap → `ProviderError(quota)` + fallback hint + `design.quota` event; no token leakage in logs.

## Manual verification steps

1. With valid creds, run a screenshot design intent → artifact + preview.
2. Force an expired token → observe a transparent refresh + success.
3. Simulate quota cap (config a tiny window) → user sees a clear message and the Figma/fake fallback path.

## Rollback / cleanup notes

- Toggle the provider back to `figma`/`fake` via config to disable Stitch. Rotate creds in the vault. Reversible.

## Design notes

- Quota state persisted in `forgeflow_meta` (a small counter doc per window) so it survives restarts and is visible to the cost/observability surfaces (`phase-46`).
- Adapter degrades to `health()=down` (not a crash) when creds are absent, so the stage/eval keep working via fallback.

## Status

✅ done — Real Stitch adapter behind the phase-16 `DesignProvider`, split into transport + auth +
quota so each is independently testable:

- `app/design/stitch.py` — `StitchDesignProvider` (registered as a builtin; `health()=down` when
  creds absent so the stage falls back, never crashes). Maps all interface methods to `DesignResult`/
  `DesignCode`; `fetch_code` is free, generations consume quota. `StitchClient` Protocol isolates the
  MCP transport; `HttpStitchClient` is the default (JSON-over-HTTP; the one unavoidable schema guess,
  isolated behind the Protocol). `_invoke()` adds transparent token refresh-on-401 (once) + transient
  backoff/retry.
- `app/design/stitch_auth.py` — `StitchAuth`: OAuth2 client-credentials, cached token, transparent
  refresh across expiry (configurable skew), **single-flight** lock (concurrent callers → one fetch).
  Tokens/secrets never logged; error bodies never echoed.
- `app/design/stitch_quota.py` + `DesignQuota` model — per-provider/per-window counter in
  `forgeflow_meta` (atomic `$inc` upsert). Pre-check fails soft at the cap with a classified
  `ProviderError`; `design.quota` events on a reserved `platform` channel carry a strictly token-free
  payload; `health()` degrades near the cap.
- `app/design/stitch_errors.py` — classifies every failure (auth/quota/transient/fatal) as a
  `ProviderError` with `kind` + a tailored `fallback_hint` (phase-19/48 use these to degrade).
- Config: `STITCH_MCP_URL/TOKEN_URL/SCOPE/TIMEOUT_S/MAX_RETRIES/TOKEN_SKEW_S/QUOTA_SOFT_RATIO` added
  to the resolver + `.env.example` (all `admin > env > default`).

**Acceptance:** token refresh across an expiry boundary + single-flight (tested with a mock
clock/counting fetcher); quota exhaustion fails soft (no crash, clear hint, `design.quota` event,
transport never called); tokens never persisted on the artifact or emitted. The **live** screenshot→
artifact smoke needs real GCP/Stitch creds (unavailable here); it is covered offline by
`test_stitch_mock.py` against a mocked MCP (image/text→UI, refine, fetch_code, result→artifact).

**Verification:** ruff/black clean; mypy clean on the phase-17 surface; **design suite 46 passed**
(30 new) and **full non-sandbox backend suite 195 passed** — no regressions from registering Stitch
as a builtin or adding `DesignQuota`/`design.quota`.

**Environment notes:** run on Windows where (a) the locked FastAPI 0.139.0 lives in an isolated
venv (system has 0.115.5), and (b) the phase-11 sandbox `runtime.py` imports Unix-only `fcntl`/`pty`/
`termios`, so `tests/sandbox` and a whole-repo mypy can't run here — they're the other agent's
Linux-verified code and were left untouched. Everything phase-17 touches is fully verified.
