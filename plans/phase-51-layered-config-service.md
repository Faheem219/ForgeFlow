# Phase 51 — Layered configuration service & platform settings store

## Title & ID

- **ID:** `phase-51`
- **Title:** Layered configuration service & platform settings store
- **Epic:** Epic 12 — Admin & Configurability *(added per user directive)*
- **Milestone:** M5

## Goal

Make platform settings (model names, provider selection, base URLs, budgets, sandbox limits, repair thresholds, feature flags) **runtime-configurable** via a DB-backed store that overrides env, which overrides code defaults — precedence **admin panel > env > default** — with a typed admin API and cache invalidation.

## Context

Implements the **user directive**: *all settings (model name, provider, base URL, etc.) must be configurable via an admin dashboard, not only env; priority admin panel > env > default.* The resolver *seam* was built in `phase-01` (env + default layers) and every consumer already reads through it. This phase adds the **DB (admin) layer** at the front of the chain — so no consumer refactor is needed — plus the admin CRUD API. The dashboard UI is `phase-52`.

## Depends on

- **Phase IDs:** `phase-02` (`PlatformSetting` model), `phase-03` (`require_admin`), `phase-01` (resolver seam)
- **External:** —

### Prerequisite check

- `phase-01` ✅ (config resolver with ordered providers), `phase-02` ✅ (`PlatformSetting` collection), `phase-03` ✅ (`role=admin` + `require_admin`).

## Detailed tasks

1. **DB setting provider (`app/core/config_db.py`).** A `DbSettingProvider` reading `PlatformSetting` docs (`key → value`), **prepended** to the resolver chain so it wins over env/default. In-memory cache with TTL + explicit invalidation on writes (`config.reload()`); safe fallback to env/default if the DB is unavailable (never hard-fail config resolution).
2. **Config registry (`app/core/config_registry.py`).** A typed catalog of admin-editable keys with `{key, category, type, default, description, sensitive?, restart_required?}`. Categories: `models` (`MODEL_CODEGEN`, `MODEL_ROUTING`, provider base URL, provider name), `providers` (active design provider, Stitch/Figma toggles), `deploy` (default mode), `sandbox` (cpu/mem/pids/idle timeout), `budget` (per-project/global caps, ₹/token pricing), `repair` (`REPAIR_MAX_ITERATIONS`, `REPAIR_STALL_THRESHOLD`), `feature_flags`. **Sensitive** values (API keys) are managed via the vault (`phase-34`), not stored as plain settings — the registry references them but the dashboard writes them through the vault.
3. **Validation.** Each key validates on write (type/range/enum, e.g., model id in an allowed set or free-form per config, positive limits); invalid writes → `UserError` with detail. Some keys marked `restart_required` (surfaced to the admin).
4. **Admin API (`app/api/admin/config_router.py`, `require_admin`).** `GET /admin/config` (catalog + effective values + **source** per key: `db|env|default`, sensitive masked), `PUT /admin/config/{key}` (validate → upsert `PlatformSetting` → `config.reload()` → audit), `DELETE /admin/config/{key}` (revert to env/default), `GET /admin/config/effective` (resolved snapshot). Every change writes an audit entry (`updated_by`, before/after, ts).
5. **Consumer verification.** Confirm `MODEL_CODEGEN`/`MODEL_ROUTING` (`phase-20`), active design provider (`phase-16`), provider base URL/name, budgets (`phase-20/46`), sandbox limits (`phase-11`), and repair thresholds (`phase-31`) all resolve through the chain and reflect admin overrides live (no restart, except `restart_required` keys).
6. **`.env.example` sync.** Ensure every registry key has an env fallback documented.

## Out of scope for this phase

