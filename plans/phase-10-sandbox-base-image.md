# Phase 10 — Sandbox base image

## Title & ID

- **ID:** `phase-10`
- **Title:** Sandbox base image
- **Epic:** Epic 2 — Sandbox & Web IDE
- **Milestone:** M1

## Goal

A secure, buildable Docker image for running untrusted generated apps: Node 20 + pnpm + Playwright browsers + git + curl, non-root, hardened.

## Context

Implements D5/§4 sandbox image and the §7 security posture (generated code runs **only** in sandboxes). Every later execution phase (manager, exec, tests, preview, codegen tools) targets this image. Untrusted code never touches the control plane.

## Depends on

- **Phase IDs:** `phase-01`
- **External:** Docker (Colima) running.

### Prerequisite check

- `phase-01` ✅; `docker build` works locally.

## Detailed tasks

1. **`sandbox/Dockerfile`.** Base `node:20-bookworm-slim`; install `git`, `curl`, `pnpm` (via corepack), Playwright system deps + browsers (`npx playwright install --with-deps chromium`); create non-root user `app` (uid 1000) + workspace dir `/workspace` owned by it; drop to `USER app`; sensible `WORKDIR`, `ENV`, `PATH`.
2. **`sandbox/entrypoint.sh`.** Minimal init: sets up the workspace, keeps the container alive for exec/preview (no auto-run of untrusted code); handles signals for clean shutdown.
3. **Hardening (image-level).** No secrets baked in; minimal packages; pin versions; `.dockerignore`; document that runtime enforcement (no host net, dropped caps, cpu/mem/pids caps) is applied by the **manager** (`phase-11`), not the image.
4. **Smoke test (`sandbox/smoke/`).** A tiny Node app + one Playwright test; a script that builds the image, runs the sample, and asserts both succeed.
5. **Build ergonomics.** `make sandbox-build` (→ `docker build -t forgeflow-sandbox:latest sandbox/`); image tag from config (`SANDBOX_IMAGE`).

## Out of scope for this phase

- Container lifecycle/limits (`phase-11`), exec/terminal (`phase-13`), preview proxy (`phase-15`), the app skeleton contents (`phase-22`). This phase is **just the image + smoke**.

## Acceptance criteria

- `make sandbox-build` produces `forgeflow-sandbox:latest`.
- The smoke script runs a sample Node app **and** a Playwright (chromium) test inside the container, both passing.
- Container runs as **non-root** (`whoami` = `app`); image contains no secrets.

## Tests to write

- `sandbox/smoke/run_smoke.sh` (CI-invokable) — build + run sample + Playwright; non-zero exit on failure.
- CI job asserting the image builds and smoke passes.

## Manual verification steps

1. `make sandbox-build` → image builds.
2. `docker run --rm forgeflow-sandbox:latest whoami` → `app`.
3. `bash sandbox/smoke/run_smoke.sh` → sample app + Playwright pass.

## Rollback / cleanup notes

- `docker rmi forgeflow-sandbox:latest` and prune build cache. No persistent state. Reversible.

## Design notes

- **Chromium-only** Playwright to keep the image lean (adequate for E2E of generated apps); more browsers can be added later if a spec needs them (out of scope).
- Runtime isolation is intentionally the manager's job so the image stays reusable across environments.

## Status

⬜ todo
