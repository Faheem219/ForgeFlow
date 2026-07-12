# Phase 52 — Admin dashboard UI

## Title & ID

- **ID:** `phase-52`
- **Title:** Admin dashboard UI
- **Epic:** Epic 12 — Admin & Configurability *(added per user directive)*
- **Milestone:** M5

## Goal

An admin-only dashboard to view and edit every platform setting from `phase-51` — model name/provider/base URL, budgets, sandbox limits, repair thresholds, feature flags — plus sensitive keys (routed to the vault), global cost/observability, and provider health.

## Context

The user-facing half of the **user directive**: *settings must be configurable via a website admin dashboard (priority admin panel > env > default).* Consumes the `phase-51` admin config API and reuses `require_admin` (`phase-03`). This is where a non-developer operator changes the model or swaps the design provider without touching env or redeploying.

## Depends on

- **Phase IDs:** `phase-51`, `phase-04` (shell/UI kit), `phase-03` (`RequireAdmin` guard)
- **External:** —

### Prerequisite check

- `phase-51` ✅ (admin config API + registry + effective/source reporting), `phase-03` ✅ (admin role + guard), `phase-04` ✅ (UI kit).

## Detailed tasks

1. **Admin area (`src/features/admin/`).** Route `/admin` wrapped in `RequireAdmin` (redirect/403 for non-admins). Sidebar sections: **Models**, **Providers**, **Deploy**, **Sandbox**, **Budget & Pricing**, **Repair Loop**, **Feature Flags**, **Credentials (platform)**, **Observability**.
2. **Settings forms (`SettingsForm`).** Render the config catalog from `GET /admin/config`: per key show label, description, **effective value + source badge** (`db`/`env`/`default`), editor by type (text/number/toggle/enum), `restart_required` badge, and a **Reset to default/env** action (`DELETE`). Save → `PUT /admin/config/{key}` → optimistic update + reload; validation errors surfaced inline. Sensitive keys show a masked, write-only input that routes to the vault (`phase-34`) — value never displayed.
3. **Model/provider quick-config.** First-class controls for `MODEL_CODEGEN`, `MODEL_ROUTING`, **provider name + base URL** (so an alternate Anthropic-compatible endpoint can be set), and the active **design provider** (Stitch/Figma) with a live `health()` indicator + quota readout (`phase-17`).
4. **Global observability.** Embed global cost/spend + budget caps (from `phase-46`) and the eval summary link (`phase-45`); alerts on cap.
5. **Audit view.** A read-only list of config changes (who/what/when, before→after) from the `phase-51` audit log.
6. **Safety UX.** Confirm on sensitive/`restart_required` changes; clearly warn when an override diverges from env.

## Out of scope for this phase

- The config resolution engine/API (`phase-51`). Per-user (non-admin) BYO credential UI (that's `phase-34`'s settings screen). User management CRUD (v1 promotes admins via DB/seed).

## Acceptance criteria

- An admin can **change the model name, provider, and base URL** (and budgets, sandbox limits, repair thresholds, flags) from the dashboard, and the change takes effect per `phase-51` (live unless `restart_required`).
- Each setting shows its **effective value + source** (`admin/db > env > default`); reset reverts correctly.
- **Non-admins cannot reach `/admin`** (guarded) and get 403 from the API.
- Sensitive keys are write-only (never displayed) and stored via the vault.
- Config changes appear in the audit view.

## Tests to write

- `frontend/src/features/admin/SettingsForm.test.tsx` — renders catalog with source badges; edit → PUT; reset → DELETE; validation errors inline (mock API).
- `frontend/src/features/admin/adminGuard.test.tsx` — non-admin redirected/blocked.
- `frontend/src/features/admin/SensitiveField.test.tsx` — masked, write-only; value never rendered.
- `frontend/src/features/admin/AuditView.test.tsx` — renders change history (mock).

## Manual verification steps

1. As admin → `/admin/Models` → change `MODEL_ROUTING` → save → source badge flips to `db`; next routed call uses it.
2. Reset the key → badge returns to `env`/`default`.
3. Set a provider **base URL** + name → persisted; used by the Anthropic client.
4. Swap design provider Stitch↔Figma → health indicator updates; design stage uses the new provider.
5. As a non-admin → `/admin` → blocked; direct API call → 403.

## Rollback / cleanup notes

- UI-only; all writes go through `phase-51` (revert by deleting overrides). Reversible.

## Design notes

- The **source badge** (`db`/`env`/`default`) makes the precedence *visible*, so an operator always knows whether a value comes from the admin panel, env, or code default — directly reflecting the user's `admin > env > default` requirement.
- Provider **base URL + name** are surfaced as first-class fields so the platform can point at an alternate/compatible endpoint without code changes (per "provider, base url, etc." in the directive).

## Status

⬜ todo
