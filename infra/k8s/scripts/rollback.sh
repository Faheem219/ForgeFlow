#!/usr/bin/env bash
# Roll the API Deployment back — to the previous revision, or to a specific one.
#
#   ./rollback.sh              # undo the last rollout
#   ./rollback.sh 3            # go to revision 3
#   ./rollback.sh --history    # just show what is available
set -euo pipefail

NAMESPACE="${NAMESPACE:-forgeflow}"
DEPLOYMENT="${DEPLOYMENT:-forgeflow-api}"

if [[ "${1:-}" == "--history" ]]; then
	kubectl -n "${NAMESPACE}" rollout history "deploy/${DEPLOYMENT}"
	exit 0
fi

echo "==> revision history (CHANGE-CAUSE is why each one exists)"
kubectl -n "${NAMESPACE}" rollout history "deploy/${DEPLOYMENT}"

if [[ -n "${1:-}" ]]; then
	echo "==> rolling back to revision $1"
	kubectl -n "${NAMESPACE}" rollout undo "deploy/${DEPLOYMENT}" --to-revision="$1"
else
	echo "==> rolling back to the previous revision"
	kubectl -n "${NAMESPACE}" rollout undo "deploy/${DEPLOYMENT}"
fi

kubectl -n "${NAMESPACE}" rollout status "deploy/${DEPLOYMENT}" --timeout=200s

echo "==> now serving"
kubectl -n "${NAMESPACE}" get deploy "${DEPLOYMENT}" \
	-o custom-columns='NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image,READY:.status.readyReplicas'
