# Phase 33 — Infra analyzer

## Title & ID

- **ID:** `phase-33`
- **Title:** Infra analyzer
- **Epic:** Epic 7 — Infra Analysis & Deployment
- **Milestone:** M4

## Goal

Classify a project's deployment needs from its workspace and produce an `infra_plan`: static FE / persistent BE / DB, recommended targets, and required env/secrets.

## Context

Beginner-safe infra is a secondary differentiator (D1). The analyzer's plan drives dual-mode deploy (D11: FE→Vercel, BE→Render, DB→Atlas). It must correctly handle FE-only, FE+BE, and FE+BE+DB shapes.

## Depends on

- **Phase IDs:** `phase-12`
- **External:** —

### Prerequisite check

- `phase-12` ✅ (read workspace files). A generated app (or skeleton) present.

## Detailed tasks

1. **Analyzer (`app/deploy/analyzer.py`).** Inspect the workspace (mostly static analysis, optional Haiku summary):
   - FE build target (Vite SPA → static output dir).
   - BE presence (Express server, entrypoint, port).
   - DB usage (Mongoose connection, `MONGODB_URI` reference).
   - Required env/secrets (scan `.env.example`, `process.env.*`, `import.meta.env.*`).
   - Produce `InfraPlan = {fe:{type:static, build_cmd, output_dir, env[]}, be?:{type:persistent, start_cmd, port, env[]}, db?:{type:mongo, provider:atlas|byo}, required_secrets[]}`.
2. **Artifact.** Persist as an `infra_plan` artifact (§6), versioned.
3. **Classification rules.** Deterministic rules + confidence; ambiguous cases flagged for user confirmation (D12 human-in-the-loop).
4. **API.** `POST /projects/{id}/infra/analyze`, `GET …/infra/plan`.

## Out of scope for this phase

- Actual deployment (`phase-35/37`), secrets vault (`phase-34`), DB provisioning (`phase-36`), topology UI (`phase-38`). Plan only.

## Acceptance criteria

- Correct `infra_plan` for **FE-only**, **FE+BE**, and **FE+BE+DB** sample projects (targets + required env identified).
- Ambiguous projects are flagged rather than guessed silently.
- Plan persisted as a versioned artifact.

## Tests to write

- `backend/tests/deploy/test_analyzer_fe_only.py`, `test_analyzer_fe_be.py`, `test_analyzer_fe_be_db.py` — fixtures → expected plans.
- `backend/tests/deploy/test_analyzer_env_scan.py` — required env/secrets extracted from FE+BE.

## Manual verification steps

1. Analyze the skeleton (FE+BE+DB) → plan shows Vercel(static FE) + Render(BE) + Atlas(DB) + `MONGODB_URI`/`VITE_API_BASE_URL`.
2. Analyze an FE-only sample → plan omits BE/DB.

## Rollback / cleanup notes

- Remove `infra_plan` artifacts for test projects. Reversible.

## Design notes

- **Static-analysis-first** (cheap, deterministic); a Haiku summary is only a fallback for ambiguity — keeps cost down and results reproducible for eval.

## Status

⬜ todo
