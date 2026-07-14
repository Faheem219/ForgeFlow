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

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

# Idle keep-alive. `sleep & wait` (rather than `sleep infinity`) so the trap can interrupt it.
while true; do
  sleep 3600 &
  wait "$!"
done
