# sandbox

The per-project execution sandbox image where **untrusted generated code runs**. The control plane
never execs generated code directly (§7); it only runs it inside a container built from this image.

## Contents (`Dockerfile`)

- `node:20-bookworm-slim` base, `git` + `curl` + `tini`, and a **baked** pnpm (see below).
- Playwright **chromium** (+ OS deps) pre-installed to `/ms-playwright` (shared, world-readable);
  the `playwright` package lives at `/opt/pw` and is importable via `NODE_PATH` (CommonJS).
- Non-root user **`app`** (uid 1000); workspace at **`/workspace`**.
- No secrets are baked in.

`entrypoint.sh` keeps the container alive for the manager to `docker exec` into (filesystem / exec /
preview) and **never auto-runs** untrusted code. `tini` is PID 1 for signal forwarding + zombie
reaping. It also warns loudly if `$HOME` is unwritable, because that failure otherwise surfaces as an
unreadable Node stack trace.

## pnpm is baked, and `$HOME` must be writable

`corepack enable` alone is **not** enough. Corepack downloads the requested pnpm on first use, into
`$HOME/.cache/node/corepack` — and the sandbox has a read-only root *and* no network, so the first
`pnpm` command in the sandbox died with:

```
Error: ENOENT: no such file or directory, mkdir '/home/app/.cache/node/corepack/v1'
```

Two things fix it, and both are load-bearing:

1. **Here:** `COREPACK_HOME=/opt/corepack` (world-readable, baked at build time) plus
   `corepack prepare pnpm@<PNPM_VERSION> --activate`, so pnpm needs neither a download nor a `$HOME`
   write to start. `PNPM_VERSION` tracks `packageManager` in `templates/app-skeleton/package.json`;
   `COREPACK_ENABLE_STRICT=0` makes a drifted `packageManager` field fall back to the baked pnpm
   instead of failing offline. The build asserts both (as root and as `app`) with
   `COREPACK_ENABLE_NETWORK=0`.
2. **In the manager:** a per-project named volume mounted at `/home/app`, because pnpm/npm still
   write caches and the pnpm store under `$HOME` at *install* time.

## Runtime isolation is the manager's job (phase-11)

This image is intentionally reusable across environments. The hard isolation — **no host network,
dropped capabilities, and CPU / memory / PID limits** — is applied by the sandbox manager at
`docker run` time (phase-11), not baked into the image. So are the two writable mounts
(`/workspace`, `/home/app`) that a read-only root requires.

Each sandbox lives on its **own `internal` docker network** (`forgeflow-sbnet-<project>`): no route
to the host, no internet, and no other sandbox on it. It is not `network_mode: none`, because docker
refuses to attach any network to a `none`-mode container — which would make the preview proxy
(phase-15) and the registry-egress window for installs (phase-39 / `app/sandbox/network.py`)
impossible. Those are still attached deliberately and temporarily, never at creation.

A sandbox created from an older image (or without the `/home/app` mount) is **recreated** by
`SandboxManager.ensure` on next use — the volumes, and therefore the generated code, survive. Rebuild
with `make sandbox-build`; no manual container surgery needed.

## Build & smoke

```bash
make sandbox-build     # docker build -t $SANDBOX_IMAGE sandbox/
make sandbox-smoke     # build + run the Node app + Playwright chromium check in-container
docker run --rm forgeflow-sandbox:latest whoami   # -> app
```

`smoke/run_smoke.sh` is CI-invokable (see the `sandbox` job in `.github/workflows/ci.yml`): it builds
the image, asserts it runs as `app`, runs **pnpm under the real runtime posture** (read-only root, no
network, `$HOME` on a volume — the regression check for the corepack `ENOENT` above), asserts the
unwritable-`$HOME` warning is emitted, then starts the sample Node server (`smoke/server.cjs`) and
drives it with a Playwright chromium test (`smoke/browser-check.cjs`), all inside the container.

## Rollback

`docker rmi forgeflow-sandbox:latest` and prune build cache. No persistent state.
