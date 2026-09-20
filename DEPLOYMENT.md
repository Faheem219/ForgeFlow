# Deployment

Backend → Oracle ARM VM · Frontend → Vercel · CI/CD → GitHub Actions.
Full detail: [`docs/devops/README.md`](docs/devops/README.md).

---

## 1. Values to fill in

### `infra/ansible/inventory.ini` (copy from `inventory.example.ini`, gitignored)

| Key                              | Value                   |
| -------------------------------- | ----------------------- |
| `ansible_host`                 | VM public IP            |
| `ansible_user`                 | `ubuntu`              |
| `ansible_ssh_private_key_file` | path to your VM SSH key |

### `infra/ansible/group_vars/all.yml`

| Key                                   | Value                                                                                     |
| ------------------------------------- | ----------------------------------------------------------------------------------------- |
| `forgeflow_domain`                  | **required** — your API subdomain, no scheme (currently `CHANGE_ME.example.com`) |
| `forgeflow_cors_origins`            | your Vercel URL, e.g.`https://forgeflow.vercel.app`                                     |
| `forgeflow_image`                   | `ghcr.io/<owner>/forgeflow-api:latest`                                                  |
| `monitoring_grafana_admin_password` | anything but the default                                                                  |
| `forgeflow_api_port`                | `8000` — change only if another service holds it                                       |

Generated automatically on the VM, do not set: `SECRET_KEY`, `FERNET_KEY`, `DOCKER_GID`.
Provider keys (`ANTHROPIC_API_KEY`) are left blank on purpose — set them in `/admin` after first boot.

### `infra/ansible/group_vars/vault.yml` (create it, gitignored) — admin login

```yaml
vault_seed_admin_email: you@example.com
vault_seed_admin_password: <strong password>
vault_app_db_cluster_uri: mongodb+srv://<user>:<pass>@<cluster>.mongodb.net   # Atlas; optional
```

Ansible writes these into `/opt/forgeflow/.env`. The first two become `SEED_ADMIN_EMAIL` /
`SEED_ADMIN_PASSWORD` (the seed step in §5 creates the admin user from them). The third is the
DB that *deployed* generated apps use — it must be Atlas, not a docker name; leave it out and set
it in `/admin` instead if you prefer. Sandboxes (preview/tests) use the VM's own `appdb`
container automatically. Optional: encrypt with
`ansible-vault encrypt group_vars/vault.yml`, then add `--ask-vault-pass` to every playbook run.

### GitHub → Settings → Secrets and variables → Actions

**Secrets**

| Name                   | How to get it                                                |
| ---------------------- | ------------------------------------------------------------ |
| `VM_HOST`            | VM public IP                                                 |
| `VM_USER`            | `ubuntu`                                                   |
| `VM_SSH_KEY`         | full private key incl.`-----BEGIN/END-----`                |
| `VM_SSH_KNOWN_HOSTS` | `ssh-keyscan <vm-ip>`                                      |
| `VERCEL_TOKEN`       | Vercel → Account Settings → Tokens                         |
| `VERCEL_ORG_ID`      | `.vercel/project.json` after `vercel link`               |
| `VERCEL_PROJECT_ID`  | same file                                                    |
| `KUBECONFIG_B64`     | *optional (k8s workflow)* — `base64 -w0 ~/.kube/config` |

`GITHUB_TOKEN` is built in — no PAT needed for GHCR.

**Variables**

| Name                 | Value                        |
| -------------------- | ---------------------------- |
| `FORGEFLOW_DOMAIN` | API subdomain, no scheme     |
| `VM_SSH_PORT`      | *optional*, default `22` |

---

## 2. One-time setup

**a. OCI console** — open ingress TCP **80** and **443** on the instance's Security List/NSG.
Ansible cannot do this, and nothing reaches the VM without it.

**b. DNS + TLS** — point your subdomain at the VM, then on the VM:

```bash
sudo certbot --nginx -d <your-subdomain>
```

**c. Provision** (from macOS or WSL — no native Windows control node):

```bash
cd infra/ansible
cp inventory.example.ini inventory.ini      # then edit both files above
ansible-galaxy collection install -r requirements.yml
ansible forgeflow -m ping                   # check SSH first
ansible-playbook site.yml --check --diff    # dry run
ansible-playbook site.yml
```

Run it twice — the second run must report `changed=0`.

**d. GHCR access** — make the package public (Packages → forgeflow-api → Change visibility),
or add an imagePullSecret on the VM. Otherwise the VM can't pull.

**e. Vercel** — create the project from `frontend/`, then `vercel link` to get the two IDs.
`VITE_API_BASE_URL` is injected by the workflow from `FORGEFLOW_DOMAIN`; no need to set it in the dashboard.

**f. WebSocket headers** — if your existing nginx block already serves this subdomain, skip the
nginx role (`--skip-tags nginx`) and copy the three upgrade headers from
[`infra/nginx/forgeflow.conf.example`](infra/nginx/forgeflow.conf.example) into it.
Without them REST works and live build streaming silently never connects.

**g. Live previews** — without this the **Preview** button hands out `*.preview.localhost` URLs
that only resolve on a dev machine. Previews are served on a wildcard domain, one hostname per
project, so they need a wildcard DNS record and a wildcard certificate (Let's Encrypt issues those
over DNS-01 only). Pick `preview.<your-api-subdomain>` as the base; then:

