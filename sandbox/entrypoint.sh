#!/usr/bin/env bash
# Minimal sandbox init. Two modes:
#   - With args  → exec them (e.g. `docker run … whoami`, or a command the manager passes).
#   - No args    → keep the container alive so the manager can `docker exec` into it for
#                  filesystem/exec/preview work. We NEVER auto-run generated (untrusted) code here.
set -euo pipefail

# Clean, prompt shutdown on SIGTERM/SIGINT (tini forwards the signal to us as PID-1 child).
terminate() {
  exit 0
}
trap terminate TERM INT

# Fail LOUDLY-but-not-fatally if $HOME is not writable. The root filesystem is read-only, so the
# package managers (corepack/pnpm/npm all write under $HOME) only work because the manager mounts a
# writable per-project volume there. Without it every `pnpm` call dies with an ENOENT/EROFS deep in
# corepack, which reads like a Node bug rather than a mount problem — so say it plainly, once.
probe="${HOME:-/home/app}/.forgeflow-writable"
if (: >"${probe}") 2>/dev/null; then
  rm -f "${probe}"
else
  echo "sandbox: WARNING \$HOME (${HOME:-/home/app}) is not writable — package managers will fail." >&2
fi

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

# Idle keep-alive. `sleep & wait` (rather than `sleep infinity`) so the trap can interrupt it.
while true; do
  sleep 3600 &
  wait "$!"
done
