# Moving to a new VM and/or a new API subdomain

Checklist for when the backend host IP changes, the API subdomain changes, or both.
Everything else (Vercel frontend, GHCR, Atlas, provider keys) stays as it is.

Assumes the new VM already has nginx installed and a certbot certificate for the subdomain
(`sudo certbot --nginx -d <subdomain>`), same as the first setup.

---

## 1. New IP — what to change

| Where | What |
|---|---|
| Dynu DNS | A record for the subdomain → new IP |
| OCI console | open ingress TCP **80** and **443** on the new instance's Security List / NSG |
| `infra/ansible/inventory.ini` | `ansible_host=<new-ip>` (and `ansible_ssh_private_key_file` if the key changed) |
| `~/.ssh/config` on your Mac | `HostName <new-ip>` under `Host forgeflow-vm` (and `IdentityFile` if the key changed) |
| GitHub → Secrets | `VM_HOST` = new IP |
| GitHub → Secrets | `VM_SSH_KNOWN_HOSTS` = the key lines from `ssh-keyscan <new-ip>` (drop the `#` lines) |
| GitHub → Secrets | `VM_SSH_KEY` — only if the private key changed |

## 2. New subdomain — what to change

| Where | What |
|---|---|
| `infra/ansible/group_vars/all.yml` | `forgeflow_domain: "<new-subdomain>"` |
| GitHub → Variables | `FORGEFLOW_DOMAIN` = new subdomain (no `https://`) |
| nginx on the VM | `server_name` + the certbot cert paths in the block below |
| Vercel | nothing — `VITE_API_BASE_URL` is built from `FORGEFLOW_DOMAIN` by CD; just redeploy |

`forgeflow_cors_origins` does **not** change (that is the frontend URL).

If previews are set up (DEPLOYMENT.md §2 g), they move with the subdomain too: Dynu wildcard on
the new hostname, a new wildcard cert for `*.preview.<new>` + `*.api.preview.<new>`, the preview
nginx block with the new `server_name`, and `forgeflow_preview_domain` in `group_vars/all.yml`.

## 3. Then, in order

```bash
# on your Mac
cd ~/Documents/Projects/ForgeFlow/infra/ansible
ansible forgeflow -m ping
ansible-playbook site.yml --skip-tags nginx          # nginx block is hand-managed, see §4
ansible-playbook site.yml --skip-tags nginx          # second run: changed=0

# sandbox image (CD does not build it)
cd ~/Documents/Projects/ForgeFlow
tar czC sandbox . | ssh forgeflow-vm 'docker build -t forgeflow-sandbox:latest -'
```

Then push to `main` (or Actions → CD → Run workflow) and check `https://<subdomain>/health`.

The DB is new, so seed the admin user again (creds come from `group_vars/vault.yml` via `.env`):

```bash
ssh forgeflow-vm 'cd /opt/forgeflow && docker compose --env-file .env -f docker-compose.prod.yml exec -T api python -m scripts.seed'
```

Log in → `/admin` → re-enter the LLM provider key (it lived in the old VM's DB).

---

## 4. nginx on the VM

### a. Map file — create once per VM

`/etc/nginx/conf.d/websocket-upgrade.conf`:

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
```

### b. 443 block — replace the one certbot generated

Change `forgeflow-backend.ddnsfree.com` (4 places) if the subdomain changed. Keep the
`# managed by Certbot` lines exactly as certbot wrote them on the new VM.

```nginx
server {
    server_name forgeflow-backend.ddnsfree.com;

    client_max_body_size 64m;

    location / {
        proxy_pass http://127.0.0.1:8000;

        proxy_http_version 1.1;
        proxy_set_header Upgrade           $http_upgrade;
        proxy_set_header Connection        $connection_upgrade;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_connect_timeout 10s;
        proxy_send_timeout    3600s;
        proxy_read_timeout    3600s;
        proxy_buffering off;
    }

    location /metrics {
        allow 127.0.0.1;
        allow ::1;
        deny all;
        proxy_pass http://127.0.0.1:8000/metrics;
    }

    listen 443 ssl; # managed by Certbot
    ssl_certificate /etc/letsencrypt/live/forgeflow-backend.ddnsfree.com/fullchain.pem; # managed by Certbot
    ssl_certificate_key /etc/letsencrypt/live/forgeflow-backend.ddnsfree.com/privkey.pem; # managed by Certbot
    include /etc/letsencrypt/options-ssl-nginx.conf; # managed by Certbot
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem; # managed by Certbot
}
```

### c. 80 block — leave as certbot wrote it

Only the subdomain changes. Do not add anything here.

```nginx
server {
    if ($host = forgeflow-backend.ddnsfree.com) {
        return 301 https://$host$request_uri;
    } # managed by Certbot


    listen 80;
    server_name forgeflow-backend.ddnsfree.com;
    return 404; # managed by Certbot


}
```

### d. Apply

```bash
sudo nginx -t && sudo systemctl reload nginx
```

Check WebSockets reach the app (`101`, `401` or `403` = good; `400`/`426` = headers missing):

```bash
curl -i -N -o /dev/null -w '%{http_code}\n' \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' -H "Sec-WebSocket-Key: $(head -c16 /dev/urandom | base64)" \
  https://forgeflow-backend.ddnsfree.com/ws/projects/test
```
