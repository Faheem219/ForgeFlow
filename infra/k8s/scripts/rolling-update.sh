#!/usr/bin/env bash
# Roll the API Deployment to a new image, watch it, and AUTO-ROLLBACK if it does not converge.
#
# The auto-rollback is the point. `kubectl set image` on its own returns the moment the API server
# accepts the patch — long before anything is actually running — so a script that stops there
# reports success for a deploy that is about to crash-loop. `kubectl rollout status` blocks until
# the Deployment's progressDeadlineSeconds (180s, set in 30-api.yaml) and returns non-zero if the
# new pods never become Ready; that non-zero is what we act on.
#
# Usage:
#   ./rolling-update.sh ghcr.io/faheem219/forgeflow-api:abc1234 "deploy: fix the thing"
set -euo pipefail

NAMESPACE="${NAMESPACE:-forgeflow}"
DEPLOYMENT="${DEPLOYMENT:-forgeflow-api}"
CONTAINER="${CONTAINER:-api}"

IMAGE="${1:-}"
CHANGE_CAUSE="${2:-manual rollout of ${IMAGE}}"

if [[ -z "${IMAGE}" ]]; then
	echo "usage: $0 <image> [change-cause]" >&2
	exit 2
fi

echo "==> current state"
kubectl -n "${NAMESPACE}" get deploy "${DEPLOYMENT}" \
	-o custom-columns='NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image,READY:.status.readyReplicas,DESIRED:.spec.replicas'

# Capture the revision we are leaving, so rollback targets a known-good point explicitly rather
# than trusting "undo" to pick the right one after a series of failed attempts.
PREVIOUS_REVISION="$(kubectl -n "${NAMESPACE}" rollout history deploy/"${DEPLOYMENT}" \
	| awk 'NF && $1 ~ /^[0-9]+$/ {rev=$1} END {print rev}')"
echo "==> current revision: ${PREVIOUS_REVISION:-<none>}"

echo "==> rolling to ${IMAGE}"
kubectl -n "${NAMESPACE}" set image "deploy/${DEPLOYMENT}" "${CONTAINER}=${IMAGE}"
kubectl -n "${NAMESPACE}" annotate "deploy/${DEPLOYMENT}" \
	"kubernetes.io/change-cause=${CHANGE_CAUSE}" --overwrite >/dev/null

echo "==> waiting for convergence (bounded by progressDeadlineSeconds)"
if kubectl -n "${NAMESPACE}" rollout status "deploy/${DEPLOYMENT}" --timeout=200s; then
	echo "==> rollout OK"
	kubectl -n "${NAMESPACE}" get pods -l app.kubernetes.io/component=api -o wide
	exit 0
fi

# --- failure path -------------------------------------------------------------------------------
echo "!!! rollout did NOT converge — rolling back" >&2

echo "--- why (pod state) ---" >&2
kubectl -n "${NAMESPACE}" get pods -l app.kubernetes.io/component=api >&2 || true
# The events are almost always the actual answer (ImagePullBackOff, OOMKilled, probe failures).
kubectl -n "${NAMESPACE}" get events --sort-by=.lastTimestamp | tail -20 >&2 || true

if [[ -n "${PREVIOUS_REVISION:-}" ]]; then
	kubectl -n "${NAMESPACE}" rollout undo "deploy/${DEPLOYMENT}" --to-revision="${PREVIOUS_REVISION}"
else
	kubectl -n "${NAMESPACE}" rollout undo "deploy/${DEPLOYMENT}"
fi

kubectl -n "${NAMESPACE}" rollout status "deploy/${DEPLOYMENT}" --timeout=200s
echo "==> rolled back to revision ${PREVIOUS_REVISION:-<previous>}" >&2
exit 1
