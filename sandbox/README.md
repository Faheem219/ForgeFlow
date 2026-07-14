# sandbox

The per-project execution sandbox image where **untrusted generated code runs**. The control plane
never execs generated code directly (§7); it only runs it inside a container built from this image.

## Contents (`Dockerfile`)

- `node:20-bookworm-slim` base, `git` + `curl` + `tini`, `pnpm` via corepack.
- Playwright **chromium** (+ OS deps) pre-installed to `/ms-playwright` (shared, world-readable);
  the `playwright` package lives at `/opt/pw` and is importable via `NODE_PATH` (CommonJS).
- Non-root user **`app`** (uid 1000); workspace at **`/workspace`**.
- No secrets are baked in.

`entrypoint.sh` keeps the container alive for the manager to `docker exec` into (filesystem / exec /
preview) and **never auto-runs** untrusted code. `tini` is PID 1 for signal forwarding + zombie
reaping.

## Runtime isolation is the manager's job (phase-11)

This image is intentionally reusable across environments. The hard isolation — **no host network,
dropped capabilities, and CPU / memory / PID limits** — is applied by the sandbox manager at
`docker run` time (phase-11), not baked into the image.

## Build & smoke

```bash
make sandbox-build     # docker build -t $SANDBOX_IMAGE sandbox/
make sandbox-smoke     # build + run the Node app + Playwright chromium check in-container
docker run --rm forgeflow-sandbox:latest whoami   # -> app
```

`smoke/run_smoke.sh` is CI-invokable (see the `sandbox` job in `.github/workflows/ci.yml`): it builds
the image, asserts it runs as `app`, then starts the sample Node server (`smoke/server.cjs`) and
drives it with a Playwright chromium test (`smoke/browser-check.cjs`), all inside the container.

## Rollback

`docker rmi forgeflow-sandbox:latest` and prune build cache. No persistent state.
