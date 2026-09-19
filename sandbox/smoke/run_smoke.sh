#!/usr/bin/env bash
# Build the sandbox image and smoke-test it: (1) it runs as non-root, (2) a sample Node app runs
# inside it, and (3) a Playwright chromium test drives that app — all in-container. CI-invokable;
# non-zero exit on any failure.
set -euo pipefail

IMAGE="${SANDBOX_IMAGE:-forgeflow-sandbox:latest}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SANDBOX_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "==> Building ${IMAGE}"
docker build -t "${IMAGE}" "${SANDBOX_DIR}"

echo "==> Asserting the container runs as non-root (app)"
who="$(docker run --rm "${IMAGE}" whoami)"
if [ "${who}" != "app" ]; then
  echo "FAIL: expected user 'app', got '${who}'" >&2
  exit 1
fi

echo "==> Asserting no obvious secrets are baked into the image"
if docker run --rm "${IMAGE}" sh -c 'env | grep -iE "secret|token|password|api_key" || true' | grep -q .; then
  echo "FAIL: suspicious secret-like environment variables present in the image" >&2
  exit 1
fi

echo "==> Asserting pnpm runs under the real runtime posture (read-only root, no network)"
# This is the regression check for `ENOENT … mkdir '/home/app/.cache/node/corepack/v1'`: corepack
# used to try to download pnpm into an unwritable $HOME on first use, so *every* package-manager
# command in the sandbox died. Reproduce the manager's posture exactly — read-only root, no network,
# a writable volume for $HOME — and require pnpm to work anyway (it is baked into the image).
home_vol="forgeflow-smoke-home-$$"
ws_vol="forgeflow-smoke-ws-$$"
mms_vol="forgeflow-smoke-mms-$$"
docker volume create "${home_vol}" >/dev/null
docker volume create "${ws_vol}" >/dev/null
trap 'docker volume rm -f "${home_vol}" "${ws_vol}" "${mms_vol}" >/dev/null 2>&1 || true' EXIT
# /workspace is a volume too, as the manager mounts it: `pnpm store path` probes its working
# directory for writability, which on the read-only root alone fails with EROFS.
if ! docker run --rm \
  --read-only \
  --network none \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v "${home_vol}:/home/app" \
  -v "${ws_vol}:/workspace" \
  "${IMAGE}" \
  bash -c 'set -e; pnpm --version; mkdir -p "$HOME/.cache/node/corepack"; pnpm store path >/dev/null'
then
  echo "FAIL: pnpm cannot run with a read-only root and no network" >&2
  exit 1
fi

echo "==> Asserting a read-only root without the \$HOME volume is reported, not silent"
if ! docker run --rm --read-only --network none --tmpfs /tmp "${IMAGE}" true 2>&1 \
  | grep -q 'not writable'; then
  echo "FAIL: expected a clear warning when \$HOME is unwritable" >&2
  exit 1
fi

# phase-59: the preview reclaims its fixed ports and kills dev-server process TREES using these.
# Assert them as the `app` user under a read-only root, which is how they actually run.
echo "==> Checking the process/port tools the preview reclaim depends on"
docker run --rm --read-only --network none --tmpfs /tmp \
  -v "${IMAGE_HOME_VOL:-forgeflow-smoke-home}:/home/app" \
  "${IMAGE}" \
  bash -c '
    set -euo pipefail
    for tool in ps pgrep pkill fuser; do
      command -v "$tool" >/dev/null || { echo "FAIL: $tool missing from the image" >&2; exit 1; }
    done
    # Not just present -- runnable unprivileged, which is the only way the sandbox ever calls them.
    ps -o pid= -p $$ >/dev/null
    pgrep -P 1 >/dev/null || true
    fuser -n tcp 5173 >/dev/null 2>&1 || true
    echo "process tools OK"
  '

# phase-65: generated-app tests run against a mongod baked into the image, because the sandbox has
# no network to download one. Both checks use the real posture, pids + memory caps included, since
# mongod's threads count against the pids limit and its cache against the memory limit.
echo "==> Asserting the baked mongod runs under the real runtime posture (no network)"
docker run --rm \
  --read-only \
  --network none \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /tmp \
  --pids-limit 256 \
  --memory 1g \
  -v "${home_vol}:/home/app" \
  "${IMAGE}" \
  bash -c '
    set -euo pipefail
    [ "${MONGOMS_SYSTEM_BINARY}" = /usr/local/bin/mongod ] || { echo "FAIL: MONGOMS_SYSTEM_BINARY" >&2; exit 1; }
    [ "${MONGOMS_RUNTIME_DOWNLOAD}" = false ] || { echo "FAIL: MONGOMS_RUNTIME_DOWNLOAD" >&2; exit 1; }
    mongod --version | head -1
    mkdir -p /tmp/db
    mongod --dbpath /tmp/db --bind_ip 127.0.0.1 --port 27999 --wiredTigerCacheSizeGB 0.25 \
      --logpath /tmp/mongod.log &
    pid=$!
    for _ in $(seq 1 60); do
      (exec 3<>/dev/tcp/127.0.0.1/27999) 2>/dev/null && { kill "${pid}"; wait "${pid}" || true; echo "mongod OK"; exit 0; }
      sleep 0.5
    done
    tail -n 20 /tmp/mongod.log >&2
    echo "FAIL: mongod did not accept connections" >&2
    exit 1
  '

echo "==> Asserting mongodb-memory-server uses the baked mongod with NO network (the reported failure)"
# Install the library WITH network, as a dependency install's egress window would, then run it with
# none: it must start the baked binary and never try to download. This is the exact path that used
# to end with a model reasoning it was "unable to download mongod".
docker volume create "${mms_vol}" >/dev/null
docker run --rm -v "${mms_vol}:/workspace" "${IMAGE}" \
  bash -c 'set -e; npm init -y >/dev/null; npm install --no-audit --no-fund --loglevel=error mongodb-memory-server-core@11 >/dev/null'
docker run --rm \
  --read-only \
  --network none \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /tmp \
  --pids-limit 256 \
  --memory 1g \
  -v "${home_vol}:/home/app" \
  -v "${mms_vol}:/workspace" \
  -v "${SCRIPT_DIR}:/smoke:ro" \
  "${IMAGE}" \
  node /smoke/mongo-check.cjs

echo "==> Running the sample Node app + Playwright chromium test in-container"
docker run --rm \
  -v "${SCRIPT_DIR}:/workspace/smoke:ro" \
  "${IMAGE}" \
  bash -c '
    set -euo pipefail
    node /workspace/smoke/server.cjs &
    server_pid=$!
    trap "kill ${server_pid} 2>/dev/null || true" EXIT
    for _ in $(seq 1 40); do
      curl -sf http://127.0.0.1:3123/ >/dev/null && break
      sleep 0.5
    done
    node /workspace/smoke/browser-check.cjs
  '

echo "SMOKE PASS: ${IMAGE} builds, runs as non-root, and passes the Node + Playwright + offline MongoDB checks."
