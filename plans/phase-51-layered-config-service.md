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

⬜ todo
