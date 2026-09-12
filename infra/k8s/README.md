# ForgeFlow on Kubernetes

Assignment **Step 3**: Deployment + Service manifests, rolling updates, rollback.

```
infra/k8s/
├── base/
│   ├── 00-namespace.yaml   Namespace
│   ├── 10-config.yaml      ConfigMap + Secret (templated, no real values)
│   ├── 20-mongo.yaml       Headless Service + StatefulSet + PVC
│   ├── 30-api.yaml         Service + Deployment + PodDisruptionBudget
│   └── 40-ingress.yaml     Ingress (Traefik)
└── scripts/
    ├── rolling-update.sh       roll forward, auto-rollback if it doesn't converge
    ├── rollback.sh             roll back to previous or a named revision
    └── demo-rolling-update.sh  the measured zero-downtime + failure demo
```

---

## Read this first: what this cluster is, and is not

**docker-compose is the production runtime** (`infra/docker-compose.prod.yml`). This cluster is the
orchestration deliverable. That split is deliberate, not laziness, and the reason is specific:

ForgeFlow creates a **Docker container per project** for its sandbox, and the preview proxy routes
to those containers **by name** over Docker's embedded DNS on the `forgeflow-preview` bridge
network. A pod on the k3s CNI cannot resolve `forgeflow-sb-<id>`, and the generated apps reach
their database at `forgeflow-appdb` the same way. Mounting the Docker socket into the API pod
(easy, via `hostPath`) solves sandbox *creation* but not sandbox *routing*.

Making it work on k8s would mean rewriting preview routing to IP-based discovery — a change to the
application's architecture, not its deployment. So:

| | compose (prod) | k3s (this) |
|---|---|---|
| auth, projects, requirements, artifacts, config, realtime | ✅ | ✅ |
| `/health`, `/metrics` | ✅ | ✅ |
| build / preview / test / deploy (sandbox-backed) | ✅ | ❌ fails soft |

The app is built for this: it logs `sandbox reconcile skipped — Docker unavailable` at startup and
serves everything else normally.

---

## Deploy

```bash
kubectl apply -f base/00-namespace.yaml

# Do NOT apply 10-config.yaml with its placeholder values. Create the real Secret out of band:
kubectl -n forgeflow create secret generic forgeflow-secrets \
  --from-literal=SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=FERNET_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  --from-literal=ANTHROPIC_API_KEY=""

kubectl apply -f base/10-config.yaml   # ConfigMap only matters now; Secret already exists
kubectl apply -f base/20-mongo.yaml
kubectl apply -f base/30-api.yaml
kubectl -n forgeflow set image deploy/forgeflow-api api=ghcr.io/<owner>/forgeflow-api:<tag>

kubectl -n forgeflow rollout status deploy/forgeflow-api
```

Reach it without touching nginx's ports:

```bash
kubectl -n forgeflow port-forward svc/forgeflow-api 8080:80
curl localhost:8080/health
```

---

## Rolling update & rollback

```bash
# Roll forward. Waits for convergence and AUTO-ROLLS-BACK if the new pods never go Ready.
./scripts/rolling-update.sh ghcr.io/<owner>/forgeflow-api:abc1234 "fix: the thing"

./scripts/rollback.sh --history     # what revisions exist, and why
./scripts/rollback.sh               # undo the last rollout
./scripts/rollback.sh 3             # go to a specific revision
```

`kubectl set image` returns as soon as the API server accepts the patch — long before anything is
running. A script that stops there reports success for a deploy that is about to crash-loop.
`rollout status` blocks until `progressDeadlineSeconds` and exits non-zero if the new pods never
become Ready; **that non-zero is what the rollback hangs off.**

### The measured demo

```bash
./scripts/demo-rolling-update.sh <v1-image> <v2-image>
```

It hammers `/health` from inside the cluster for the whole run and **counts** failures, because
"zero downtime" is a claim that has to be measured, not asserted.

---

## Verified behaviour

