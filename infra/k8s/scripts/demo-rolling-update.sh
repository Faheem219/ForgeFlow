#!/usr/bin/env bash
# The Step-3 demonstration: a rolling update that drops no requests, then a deliberately broken
# release that is caught and rolled back automatically.
#
# It measures rather than asserts. A rolling update that "looked fine" proves nothing — the whole
# claim is *zero dropped requests*, so this hammers /health from a pod inside the cluster for the
# whole duration and counts the failures. If maxUnavailable were not 0, or the readiness probe were
# missing, that counter would come back non-zero and the demo would have found a real bug.
#
# Probing from INSIDE the cluster (a curl pod hitting the Service) rather than through the Ingress
# is deliberate: it isolates what is being demonstrated (Deployment rollout + Service endpoint
# churn) from whatever the ingress controller happens to be doing.
#
# Usage:
#   ./demo-rolling-update.sh <good-image-v1> <good-image-v2> [broken-image]
#
# Example (tags your CD pipeline already pushed):
#   ./demo-rolling-update.sh \
#       ghcr.io/faheem219/forgeflow-api:v1 \
#       ghcr.io/faheem219/forgeflow-api:v2 \
#       ghcr.io/faheem219/forgeflow-api:this-tag-does-not-exist
set -euo pipefail

NAMESPACE="${NAMESPACE:-forgeflow}"
DEPLOYMENT="${DEPLOYMENT:-forgeflow-api}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

V1="${1:?usage: $0 <good-image-v1> <good-image-v2> [broken-image]}"
V2="${2:?usage: $0 <good-image-v1> <good-image-v2> [broken-image]}"
BROKEN="${3:-ghcr.io/faheem219/forgeflow-api:deliberately-nonexistent}"

PROBE_POD="rollout-probe"

banner() { printf '\n\033[1;36m=== %s ===\033[0m\n' "$*"; }

cleanup() {
	kubectl -n "${NAMESPACE}" delete pod "${PROBE_POD}" --ignore-not-found --wait=false >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ------------------------------------------------------------------ 1. baseline
banner "1. Baseline — settle on ${V1}"
kubectl -n "${NAMESPACE}" set image "deploy/${DEPLOYMENT}" "api=${V1}"
kubectl -n "${NAMESPACE}" annotate "deploy/${DEPLOYMENT}" \
	"kubernetes.io/change-cause=baseline ${V1}" --overwrite >/dev/null
kubectl -n "${NAMESPACE}" rollout status "deploy/${DEPLOYMENT}" --timeout=240s
kubectl -n "${NAMESPACE}" get pods -l app.kubernetes.io/component=api

# ------------------------------------------------------------------ 2. continuous probe
banner "2. Start the availability probe (in-cluster, hits the Service)"
kubectl -n "${NAMESPACE}" delete pod "${PROBE_POD}" --ignore-not-found >/dev/null 2>&1 || true
# `|| true` on the curl so one failure does not end the loop — we want to COUNT failures, and
# `set -e` inside the probe shell would stop at the first one and under-report.
#
# shellcheck disable=SC2016
# Single quotes are intentional: $ok/$fail must be expanded by the shell INSIDE the probe pod, not
# by this one. Double-quoting would interpolate empty local values and the tally would always read 0.
kubectl -n "${NAMESPACE}" run "${PROBE_POD}" --image=curlimages/curl:latest --restart=Never -- \
	sh -c 'ok=0; fail=0;
	       while true; do
	         if curl -sf -m 2 -o /dev/null http://forgeflow-api/health; then
	           ok=$((ok+1));
	         else
	           fail=$((fail+1)); echo "MISS at $(date -u +%H:%M:%S)";
	         fi
	         echo "ok=$ok fail=$fail" > /tmp/tally
	         sleep 0.2
	       done' >/dev/null
kubectl -n "${NAMESPACE}" wait --for=condition=Ready "pod/${PROBE_POD}" --timeout=120s
sleep 5

# ------------------------------------------------------------------ 3. the rolling update
banner "3. Rolling update ${V1} -> ${V2} (watch pods surge, never dip below 2 Ready)"
kubectl -n "${NAMESPACE}" set image "deploy/${DEPLOYMENT}" "api=${V2}"
kubectl -n "${NAMESPACE}" annotate "deploy/${DEPLOYMENT}" \
	"kubernetes.io/change-cause=rolling update to ${V2}" --overwrite >/dev/null

# Snapshot pod state DURING the rollout — this is the screenshot worth taking: old and new pods
# coexisting, with the old one only terminating after the new one reports Ready.
( for _ in $(seq 1 12); do
	kubectl -n "${NAMESPACE}" get pods -l app.kubernetes.io/component=api \
		--no-headers -o custom-columns='POD:.metadata.name,STATUS:.status.phase,READY:.status.containerStatuses[0].ready,IMAGE:.spec.containers[0].image' 2>/dev/null \
		| sed 's/^/    /'
	echo "    ----"
	sleep 4
done ) &
WATCHER=$!

kubectl -n "${NAMESPACE}" rollout status "deploy/${DEPLOYMENT}" --timeout=240s
wait "${WATCHER}" 2>/dev/null || true

sleep 5
banner "4. Availability during the rolling update"
kubectl -n "${NAMESPACE}" exec "${PROBE_POD}" -- cat /tmp/tally
echo "    ^ fail=0 is the claim: maxUnavailable=0 + a readiness probe means no request"
echo "      ever reached a pod that was not ready to serve it."

# ------------------------------------------------------------------ 5. the broken release
banner "5. Now deploy a BROKEN image (${BROKEN}) — expect automatic rollback"
set +e
"${HERE}/rolling-update.sh" "${BROKEN}" "demo: deliberately broken release"
ROLLOUT_RC=$?
set -e
echo "    rolling-update.sh exited ${ROLLOUT_RC} (non-zero = it caught the failure and rolled back)"

banner "6. Availability across the FAILED release + rollback"
kubectl -n "${NAMESPACE}" exec "${PROBE_POD}" -- cat /tmp/tally
echo "    ^ still fail=0: maxUnavailable=0 means the broken pod never became Ready, so it was"
echo "      never added to the Service endpoints and never received a single request."

banner "7. Revision history"
kubectl -n "${NAMESPACE}" rollout history "deploy/${DEPLOYMENT}"

banner "8. Final state — should be back on ${V2}"
kubectl -n "${NAMESPACE}" get deploy "${DEPLOYMENT}" \
	-o custom-columns='NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image,READY:.status.readyReplicas,DESIRED:.spec.replicas'
kubectl -n "${NAMESPACE}" get pods -l app.kubernetes.io/component=api