- The **admin UI** (`phase-52`). Per-user BYO credentials (that's the vault, `phase-34`). New settings beyond the registry.

## Acceptance criteria

- Setting a key via `PUT /admin/config/{key}` **overrides env**; deleting it **reverts to env/default** — precedence **admin > env > default** proven.
- `GET /admin/config` reports each key's **effective value + source**; sensitive values masked.
- Changing `MODEL_CODEGEN` (or design provider, budget cap, repair threshold, sandbox limit) via the API takes effect **without a restart** (cache invalidated); `restart_required` keys are flagged.
- Non-admins get 403 on all `/admin/config/*` routes.
- If the DB layer is unavailable, config still resolves from env/default (no hard failure).
- Every change is audited.

## Tests to write

- `backend/tests/config/test_precedence.py` — db > env > default; delete reverts.
- `backend/tests/config/test_reload_no_restart.py` — write → resolver reflects it immediately (e.g., model router returns the new id).
- `backend/tests/config/test_admin_authz.py` — `require_admin` enforced; non-admin 403.
- `backend/tests/config/test_validation.py` — invalid value rejected with detail; `restart_required` flagged.
- `backend/tests/config/test_db_unavailable_fallback.py` — DB down → env/default still resolve.
- `backend/tests/config/test_audit.py` — changes audited with before/after + actor.

## Manual verification steps

1. `PUT /admin/config/MODEL_ROUTING {value:"claude-haiku-4-5-20251001"}` → `GET /admin/config` shows source `db`; the router uses it immediately.
2. `DELETE /admin/config/MODEL_ROUTING` → source reverts to `env`/`default`.
3. As a non-admin → `/admin/config` → 403.
4. Stop Mongo briefly → config still resolves (env/default); restore → db layer resumes.

## Rollback / cleanup notes

- Delete `PlatformSetting` rows to revert all overrides to env/default (safe, reversible). Disable the DB provider via a bootstrap flag to fully bypass the admin layer if needed.

## Design notes

- **Why a new phase (not woven in):** the directive adds a genuinely new, independently-shippable capability (a config plane + admin API) with its own acceptance criteria — folding it into an existing phase would violate "don't expand a phase's scope." The *seam* was pre-built in `phase-01`/`phase-02`/`phase-03` so this phase is additive and low-risk.
- **Sensitive keys via the vault:** API keys/tokens are never stored as plain `PlatformSetting` values; the registry marks them `sensitive` and the dashboard routes them to the encrypted vault (`phase-34`), preserving §7 secret handling while still being "configurable from the admin panel."
- Precedence is strict and total: `admin(DB) > env > default`, matching the user's stated priority.

## Status

✅ done

**Implementation notes**

- **DB layer** — [config_db.py](../backend/app/core/config_db.py): `DbSettingProvider` is prepended to the resolver chain (`admin(DB) > env > default`). The crux: `ConfigResolver.get` is **synchronous** but reading a `PlatformSetting` is **async**, so the provider keeps a **sync-readable cache** that async code refreshes — `load()` at startup + after every admin write (→ visible immediately, no restart), `ensure_fresh(ttl)` on admin reads for out-of-band edits. **Fail-soft:** `load()` never raises; a DB outage leaves the last-known cache (empty at worst) so config still resolves from env/default. Wired into the app lifespan; `add_provider` made idempotent so it re-attaches cleanly after `reset_config()`.
- **Registry** — [config_registry.py](../backend/app/core/config_registry.py): the **allowlist** of 18 admin-editable keys across all 7 categories (`models`/`providers`/`deploy`/`sandbox`/`budget`/`repair`/`feature_flags` — added `repair` to the enum), each with `{type, description, sensitive, restart_required, choices, min/max, nullable}` and per-write coercion+validation. **Sensitive keys** (`anthropic_api_key`) are listed but read-only — a write is refused, pointing at the vault (§7). Type/range/enum/nullable/JSON validation returns `UserError` with detail.
- **Admin API** — [config_router.py](../backend/app/api/admin/config_router.py) (`require_admin`): `GET /admin/config` (catalog + effective value + **source** per key, sensitive masked), `GET /admin/config/effective`, `GET/PUT/DELETE /admin/config/{key}`. Writes go through [config_admin.py](../backend/app/core/config_admin.py): validate → upsert `PlatformSetting` → refresh the sync cache → **audit**. `DELETE` reverts to env/default.
- **Audit** — new `ConfigAudit` model (key, action, before, after, updated_by, ts); every update/delete is recorded, and a *rejected* write records nothing.
- **Consumers** verified: a `PUT model_routing` is reflected by `agents.models.route()` live; the same mechanism covers design provider, budgets, sandbox limits and repair thresholds (all read through the resolver). All 18 registry keys already have `.env.example` fallbacks.

**Tests** — `tests/config/` (36): `test_precedence` (db > env > default, delete reverts, typed values), `test_reload_no_restart` (write live via the router, survives a fresh load), `test_admin_authz` (401/403 across every route + a static require_admin guard), `test_validation` (range/enum/bool/json/nullable/sensitive + API 400 with detail), `test_db_unavailable_fallback` (load never raises, keeps last-known cache, safe before Mongo is up), `test_audit` (before/after/actor; rejected write leaves no audit). The phase-47 authz matrix auto-discovered the new admin routes and confirms they're gated.

**Verification:** backend 868 passed, ruff/black/mypy clean. The 7 remaining failures are the pre-existing Docker-dependent repair tests (identical at HEAD). No frontend — the admin dashboard is `phase-52`.
