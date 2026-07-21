#!/usr/bin/env bash
# Guard: no real credential may be committed (phase-47, docs/threat-model.md §5.3).
#
# Complements the gitleaks CI job with checks specific to ForgeFlow's own conventions:
#   1. `.env` must never be tracked by git — only `.env.example`.
#   2. Secret-shaped keys in `.env.example` must ship blank.
#   3. No provider-token literal may appear in application source.
#
# Deliberately narrow so it stays signal, not noise: it scans shipped code, not test fixtures
# (which legitimately contain synthetic credential-shaped values).
set -euo pipefail

cd "$(dirname "$0")/../.." # repo root

fail=0

# --- 1. .env must not be tracked -------------------------------------------------------------
tracked_env="$(git ls-files | grep -E '(^|/)\.env$' || true)"
if [[ -n "$tracked_env" ]]; then
  echo "FAIL: .env is tracked by git:"
  echo "$tracked_env"
  fail=1
fi

# --- 2. .env.example must ship blank secrets --------------------------------------------------
if [[ -f .env.example ]]; then
  # A key is secret-shaped when its LAST underscore-segment is a credential word. This keeps
  # numeric tuning knobs (ANTHROPIC_MAX_OUTPUT_TOKENS, STITCH_TOKEN_SKEW_S) out of the sweep.
  populated="$(awk -F= '
    /^[[:space:]]*#/ || !/=/ { next }
    {
      key = $1
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
      value = $2
      sub(/#.*$/, "", value)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if (value == "") next
      if (key ~ /(TOKEN|SECRET|KEY|PASSWORD|URI|DSN)$/) {
        # Documented, self-describing dev placeholders and local credential-free defaults.
        if (value == "dev-insecure-change-me") next
        if (value ~ /^(mongodb|http):\/\/localhost/) next
        print key "=" value
      }
    }
  ' .env.example || true)"

  if [[ -n "$populated" ]]; then
    echo "FAIL: .env.example ships non-empty secret values:"
    echo "$populated"
    fail=1
  fi
fi

# --- 3. no provider-token literals in application source --------------------------------------
PATTERNS='sk-ant-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|rnd_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|mongodb\+srv://[^:[:space:]]+:[^@[:space:]]+@'

for dir in backend/app frontend/src; do
  [[ -d "$dir" ]] || continue
  hits="$(grep -rnEI "$PATTERNS" "$dir" || true)"
  if [[ -n "$hits" ]]; then
    echo "FAIL: possible committed credential in $dir:"
    echo "$hits"
    fail=1
  fi
done

if [[ "$fail" -ne 0 ]]; then
  echo
  echo "See docs/hardening-checklist.md §3 (Secrets)."
  exit 1
fi

echo "OK: no committed secrets detected."
