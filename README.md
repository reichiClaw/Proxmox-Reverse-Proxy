# Proxmox Reverse Proxy

TLS **Main Gate** for a Proxmox instance: one Traefik proxy terminates HTTPS and routes by **subdomain** to Proxmox VE and all guest services. Certificates are **self-maintained** (issued and renewed by Traefik ACME).

## Quick mental model

```text
https://gate.<domain>      → Admin GUI (add services & settings)
https://pve.<domain>       → Proxmox VE
https://gitea.<domain>     → guest app
https://<name>.<domain>    → any new service (any configured domain)
```

DNS options — either works, per domain:

- **Wildcard record** `*.<domain>` → gate IP (zero DNS work per service), or
- **Cloudflare automation** (below): Gate creates a CNAME per host automatically — works across **multiple domains**.

Certificates are always automatic (Traefik ACME).

## Admin GUI (recommended)

```bash
cp config/base.env.example config/base.env   # set DOMAIN
python3 -m venv .venv && source .venv/bin/activate
pip install -r gui/requirements.txt
python -m gui
# open http://127.0.0.1:8080 — create admin user, then manage services/settings
```

The GUI writes the same Traefik YAML drop-ins as the CLI. Publish it at `gate.<domain>` via `config/dynamic/apps/gate.yml`. Details: [gui/README.md](gui/README.md).

## CLI alternative

```bash
./scripts/add-service.sh gitea http://10.10.10.20:3000
TRAEFIK_LXC=101 ./deploy/sync-config.sh
```

## Cloudflare DNS integration

When configured, **every new host gets a CNAME record in your Cloudflare
zone automatically** — no per-service DNS work, and no wildcard record
needed. Works with **multiple domains** at once.

### How it works

```text
You add "gitea" on domain example.com in the GUI
        │
        ├─ Traefik route file written  → https://gitea.example.com live
        └─ Cloudflare API              → CNAME gitea.example.com → <target>
```

- The **target** is one DNS name that already resolves to your gate
  (an A/AAAA record you create once, or your DDNS hostname).
- Records created by Gate are tagged (`managed-by:proxmox-gate`). Gate only
  ever updates or deletes **its own** records — existing records of any other
  origin are reported as a conflict and never touched.
- Renaming a service creates the new record and removes the old managed one;
  deleting a service removes its managed record.
- DNS failures never block route changes: Traefik config is the source of
  truth, and DNS can be re-converged any time with the sync command below.

### Setup — step by step

**1. Put your domain(s) on Cloudflare** (nameservers pointing to Cloudflare;
the free plan is fine).

**2. Create the one target record** (once, manually, in the Cloudflare
dashboard → DNS):

| Type | Name | Content | Proxy |
|---|---|---|---|
| `A` (or `AAAA`) | `gate` (→ `gate.example.com`) | your WAN IP | DNS only (grey) |

Dynamic IP? Point any DDNS client at this record instead — Gate never
touches it.

**3. Create the API token** — Cloudflare dashboard → My Profile →
**API Tokens** → *Create Token* → template **"Edit zone DNS"**:

- Permissions: `Zone → DNS → Edit` **and** `Zone → Zone → Read`
- Zone Resources: *Include → Specific zone* → select **each domain** you
  want Gate to manage (least privilege — do not grant "All zones" unless
  you need it)
- Copy the token — it is shown only once.

**4. Configure Gate** — either in the GUI:

> **Settings → Cloudflare DNS automation** → paste the API token, set the
> CNAME target (e.g. `gate.example.com`), leave *Proxy* off → **Save**.
> The flash message confirms which zones the token can see and warns if a
> configured domain is not covered.

…or in a file (headless / CLI setups):

```bash
cp config/cloudflare.env.example config/cloudflare.env
chmod 600 config/cloudflare.env
# edit: CF_API_TOKEN, CF_TARGET, CF_PROXIED
```

**5. Sync existing hosts** (pve, gate, whoami, anything added earlier):

```bash
.venv/bin/python -m gui.app.dns status   # verify token + visible zones
.venv/bin/python -m gui.app.dns sync     # create records for all current hosts
```

From now on, every add / rename / delete in the GUI keeps DNS in sync
automatically.

### Using multiple domains

1. **Settings → Additional domains**: add them comma-separated
   (e.g. `second.example, third.example`). Each must be a zone (or a
   subdomain of a zone) covered by your API token.
2. When adding a service, pick the domain from the dropdown — the CNAME is
   created in the matching zone automatically (longest-suffix match, so
   delegated subzones like `lab.example.com` work too).
3. Certificates need no extra setup: per-host HTTP-01 issuance works for
   every domain that resolves to the gate.

The CNAME target can live on any one of the domains; records in the other
zones simply point across domains (normal and fine for CNAMEs).

### Proxied ("orange cloud") mode — read before enabling

The *Proxy through Cloudflare* toggle sets new records to proxied. Defaults
to **off** (DNS-only) because:

| Concern | DNS-only (default) | Proxied |
|---|---|---|
| ACME HTTP-01 (this repo's default) | works unchanged | breaks — switch Traefik to DNS-01 first |
| Proxmox noVNC/xterm.js consoles | works unchanged | subject to Cloudflare WebSocket/timeout limits |
| Large uploads (ISOs, backups) | works unchanged | Cloudflare body-size limits apply (100 MB on free) |
| Hides your WAN IP | no | yes |

If you enable it: configure the `dnsChallenge` block in `config/traefik.yml`
(see comments there) and keep at least `pve.` and `gate.` DNS-only.

### Troubleshooting

| Symptom | Check |
|---|---|
| "no Cloudflare zone matches" | Token's *Zone Resources* includes that domain? `python -m gui.app.dns status` lists visible zones |
| "conflict — NOT managed by Gate" | A record for that host already exists (e.g. old wildcard or manual entry). Remove it yourself or pick another name; Gate won't overwrite it |
| Token verification failed | Token revoked/expired, or missing `Zone → Zone → Read` permission |
| Record created but site unreachable | The **target** record must resolve to your gate and ports 80/443 must be forwarded |

## Install on Proxmox

One-shot install — on the Proxmox host as root:

```bash
git clone https://github.com/reichiClaw/Proxmox-Reverse-Proxy.git
cd Proxmox-Reverse-Proxy
./deploy/install-proxmox.sh --vmid 110 --ip 192.168.1.10/24 \
  --gateway 192.168.1.1 --domain lab.example.com --acme-email you@lab.example.com
```

Creates the LXC, installs Traefik (checksum-verified) + Gate GUI as hardened
systemd services, wires up verified TLS to Proxmox, and prints the DNS /
port-forward follow-ups. Step-by-step manual alternative:
**[docs/install-proxmox.md](docs/install-proxmox.md)**

## Docs

| Doc | Contents |
|---|---|
| [docs/install-proxmox.md](docs/install-proxmox.md) | **Full Proxmox install manual** |
| [docs/security.md](docs/security.md) | Threat model, trust boundaries, hardening |
| [docs/architecture.md](docs/architecture.md) | Design: topology, TLS, security, phases |
| [docs/runbook.md](docs/runbook.md) | GUI/CLI add-remove, certs, PVE cutover |
| [docs/networking.md](docs/networking.md) | Domains, IPs, firewall worksheet |
| [gui/README.md](gui/README.md) | Admin GUI setup |

## Layout

```text
gui/                         # Web UI for services + settings
config/traefik.yml           # static: entrypoints + ACME
config/dynamic/apps/*.yml    # one file per subdomain/service
scripts/add-service.sh       # CLI route generator
deploy/sync-config.sh        # push config to the Traefik LXC
```
