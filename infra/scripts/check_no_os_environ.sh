#!/usr/bin/env bash
# Guard: generated/control-plane code must read config through the layered resolver
# (backend/app/core/config.py), never os.environ / os.getenv directly.
# The config layer itself is the single allowed place to touch the environment.
set -euo pipefail

cd "$(dirname "$0")/../.." # repo root

ALLOW='backend/app/core/config\.py|backend/app/core/config_db\.py'

if [[ ! -d backend/app ]]; then
  echo "OK: backend/app not present yet; nothing to check."
  exit 0
fi

matches="$(grep -rnE 'os\.environ|os\.getenv' backend/app --include='*.py' | grep -vE "$ALLOW" || true)"

if [[ -n "$matches" ]]; then
  echo "FAIL: direct environment access outside the config layer:"
  echo "$matches"
  echo
  echo "Read settings via app.core.config.get_config().get(...) instead."
  exit 1
fi

echo "OK: no direct os.environ/os.getenv outside the config layer."
