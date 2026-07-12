# Phase 34 — Secrets & credential vault

## Title & ID

- **ID:** `phase-34`
- **Title:** Secrets & credential vault
- **Epic:** Epic 7 — Infra Analysis & Deployment
- **Milestone:** M4

## Goal

Secure storage and use of provider tokens (platform-owned + BYO): Fernet-encrypted at rest, decrypted only at call time, never logged or returned to the client; plus a settings UI to add BYO tokens.

## Context

Implements §7 secrets posture and D11 dual-mode (platform vs BYO creds). Every provider (Stitch/Figma/Vercel/Render/Mongo URI) reads credentials through this vault. Foundational for safe deployment.

## Depends on

- **Phase IDs:** `phase-03`
- **External:** `FERNET_KEY` (encryption key) in config.

### Prerequisite check

- `phase-03` ✅ (users/auth); `Credential` doc from `phase-02`; `FERNET_KEY` present.

## Detailed tasks

1. **Vault (`app/deploy/secrets.py`).** Fernet (`cryptography`) encrypt/decrypt; `put_credential(user_id, kind, secret, scope)` (encrypt + store), `get_credential(...)` (decrypt **in-process at call time only**), `list_kinds(user)` (metadata only — never values), `delete`. `kind ∈ {vercel|render|mongo_uri|stitch|figma}`, `scope ∈ {platform|byo}`.
2. **Resolution.** `resolve(kind, project/user)` → prefer **BYO** (user) over **platform** creds when present (D11); platform creds loaded from config/vault.
3. **Non-leakage.** Structured logging redaction; API responses expose only `{kind, scope, created_at, last4?}`; never the secret. A test asserts no secret appears in logs/responses.
4. **Wire providers.** Update Stitch/Figma auth (`phase-17/18`) and (later) Vercel/Render/Mongo to read via the vault (BYO-aware).
5. **Settings UI (`src/features/settings/Credentials.tsx`).** Add/replace/delete BYO tokens (masked input, write-only); shows kind/scope/created, never the value.

## Out of scope for this phase

- Deploy adapters (`phase-35`), DB provisioning (`phase-36`), the **admin platform-config** dashboard (`phase-52`) — this is per-user credentials; platform-wide config is separate.

## Acceptance criteria

- Secrets are **encrypted at rest** (ciphertext in Mongo), decrypted only at call time.
- Secrets are **never** logged or returned to the client (asserted by tests).
- BYO creds override platform creds when present; both modes usable.
- Settings UI adds/deletes BYO tokens without ever displaying stored values.

## Tests to write

- `backend/tests/deploy/test_vault_encryption.py` — round-trip; stored value is ciphertext.
- `backend/tests/deploy/test_vault_no_leak.py` — logs/responses contain no plaintext secret.
- `backend/tests/deploy/test_credential_resolution.py` — BYO > platform precedence.
- `frontend/src/features/settings/Credentials.test.tsx` — add/delete; value never rendered.

## Manual verification steps

1. Add a BYO Vercel token → DB shows ciphertext; API list shows metadata only.
2. Trigger a code path that uses it → works; grep logs → no plaintext.
3. Delete it → removed.

## Rollback / cleanup notes

- Deleting a `Credential` is immediate. **Rotating `FERNET_KEY` invalidates all stored secrets** — irreversible without re-entry; document clearly.

## Design notes

- Per-user BYO vs platform is the D11 mechanism; **admin platform-wide settings** (models/providers/base URLs) are a *separate* store (`PlatformSetting`, `phase-51`) — credentials and config are deliberately not the same surface.

## Status

⬜ todo
