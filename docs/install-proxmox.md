# Install manual — Proxmox TLS Main Gate

This guide walks through installing **Proxmox Reverse Proxy** (Traefik + Gate admin GUI) on a Proxmox VE host so that:

- one LXC is the **only public HTTPS entry point** (`:80` / `:443`)
- every published app is a **subdomain** (`https://name.your.domain`)
- **TLS certificates are self-maintained** (Let’s Encrypt via Traefik ACME)
- you manage routes from the **Gate web GUI** (or CLI / YAML)

Related docs: [architecture.md](architecture.md) · [networking.md](networking.md) · [runbook.md](runbook.md) · [gui/README.md](../gui/README.md)

---

## Table of contents

1. [What you will build](#1-what-you-will-build)
2. [Prerequisites](#2-prerequisites)
3. [Plan your network and DNS](#3-plan-your-network-and-dns)
4. [Create the Gate LXC on Proxmox](#4-create-the-gate-lxc-on-proxmox)
5. [Install the operating system packages](#5-install-the-operating-system-packages)
6. [Install Traefik](#6-install-traefik)
7. [Deploy this repository into the LXC](#7-deploy-this-repository-into-the-lxc)
8. [Configure domain, ACME, and first routes](#8-configure-domain-acme-and-first-routes)
9. [Install and enable the Gate admin GUI](#9-install-and-enable-the-gate-admin-gui)
10. [Open the firewall / port forwards](#10-open-the-firewall--port-forwards)
11. [First smoke test (staging certificates)](#11-first-smoke-test-staging-certificates)
12. [Put Proxmox VE behind the gate](#12-put-proxmox-ve-behind-the-gate)
13. [Switch to production Let’s Encrypt](#13-switch-to-production-lets-encrypt)
14. [Add real services](#14-add-real-services)
15. [Hardening checklist](#15-hardening-checklist)
16. [Backup and restore](#16-backup-and-restore)
17. [Upgrades](#17-upgrades)
18. [Troubleshooting](#18-troubleshooting)
19. [Appendix](#19-appendix)

---

## 1. What you will build

```text
                         Internet / LAN clients
                                   |
                              :80 / :443
                                   |
                    +--------------+---------------+
                    |  Gate LXC (Debian)           |
                    |  - Traefik  (TLS Main Gate)  |
                    |  - Gate GUI (:8080 local)    |
                    +--------------+---------------+
                                   |
           +-----------------------+------------------------+
           |                       |                        |
    pve.your.domain         gate.your.domain         app.your.domain
           |                       |                        |
    Proxmox VE :8006        Gate GUI :8080           Guest VM/LXC
```

| Hostname | Purpose |
|---|---|
| `gate.<domain>` | Admin GUI to add services & settings |
| `pve.<domain>` | Proxmox VE web UI + API |
| `<name>.<domain>` | Any guest service you publish |

All certificates are issued and renewed by Traefik. You do not upload cert files per service.

---

## 2. Prerequisites

### On Proxmox

- Proxmox VE 8.x (or newer) with working networking (`vmbr0` at minimum)
- Ability to create an unprivileged LXC
- Debian 12 (Bookworm) CT template downloaded in Proxmox
- Shell access to the Proxmox **host** as `root` (or equivalent)

Download the template if needed (Proxmox UI → local storage → CT Templates → Templates, or on the host):

```bash
pveam update
pveam available | grep debian-12-standard
pveam download local debian-12-standard_12.*_amd64.tar.zst
```

### Outside Proxmox

- A **domain name** you control (example used below: `lab.example.com`)
- DNS access to create records (wildcard recommended)
- For public HTTPS with Let’s Encrypt **HTTP-01**:
  - ports **80** and **443** reachable on the Gate LXC from the internet  
  - *or* use **DNS-01** instead (see [Appendix C](#c-dns-01-wildcard-certificates))
- For LAN-only labs: either use DNS-01, an internal ACME CA, or accept staging/self-signed workflows (HTTP-01 needs the CA to reach `:80`)

### Decide these values before you start

Fill [networking.md](networking.md) or copy this table:

| Item | Your value | Example in this guide |
|---|---|---|
| Base domain | | `lab.example.com` |
| Gate LXC VMID | | `110` |
| Gate LXC hostname | | `gate` |
| Gate LXC IP | | `192.168.1.10/24` |
| Gateway | | `192.168.1.1` |
| Bridge | | `vmbr0` |
| Proxmox node IP (UI) | | `192.168.1.2` |
| ACME email | | `admin@lab.example.com` |
| Git repo URL | | `https://github.com/reichiClaw/Proxmox-Reverse-Proxy.git` |

Replace every example IP/domain with yours as you follow the steps.

---

## 3. Plan your network and DNS

### 3.1 Recommended simple layout (single bridge)

Many home / small lab installs put everything on `vmbr0`:

| Role | Address |
|---|---|
| Router / gateway | `192.168.1.1` |
| Proxmox host | `192.168.1.2` |
| Gate LXC (Traefik) | `192.168.1.10` |
| Guest apps | `192.168.1.20+` |

Router port-forward (or WAN firewall allow):

- WAN `:80` → `192.168.1.10:80`
- WAN `:443` → `192.168.1.10:443`

Do **not** forward Proxmox `:8006` to the internet once the gate works. Keep LAN/VPN access to `:8006` as break-glass.

### 3.2 Better layout (two networks)

| Network | Bridge | Who is on it |
|---|---|---|
| Front / LAN | `vmbr0` | clients, Gate eth0 |
| Services | `vmbr1` | Gate eth1, guests, optionally PVE mgmt |

Then firewall so only the Gate can reach guest app ports from untrusted networks. See [networking.md](networking.md).

### 3.3 DNS records (do this early)

At your DNS provider, create:

| Type | Name | Value |
|---|---|---|
| `A` | `gate.lab.example.com` | Gate LXC IP (or public IP if 1:1 NAT) |
| `A` | `*.lab.example.com` | **same IP** (strongly recommended) |
| `A` | `pve.lab.example.com` | same IP (optional if wildcard covers it) |

Notes:

- With a **wildcard**, every new service works without another DNS change.
- If the Gate is behind NAT, public DNS must point at the **WAN IP**; the router forwards 80/443 to the LXC.
- For split DNS (LAN resolves to private IP, internet to WAN IP), configure both views the same way relative to how clients reach the gate.

Verify from a machine that should reach the gate:

```bash
dig +short gate.lab.example.com
dig +short whoami.lab.example.com
```

Both should return the expected address **before** you expect certificates to succeed.

---

## 4. Create the Gate LXC on Proxmox

You can use the Proxmox UI or the CLI. Both are fine; CLI is shown for reproducibility.

### 4.1 Create via CLI (on the Proxmox host)

```bash
# Adjust storage, bridge, IP, password, template filename
TEMPLATE="local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst"
VMID=110
STORAGE=local-lvm   # or your CT disk storage
BRIDGE=vmbr0
IP=192.168.1.10/24
GW=192.168.1.1

pct create "$VMID" "$TEMPLATE" \
  --hostname gate \
  --memory 1024 \
  --cores 2 \
  --rootfs "${STORAGE}:8" \
  --net0 name=eth0,bridge=${BRIDGE},ip=${IP},gw=${GW},firewall=1 \
  --unprivileged 1 \
  --features nesting=1 \
  --onboot 1 \
  --start 1 \
  --password   # prompts for root password
```

Suggested resources:

| Resource | Value | Notes |
|---|---|---|
| Memory | 1024 MB | 512 MB can work; 1 GB is comfortable with the GUI |
| Cores | 1–2 | |
| Disk | 8 GB | Traefik + venv + logs |
| Unprivileged | yes | |
| Nesting | yes | only required if you later run Docker inside; harmless otherwise |
| On boot | yes | gate should return after host reboot |

### 4.2 Create via UI

1. **Create CT**
2. General: VMID `110`, hostname `gate`, set password / SSH key
3. Template: Debian 12 standard
4. Disks: 8 GB on your storage
5. CPU: 2 cores · Memory: 1024 MB
6. Network: static IP on `vmbr0`, gateway set, firewall enabled
7. Confirm → Start after created
8. Options → Features → enable **Nesting** if you want Docker later
9. Options → Start at boot: yes

### 4.3 Enter the container

From the Proxmox host:

```bash
pct enter 110
```

Or SSH to `192.168.1.10` if you installed `openssh-server` / pushed a key.

Confirm networking:

```bash
ip -br a
ping -c2 1.1.1.1
ping -c2 deb.debian.org
```

---

## 5. Install the operating system packages

Still inside the Gate LXC (`pct enter 110`):

```bash
apt update
apt -y full-upgrade
apt -y install \
  ca-certificates curl wget gnupg apt-transport-https \
  git sudo ufw \
  python3 python3-venv python3-pip \
  apache2-utils jq dnsutils
```

Optional but useful:

```bash
apt -y install openssh-server
# add your SSH public key to /root/.ssh/authorized_keys
```

Create a dedicated system user for the Gate GUI (Traefik can stay root or its own user; this guide runs Traefik as a system service as root for binding `:80`/`:443`, and the GUI as user `gate`):

```bash
useradd --system --home /opt/gate --shell /usr/sbin/nologin gate
mkdir -p /opt/gate /etc/traefik/dynamic/apps /var/lib/traefik /var/log/traefik
chown -R gate:gate /opt/gate
touch /var/lib/traefik/acme.json
chmod 600 /var/lib/traefik/acme.json
```

---

## 6. Install Traefik

### 6.1 Download a Traefik v3 release

Check the latest Traefik v3 release at https://github.com/traefik/traefik/releases and adjust the version:

```bash
TRAEFIK_VERSION="v3.4.1"   # pin a current v3.x release
cd /tmp
curl -sLO "https://github.com/traefik/traefik/releases/download/${TRAEFIK_VERSION}/traefik_${TRAEFIK_VERSION}_linux_amd64.tar.gz"
tar xzf "traefik_${TRAEFIK_VERSION}_linux_amd64.tar.gz" traefik
install -m 0755 traefik /usr/local/bin/traefik
traefik version
```

### 6.2 systemd unit for Traefik

```bash
cat >/etc/systemd/system/traefik.service <<'EOF'
[Unit]
Description=Traefik (Proxmox TLS Main Gate)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/traefik --configFile=/etc/traefik/traefik.yml
Restart=on-failure
RestartSec=5
LimitNOFILE=65536
NoNewPrivileges=true
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
```

Do **not** start Traefik yet — deploy config first (next section).

---

## 7. Deploy this repository into the LXC

### 7.1 Clone into `/opt/gate`

```bash
# as root inside the LXC
cd /opt
git clone https://github.com/reichiClaw/Proxmox-Reverse-Proxy.git gate
chown -R gate:gate /opt/gate
```

If you work from a fork or private mirror, clone that URL instead.

### 7.2 Two ways to keep config in sync

**Option A — LXC is the source of truth (simplest)**  
Edit files directly under `/opt/gate/config` and copy (or symlink) them into `/etc/traefik`.

**Option B — Git repo on the Proxmox host, push into the LXC**  
Keep the clone on the host and run:

```bash
# on Proxmox host
cd /path/to/Proxmox-Reverse-Proxy
TRAEFIK_LXC=110 ./deploy/sync-config.sh
```

This guide uses **Option A** for the initial install, then optionally switches to B for GitOps.

### 7.3 Install config into `/etc/traefik`

```bash
# inside LXC
cp /opt/gate/config/traefik.yml /etc/traefik/traefik.yml
cp /opt/gate/config/dynamic/middlewares.yml /etc/traefik/dynamic/middlewares.yml
cp /opt/gate/config/dynamic/pve.yml /etc/traefik/dynamic/pve.yml
cp /opt/gate/config/dynamic/apps/*.yml /etc/traefik/dynamic/apps/
# keep template only in the git tree if you prefer:
rm -f /etc/traefik/dynamic/apps/_template.yml

# Make the live Traefik tree and the repo tree the same place (recommended)
# so the Gate GUI edits are picked up immediately:
rm -rf /etc/traefik/dynamic
ln -sfn /opt/gate/config/dynamic /etc/traefik/dynamic
cp /opt/gate/config/traefik.yml /etc/traefik/traefik.yml
# Also symlink static config if you want GUI ACME edits live without copying:
ln -sfn /opt/gate/config/traefik.yml /etc/traefik/traefik.yml
```

> **Important:** Traefik’s file provider watches `/etc/traefik/dynamic`. Symlinking that directory to `/opt/gate/config/dynamic` means the Gate GUI (which writes into the repo tree) updates live routes without a separate sync step.

If you use symlinks, ensure Traefik can read them:

```bash
ls -la /etc/traefik /etc/traefik/dynamic /opt/gate/config/dynamic/apps
```

---

## 8. Configure domain, ACME, and first routes

### 8.1 Set `base.env`

```bash
cp /opt/gate/config/base.env.example /opt/gate/config/base.env
editor /opt/gate/config/base.env
```

Example:

```bash
DOMAIN=lab.example.com
ACME_EMAIL=admin@lab.example.com
```

### 8.2 Set ACME email / staging in `traefik.yml`

Edit `/opt/gate/config/traefik.yml` (and thus `/etc/traefik/traefik.yml` if symlinked):

```yaml
certificatesResolvers:
  le:
    acme:
      email: admin@lab.example.com
      storage: /var/lib/traefik/acme.json
      caServer: https://acme-staging-v02.api.letsencrypt.org/directory
      httpChallenge:
        entryPoint: web
```

Stay on **staging** until the smoke test works. Staging certs are not trusted by browsers; that is intentional.

### 8.3 Rewrite example hostnames to your domain

Either use the Gate GUI Settings page after it is up, or do a one-shot replace now:

```bash
DOMAIN=lab.example.com
# replace example.com hostnames in shipped examples
sed -i "s/example.com/${DOMAIN}/g" \
  /opt/gate/config/dynamic/pve.yml \
  /opt/gate/config/dynamic/apps/whoami.yml \
  /opt/gate/config/dynamic/apps/gate.yml
```

### 8.4 Point PVE upstream at your node

Edit `/opt/gate/config/dynamic/pve.yml`:

```yaml
servers:
  - url: "https://192.168.1.2:8006"   # your Proxmox node IP
```

The Gate LXC must be able to reach that IP on TCP 8006:

```bash
curl -kI https://192.168.1.2:8006
```

### 8.5 Temporary whoami backend (for smoke test)

The shipped `whoami.yml` points at `http://10.10.10.50:80`. For a quick test you can run whoami **inside the Gate LXC** with Docker, or use any HTTP service.

Easiest without Docker — a tiny Python responder on localhost:

```bash
cat >/usr/local/bin/gate-whoami.py <<'PY'
#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = f"whoami ok\nhost: {self.headers.get('Host')}\npath: {self.path}\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args): pass
HTTPServer(("127.0.0.1", 8099), H).serve_forever()
PY
chmod +x /usr/local/bin/gate-whoami.py

cat >/etc/systemd/system/gate-whoami.service <<'EOF'
[Unit]
Description=Tiny whoami for Gate smoke tests
After=network.target
[Service]
ExecStart=/usr/local/bin/gate-whoami.py
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOF

systemctl enable --now gate-whoami.service
```

Point the whoami route at it — edit `/opt/gate/config/dynamic/apps/whoami.yml`:

```yaml
servers:
  - url: "http://127.0.0.1:8099"
```

---

## 9. Install and enable the Gate admin GUI

### 9.1 Python virtualenv

```bash
cd /opt/gate
sudo -u gate python3 -m venv /opt/gate/.venv
sudo -u gate /opt/gate/.venv/bin/pip install --upgrade pip
sudo -u gate /opt/gate/.venv/bin/pip install -r /opt/gate/gui/requirements.txt
```

### 9.2 Session secret

```bash
SESSION_SECRET="$(openssl rand -hex 32)"
cat >/etc/gate-admin.env <<EOF
GATE_SESSION_SECRET=${SESSION_SECRET}
EOF
chmod 600 /etc/gate-admin.env
```

### 9.3 systemd unit

```bash
cp /opt/gate/deploy/gate-admin.service /etc/systemd/system/gate-admin.service
# Ensure paths match (defaults already use /opt/gate)
systemctl daemon-reload
systemctl enable --now gate-admin.service
systemctl status gate-admin.service --no-pager
```

Confirm it listens only on localhost:

```bash
ss -lntp | grep 8080
curl -sI http://127.0.0.1:8080/setup | head
```

### 9.4 Permissions so the GUI can write config

The GUI process runs as `gate` and must write into `/opt/gate/config`:

```bash
chown -R gate:gate /opt/gate/config
# Traefik reads the same files — world-readable YAML is OK; secrets stay in gui.env / acme.json
chmod 644 /opt/gate/config/traefik.yml
chmod 644 /opt/gate/config/dynamic/*.yml /opt/gate/config/dynamic/apps/*.yml 2>/dev/null || true
```

If you symlinked `/etc/traefik/traefik.yml` → `/opt/gate/config/traefik.yml`, Traefik still needs read access (it does, as root).

### 9.5 Start Traefik

```bash
systemctl enable --now traefik.service
systemctl status traefik.service --no-pager
journalctl -u traefik -n 50 --no-pager
```

Traefik should bind `:80` and `:443`:

```bash
ss -lntp | grep -E ':80|:443'
```

---

## 10. Open the firewall / port forwards

### 10.1 Inside the Gate LXC (UFW example)

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp    # if you use SSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
ufw status
```

Do **not** expose `8080` publicly; Traefik proxies `gate.<domain>` to `127.0.0.1:8080`.

### 10.2 Proxmox host firewall / datacenter firewall

If the Proxmox firewall is enabled on the CT or datacenter:

- Allow in to the Gate CT: `80/tcp`, `443/tcp` (and SSH if needed)
- Allow out from Gate CT to your guests / PVE `:8006`

### 10.3 Router / edge

Forward WAN 80/443 → Gate LXC IP. Remove old forwards that published apps or PVE directly.

### 10.4 Admin allowlist middleware

Shipped `gate.yml` attaches `admin-allowlist` (private RFC1918 ranges) from `middlewares.yml`.  
If you need to reach `gate.<domain>` from a public IP (or CGNAT VPN range), edit `/opt/gate/config/dynamic/middlewares.yml` and add your admin IP / VPN CIDR to `sourceRange`, or temporarily remove `admin-allowlist` from `gate.yml` until you confirm login works on LAN.

---

## 11. First smoke test (staging certificates)

### 11.1 DNS must already point here

```bash
dig +short whoami.lab.example.com
dig +short gate.lab.example.com
```

### 11.2 HTTP → HTTPS and Traefik reachability

From a client (or the LXC):

```bash
curl -sI http://whoami.lab.example.com
# expect redirect to https://...

curl -vkI https://whoami.lab.example.com
# staging cert = untrusted; look for HTTP/2 200 and body "whoami ok"
```

### 11.3 Watch ACME

```bash
journalctl -u traefik -f
# look for certificate obtain success for whoami.lab.example.com
ls -l /var/lib/traefik/acme.json
```

### 11.4 Open the Gate GUI

Browser:

1. Visit `https://gate.lab.example.com` (accept the staging warning once)
2. Complete **Initial setup** (create admin user)
3. Sign in → **Services** should list `whoami`, `gate`, `pve`
4. Open **Settings** and confirm domain + ACME email

If `admin-allowlist` blocks you, use LAN IP or adjust the middleware as in §10.4.

---

## 12. Put Proxmox VE behind the gate

### 12.1 Validate the route

1. Confirm `pve.yml` upstream is `https://<pve-ip>:8006`
2. Open `https://pve.lab.example.com`
3. Log in
4. Open a VM/CT console (**noVNC** / **xterm.js**) — WebSockets must work
5. Hit the API once, e.g. from a trusted machine:

```bash
curl -k https://pve.lab.example.com/api2/json/version
```

### 12.2 Cut over exposure

When satisfied:

1. Remove WAN port-forward / DNAT for `:8006`
2. Keep **break-glass** access:
   - LAN to `https://192.168.1.2:8006`, and/or
   - VPN into the management network
3. Optionally attach `admin-allowlist` on the `pve` router in `pve.yml` so only private/VPN clients can use `pve.<domain>`

### 12.3 Cluster notes

- For a single node, pointing at that node’s IP is enough.
- For a cluster, prefer the node IP you want to land on, or a stable VIP; consoles for guests on other nodes may need additional routing — test before removing direct access.

---

## 13. Switch to production Let’s Encrypt

Only after staging smoke tests succeed (whoami + gate + pve as needed).

### Via Gate GUI

1. **Settings**
2. Uncheck **Use Let’s Encrypt staging CA**
3. Save

### Via file

In `/opt/gate/config/traefik.yml`:

```yaml
caServer: https://acme-v02.api.letsencrypt.org/directory
```

Restart Traefik (ACME resolver changes are static config):

```bash
systemctl restart traefik.service
```

Force fresh certs if browsers still see staging:

```bash
systemctl stop traefik
mv /var/lib/traefik/acme.json /var/lib/traefik/acme.json.staging.bak
touch /var/lib/traefik/acme.json
chmod 600 /var/lib/traefik/acme.json
systemctl start traefik
```

Then reload `https://whoami.lab.example.com` — the certificate should be trusted.

> Rate limits: production Let’s Encrypt has weekly limits. Do not flap production issuance while testing; use staging for experiments.

---

## 14. Add real services

### 14.1 With the Gate GUI (preferred)

1. Open `https://gate.lab.example.com`
2. **Services → Add service**
3. Name: `gitea` (→ `gitea.lab.example.com`)
4. Upstream: `http://192.168.1.20:3000` (must be reachable **from the Gate LXC**)
5. Create route
6. Wait a few seconds for ACME; open the URL

### 14.2 With the CLI

```bash
cd /opt/gate
sudo -u gate bash -lc './scripts/add-service.sh gitea http://192.168.1.20:3000'
# if dynamic/ is symlinked, Traefik hot-reloads automatically
```

### 14.3 Checklist for each new guest app

1. App listens on an IP the Gate can reach
2. From Gate LXC: `curl -sI http://<guest-ip>:<port>`
3. Add the service (GUI or CLI)
4. DNS covered by wildcard (or create `A` record)
5. Test `https://<name>.<domain>`
6. Remove any old direct WAN port-forward for that app

---

## 15. Hardening checklist

- [ ] Only `:80` and `:443` published on WAN
- [ ] `:8006` / guest ports not forwarded publicly
- [ ] VPN or LAN break-glass to Proxmox still works
- [ ] `admin-allowlist` (or VPN-only DNS) on `gate.` and preferably `pve.`
- [ ] Strong Gate GUI password; `config/gui.env` mode `600`, owned by `gate`
- [ ] `acme.json` mode `600`
- [ ] `GATE_SESSION_SECRET` set in `/etc/gate-admin.env`
- [ ] `GATE_HTTPS_ONLY=1` in the GUI unit (already in shipped unit)
- [ ] Unattended upgrades on the LXC: `apt install unattended-upgrades`
- [ ] Proxmox backup job for CT `110` (see §16)
- [ ] Production ACME only after staging validation
- [ ] Consider DNS-01 wildcard if you cannot expose `:80` ([Appendix C](#c-dns-01-wildcard-certificates))

---

## 16. Backup and restore

### 16.1 What matters

| Path | Why |
|---|---|
| `/opt/gate/` | Git repo, routes, GUI, venv |
| `/var/lib/traefik/acme.json` | Issued certificates / ACME account |
| `/etc/gate-admin.env` | Session secret |
| `/etc/systemd/system/traefik.service` | Unit (also in docs) |
| `/etc/systemd/system/gate-admin.service` | Unit |

If you symlinked dynamic config into `/opt/gate`, backing up the CT (or `/opt/gate` + `acme.json`) is enough.

### 16.2 Proxmox Backup / vzdump

In Datacenter → Backup, include CT `110` on a regular schedule. That captures the whole gate.

### 16.3 Restore sketch

1. Restore / recreate CT
2. Ensure IP/DNS/port-forwards still match
3. `systemctl enable --now traefik gate-admin`
4. Confirm `acme.json` permissions `600`
5. Hit `https://gate.<domain>` and a known app

---

## 17. Upgrades

### Traefik

```bash
# inside LXC — download newer v3.x, install over binary
systemctl stop traefik
install -m 0755 /tmp/traefik /usr/local/bin/traefik
traefik version
systemctl start traefik
```

Read Traefik migration notes before major jumps (v2 → v3, etc.). This repo targets **v3**.

### Gate GUI / repo

```bash
cd /opt/gate
sudo -u gate git pull
sudo -u gate /opt/gate/.venv/bin/pip install -r gui/requirements.txt
systemctl restart gate-admin
# Traefik hot-reloads YAML; restart Traefik only if traefik.yml static bits changed
systemctl restart traefik
```

---

## 18. Troubleshooting

### Certificate not issued

1. DNS for the hostname points at the gate? `dig +short name.domain`
2. Port 80 reachable from the internet for HTTP-01?  
   External check: https://www.yougetsignal.com/tools/open-ports/ or `curl` from a VPS
3. Staging vs production CA mismatch?
4. Logs: `journalctl -u traefik -n 100 --no-pager`
5. `acme.json` writable / mode `600`?

### 404 / no route

1. File present under `/opt/gate/config/dynamic/apps/<name>.yml`?
2. Symlink `/etc/traefik/dynamic` → repo dynamic dir still valid?
3. `Host()` rule matches the URL you typed?
4. Traefik log shows the router?

### 502 Bad Gateway

1. From **inside Gate LXC**: `curl -vk <upstream>`
2. Guest firewall blocking the Gate IP?
3. Wrong scheme (`http` vs `https`) or port in upstream URL?
4. For PVE, upstream must be `https://...:8006`

### Gate GUI not loading

1. `systemctl status gate-admin`
2. `curl -sI http://127.0.0.1:8080/setup`
3. Traefik route `gate.yml` upstream `http://127.0.0.1:8080`?
4. Blocked by `admin-allowlist`? Try from LAN or add your IP

### Proxmox console (noVNC) fails through proxy

1. Confirm WebSocket upgrade — try another browser, disable broken extensions
2. Do not terminate WebSockets at another middle proxy
3. `passHostHeader: true` must remain on the PVE service
4. Test direct `https://<pve-ip>:8006` via VPN to isolate PVE vs proxy

### Permission errors when saving in the GUI

```bash
chown -R gate:gate /opt/gate/config
systemctl restart gate-admin
```

---

## 19. Appendix

### A. Quick install command checklist

```bash
# --- on Proxmox host ---
pct create 110 local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \
  --hostname gate --memory 1024 --cores 2 --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.1.10/24,gw=192.168.1.1,firewall=1 \
  --unprivileged 1 --features nesting=1 --onboot 1 --start 1 --password
pct enter 110

# --- inside LXC ---
apt update && apt -y full-upgrade
apt -y install ca-certificates curl git python3 python3-venv ufw jq dnsutils
useradd --system --home /opt/gate --shell /usr/sbin/nologin gate
mkdir -p /opt/gate /etc/traefik /var/lib/traefik
touch /var/lib/traefik/acme.json && chmod 600 /var/lib/traefik/acme.json
# install traefik binary to /usr/local/bin/traefik  (see §6)
# install traefik.service                          (see §6)
git clone https://github.com/reichiClaw/Proxmox-Reverse-Proxy.git /opt/gate
chown -R gate:gate /opt/gate
ln -sfn /opt/gate/config/traefik.yml /etc/traefik/traefik.yml
ln -sfn /opt/gate/config/dynamic /etc/traefik/dynamic
# edit base.env, traefik.yml email/domain, pve.yml upstream, whoami upstream
# install gate-admin venv + unit                   (see §9)
systemctl enable --now traefik gate-admin
```

### B. Example values used in this guide

| Item | Value |
|---|---|
| Domain | `lab.example.com` |
| Gate CTID | `110` |
| Gate IP | `192.168.1.10` |
| PVE IP | `192.168.1.2` |
| Repo path in CT | `/opt/gate` |
| Traefik config | `/etc/traefik` → symlinked to `/opt/gate/config` |
| ACME store | `/var/lib/traefik/acme.json` |
| GUI bind | `127.0.0.1:8080` |

### C. DNS-01 wildcard certificates

Use when port 80 cannot be opened, or you want one wildcard cert for all subdomains.

1. Create an API token at your DNS provider (Cloudflare, Route53, Hetzner, …).
2. Install the matching Traefik DNS provider credentials as env vars in `traefik.service`.
3. In `traefik.yml`, replace `httpChallenge` with `dnsChallenge` (see comments in the file).
4. Optionally request `*.lab.example.com` via a TLS store / certificate directive.
5. Restart Traefik.

New GUI-added subdomains then need no per-host ACME round trip if they are covered by the wildcard.

### D. Publishing the Gate GUI without allowlist (lab only)

In `/opt/gate/config/dynamic/apps/gate.yml`, under `middlewares`, leave only:

```yaml
middlewares:
  - secured
```

Re-enable `admin-allowlist` before exposing the UI to the internet.

### E. Using `deploy/sync-config.sh` from the Proxmox host

If the git checkout lives on the host instead of (or in addition to) the LXC:

```bash
# on Proxmox host
git clone https://github.com/reichiClaw/Proxmox-Reverse-Proxy.git
cd Proxmox-Reverse-Proxy
# edit config/* for your domain
TRAEFIK_LXC=110 ./deploy/sync-config.sh
```

Note: the Gate GUI inside the LXC edits `/opt/gate/config`. If you also sync from the host, pick **one** source of truth to avoid overwriting GUI changes.

### F. Verify end state

| Check | Command / action | Expect |
|---|---|---|
| Traefik up | `systemctl is-active traefik` | `active` |
| GUI up | `systemctl is-active gate-admin` | `active` |
| Ports | `ss -lntp \| grep -E ':80|:443|:8080'` | 80/443 public bind; 8080 on 127.0.0.1 |
| whoami | `curl -k https://whoami.<domain>` | 200 body |
| gate UI | browser `https://gate.<domain>` | login / services |
| pve UI | browser `https://pve.<domain>` | login + console |
| Cert prod | browser padlock on whoami | trusted LE cert |
| No direct PVE WAN | external port scan `:8006` | closed |

---

## Next steps after install

- Add guest apps via **Gate → Services**
- Fill remaining rows in [networking.md](networking.md)
- Read day-2 ops in [runbook.md](runbook.md)
- Review security options in [architecture.md](architecture.md) (SSO, CrowdSec, HA)
