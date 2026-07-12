# sandbox (placeholder)

The per-project execution sandbox image (Node 20 + pnpm + Playwright + git, non-root, hardened)
where untrusted generated code runs. Control plane **never** execs generated code directly (§7).

**Built in [phase-10](../plans/phase-10-sandbox-base-image.md)** (`Dockerfile` + `entrypoint.sh`)
and managed in [phase-11](../plans/phase-11-sandbox-manager.md). This placeholder only reserves
the directory.
