# ForgeFlow — VM provisioning (Ansible)

Configuration management for the Oracle ARM VM that runs the ForgeFlow backend.
Assignment **Step 2**: install packages, create users, manage files.

---

## Control node setup

Ansible has **no native Windows control node**. Both of these work identically — the playbook is
plain POSIX and does not care which you use.

### macOS

```bash
brew install ansible
cd infra/ansible
ansible-galaxy collection install -r requirements.yml
```

### Windows (via WSL)

```powershell
wsl --install -d Ubuntu      # once, if you don't have WSL yet
wsl
```

then inside WSL:

```bash
sudo apt update && sudo apt install -y pipx
pipx install --include-deps ansible
pipx ensurepath && exec $SHELL

# Work from the WSL mount of the repo, NOT /mnt/c, if you can avoid it:
# ansible refuses to read an ansible.cfg from a world-writable directory, and
# everything under /mnt/c is world-writable by default.
cd ~ && git clone git@github.com:Faheem219/ForgeFlow.git
cd ForgeFlow/infra/ansible
ansible-galaxy collection install -r requirements.yml
```

> **The /mnt/c gotcha.** If you run from `/mnt/c/...` you will see
> `Ansible is being run in a world writable directory, ignoring it as an ansible.cfg source`
> and the playbook will silently use defaults instead of `ansible.cfg` (wrong inventory, no
> pipelining, host key checking off). Either clone into the WSL filesystem as above, or add
> `metadata,umask=022` to that drive's entry in `/etc/wsl.conf` and restart WSL.

---

## Configure

```bash
cp inventory.example.ini inventory.ini    # gitignored — holds your VM address
$EDITOR inventory.ini                     # ansible_host, ansible_user, key path
$EDITOR group_vars/all.yml                # forgeflow_domain is REQUIRED
```

The playbook refuses to start while `forgeflow_domain` is still the placeholder — better a
five-second failure than one after Docker is installed and half the box is configured.

Check connectivity before anything else:

```bash
ansible forgeflow -m ping
```

---

## Run

```bash
ansible-playbook site.yml --check --diff   # dry run: shows what WOULD change
ansible-playbook site.yml                  # for real
ansible-playbook site.yml --tags nginx     # just one part
ansible-playbook site.yml --skip-tags k3s,monitoring
```

**Run it twice.** The second run must report `changed=0`. That is the entire promise of
configuration management, and any task that reports *changed* on a no-op run is a bug in this
playbook — not a quirk.

Tags: `common` `docker` `forgeflow` `nginx` `k3s` `monitoring` `firewall`.

---

## What each role does

| Role | Responsibility | Notes |
|---|---|---|
| `common` | apt packages, timezone, the `forgeflow` system user, swap, unattended security upgrades | The user is shell-less; nothing should log in as it |
| `docker` | Docker CE + Compose v2 from Docker's apt repo, daemon config, the `forgeflow-preview` network | Discovers the host docker **gid** and passes it on — it differs per image |
| `forgeflow` | `/opt/forgeflow` tree, `.env`, compose + Caddy files, systemd units, nightly Mongo backup | Does **not** start the stack — that is CD's job |
| `nginx` | One additive site file with the WebSocket headers | Never touches `nginx.conf` or your existing site |
| `k3s` | Single-node k3s + the manifests, for the orchestration demo | Traefik disabled (it would fight nginx for :80/:443) |
| `monitoring` | Prometheus, Grafana, node-exporter, cAdvisor, blackbox | Does start its stack — monitoring must not wait for a deploy |
| `firewall` | ufw, and defusing Oracle's pre-baked iptables REJECT rules | Runs **last**, so a mistake cannot strand the play |

---

## Secrets

`SECRET_KEY` and `FERNET_KEY` are generated **on the VM on first run** and then read back on every
subsequent run. This matters more than it looks:

> Rotating `FERNET_KEY` is **unrecoverable**. It encrypts the credential vault, so a new key turns
> every stored provider token into undecryptable bytes. The read-back is what stops a re-run of
> this playbook from silently destroying them. Back the key up.

Provider API keys are deliberately left **blank** in `.env`. Set them from the `/admin` dashboard
instead, where they are Fernet-encrypted at rest and never readable again — the admin layer
overrides env, so a value set there wins with no redeploy.

If you would rather have Ansible place them, use a vault:

```bash
ansible-vault create group_vars/vault.yml
# vault_anthropic_api_key: sk-ant-...
# vault_seed_admin_email: you@example.com
# vault_seed_admin_password: ...
# vault_app_db_cluster_uri: mongodb+srv://...   # Atlas, for DEPLOYED generated apps

ansible-playbook site.yml --ask-vault-pass
```

`group_vars/vault.yml` is gitignored regardless of being encrypted.

---

## Oracle networking — read this before debugging "it's unreachable"

Oracle Cloud has **two independent firewalls**, and this trips up nearly everyone.

1. **On the VM.** Oracle's Ubuntu images ship an iptables ruleset whose `INPUT` chain ends in a
   blanket `REJECT`, persisted in `/etc/iptables/rules.v4`. ufw writes to a *different* chain, so
   `ufw allow 443` reports success, `ufw status` shows the port open, and packets are still
   rejected by the earlier rule. **The `firewall` role detects and removes these**, then purges
   `iptables-persistent` (which ufw `Breaks` anyway) so nothing reloads them on reboot; ufw
   persists its own rules under `/etc/ufw`.

2. **In the OCI console.** The instance's **Security List / NSG**. Ansible cannot touch this. Open
   ingress for TCP **80** and **443** there, or the host firewall is irrelevant because packets
   never arrive.

If the VM is unreachable, check (2) first — it is the more common cause and the less obvious one.

---

## Port conflicts

`forgeflow_api_port` defaults to **8000** because that is what your nginx already forwards the
subdomain to. If another service already holds 8000, the playbook prints a warning during
`pre_tasks`. To move it, change **both**:

```yaml
# group_vars/all.yml
forgeflow_api_port: 8010
```

and the `proxy_pass` in the nginx server block for your subdomain. Changing only one gives you a
502 that looks like the app is down.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `couldn't resolve module/action 'community.general.ufw'` | Collections not installed — `ansible-galaxy collection install -r requirements.yml` |
| `ignoring it as an ansible.cfg source` | Running from `/mnt/c` in WSL; see the gotcha above |
| `Permission denied (publickey)` | Wrong `ansible_ssh_private_key_file` or `ansible_user` (Ubuntu images use `ubuntu`, Oracle Linux uses `opc`) |
| nginx role fails `nginx -t` | Your certbot cert for `forgeflow_domain` doesn't exist yet — run `sudo certbot --nginx -d <domain>` first |
| `systemctl start forgeflow` fails | Expected before the first CD deploy: there is no image yet |
| Second run reports changes | A genuine idempotency bug — please read the task name it reports |
