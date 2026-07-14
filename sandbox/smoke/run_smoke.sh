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

echo "SMOKE PASS: ${IMAGE} builds, runs as non-root, and passes the Node + Playwright check."