Run on a real single-node k3s v1.31.4 cluster with the actual production image:

```
### rolling update v1 -> v2 (pod state sampled during the rollout)
forgeflow-api-59f66c4755-bk9rh   true    forgeflow-api:v1   Running
forgeflow-api-59f66c4755-hqn2z   true    forgeflow-api:v1   Running
forgeflow-api-5cbc6c4774-psp4r   false   forgeflow-api:v2   Running   <- new pod, not yet Ready
----
forgeflow-api-59f66c4755-hqn2z   true    forgeflow-api:v1   Running
forgeflow-api-5cbc6c4774-psp4r   true    forgeflow-api:v2   Running   <- Ready; only NOW does
forgeflow-api-5cbc6c4774-wkhs9   false   forgeflow-api:v2   Running      a v1 pod terminate

### broken release (nonexistent tag)
forgeflow-api-6ff44cfbc7-42kpt   0/1   ErrImagePull
error: deployment "forgeflow-api" exceeded its progress deadline
  -> rolling back
deployment.apps/forgeflow-api rolled back

### availability probe across the ENTIRE sequence
ok=807 fail=0
```

**807 requests, zero failures** — across the rolling update, the failed release *and* the rollback.
The broken pod never became Ready, so it was never added to the Service endpoints and never
received a single request.

---

## Why the manifests look the way they do

| Setting | Reason |
|---|---|
| `maxUnavailable: 0`, `maxSurge: 1` | Strictly additive: a new pod must be Ready before an old one goes. Capacity never dips below `replicas`. |
| `replicas: 2` | With one replica the surge settings have nothing to express and "no dropped requests" is untestable. |
| `minReadySeconds: 10` | Without it, a pod that passes one readiness check then crashes still lets the rollout march on — a broken image can replace every healthy pod before anyone notices. |
| `progressDeadlineSeconds` | Makes `rollout status` *fail* instead of hanging forever. This is what CD keys its rollback off. |
| Three separate probes | `startup` = "has it booted?", `readiness` = "should it get traffic?", `liveness` = "is it wedged?". Liveness is deliberately slacker than readiness — a tight liveness probe turns a transient slowdown into an outage. |
| `initContainer: wait-for-mongo` | The API **hangs in startup** if Mongo is unreachable (`init_db()` blocks before uvicorn finishes booting — verified against the real image). A startupProbe alone would just restart-loop it. |
| StatefulSet for Mongo | Stable identity + its own PVC. A Deployment with a shared PVC would run two mongods against one data dir; mongod holds an exclusive lock, so the new pod crash-loops. |
| No CPU limit on Mongo | CFS throttling a database produces latency spikes indistinguishable from application slowness. |
| `revisionHistoryLimit: 10` | `rollout undo --to-revision` silently has nothing to go to once history is trimmed. |

---

## Port conflict with nginx

k3s's default Traefik is exposed via a `LoadBalancer` Service, which klipper-lb satisfies by
binding host **:80 and :443** — ports your nginx already owns. They will fight.

Three ways out, in order of preference:

1. **`kubectl port-forward`** (what the demo scripts use). No ingress needed at all.
2. **Disable Traefik** — the Ansible `k3s` role does this by default (`--disable=traefik`).
3. **Move Traefik's ports** via a HelmChartConfig, then proxy to them from nginx.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Pods stuck `Init:0/1` | The init container is waiting for Mongo. `kubectl -n forgeflow logs <pod> -c wait-for-mongo` |
| `ErrImagePull` on a private GHCR image | Needs an imagePullSecret, or make the package public |
| PVC stuck `Pending` | No default StorageClass. k3s ships `local-path`; other clusters may need one named. |
| `kubectl drain` blocked | The PodDisruptionBudget, correctly — on a single node k8s cannot satisfy `minAvailable: 1` elsewhere. Use `--disable-eviction` or delete the PDB. |
| Rollout hangs forever | `progressDeadlineSeconds` not set, or set very high |