1. **Dynu** → your hostname → enable **Wildcard** (so `*.preview.<subdomain>` resolves to the VM).
   Check from your Mac: `dig +short anything.preview.<subdomain>` prints the VM IP.
2. **Wildcard cert on the VM** — `acme.sh` talks to Dynu's API, so renewals are automatic
   (certbot's manual DNS-01 is not renewable). Dynu → **API Credentials** gives the ID + secret.
   ```bash
   sudo -i
   curl https://get.acme.sh | sh -s email=you@example.com
   export Dynu_ClientId='<client id>' Dynu_Secret='<secret>'
   ~/.acme.sh/acme.sh --issue --server letsencrypt --dns dns_dynu \
       -d '*.preview.<subdomain>' -d '*.api.preview.<subdomain>'
   mkdir -p /etc/ssl/forgeflow-preview
   ~/.acme.sh/acme.sh --install-cert -d '*.preview.<subdomain>' \
       --fullchain-file /etc/ssl/forgeflow-preview/fullchain.pem \
       --key-file       /etc/ssl/forgeflow-preview/privkey.pem \
       --reloadcmd      'systemctl reload nginx'
   ```
   Both wildcards are required: the frontend preview is `<id>.preview.…`, its API is
   `<id>.api.preview.…`, and a single-level wildcard does not cover the second.
3. **nginx** — add the preview server block from
   [`infra/nginx/forgeflow.conf.example`](infra/nginx/forgeflow.conf.example) (§ "Previews"),
   then `sudo nginx -t && sudo systemctl reload nginx`.
4. **Ansible** — set `forgeflow_preview_domain: "preview.<subdomain>"` in `group_vars/all.yml`
   and re-run `ansible-playbook site.yml --tags forgeflow` (renders `PREVIEW_BASE_DOMAIN` into
   `.env` and restarts the stack). Previews then open at `https://<project-id>.preview.<subdomain>`.

---

## 3. Deploy

```bash
git push origin main     # CI → CD → VM + Vercel
```

Or **Actions → CD → Run workflow** to redeploy a specific `image_tag`.

Deploy fails → previous image is restored automatically, production unchanged.

---

## 4. Verify

Every `ssh`/`scp` below assumes an alias in `~/.ssh/config` so you never repeat the key path
(the same key you gave Ansible in `inventory.ini`):

```
Host forgeflow-vm
    HostName <vm-ip>
    User ubuntu
    IdentityFile ~/path/to/your-oracle-key
    IdentitiesOnly yes
```

```bash
curl https://<your-subdomain>/health                    # {"status":"ok",...}
ssh forgeflow-vm 'systemctl status forgeflow'
ssh -N -L 3000:127.0.0.1:3000 forgeflow-vm              # Grafana → http://localhost:3000
```

Grafana is loopback-only on the VM by design, hence the tunnel. Log in as
`monitoring_grafana_admin_user` / `monitoring_grafana_admin_password` from `group_vars/all.yml`;
`Ctrl-C` closes the tunnel.

---

## 5. What you still have to do

CI on `main` is fixed (was red: `mypy` errors, 2 failing tests, Prettier, sandbox smoke).
Remaining steps, in order:

1. **Check CI is green** — GitHub → Actions → latest **CI** run on `main`. Red? Open the failing
   job; nothing deploys until it passes.
2. **Fill in the values** in §1 (inventory, `group_vars/all.yml`, GitHub secrets + variables).
3. **One-time setup** in §2 (a → f): OCI ports, DNS + certbot, Ansible, GHCR public, Vercel link.
4. **Build the sandbox image on the VM** — CD does not do this, and without it no project can
   build or test. Rebuild whenever `sandbox/` changes (uses the `forgeflow-vm` alias from §4;
   streams the build context over SSH, so nothing is copied onto the VM's disk):
   ```bash
   tar czC sandbox . | ssh forgeflow-vm 'docker build -t forgeflow-sandbox:latest -'
   ssh forgeflow-vm 'docker image ls forgeflow-sandbox'     # confirm the tag exists
   ```
   The first build takes a while (Playwright + browsers); rebuilds hit the layer cache.
5. **Deploy** — push to `main` (§3). Watch **Actions → CD** go green.
6. **Seed the admin user** — CD does not do this either. Reads the creds from `.env` (§1):
   ```bash
   ssh forgeflow-vm 'cd /opt/forgeflow && docker compose --env-file .env -f docker-compose.prod.yml exec -T api python -m scripts.seed'
   ```
   Prints `created admin (<email>)`; `already exists` on re-runs; `skipped` means the two
   `SEED_ADMIN_*` lines in `/opt/forgeflow/.env` are blank — add `vault.yml` and re-run
   `ansible-playbook site.yml --tags forgeflow`.
7. **Verify** — §4, then log in with those creds → `/admin` → set your LLM provider key.

---

## Optional

| Want                  | Do                                                                    |
| --------------------- | --------------------------------------------------------------------- |
| Kubernetes demo       | `make k8s-apply`, then `make k8s-demo V1=<img> V2=<img>`          |
| Skip k3s / monitoring | `ansible-playbook site.yml --skip-tags k3s,monitoring`              |
| Preview subdomains    | §2 g                                                                |
| Lint everything       | `make devops-lint`                                                  |
