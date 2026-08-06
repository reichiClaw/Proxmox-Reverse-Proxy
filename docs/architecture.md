# Proxmox TLS Gateway — Architecture Plan

A single reverse proxy sits in front of this Proxmox instance and becomes the **only public TLS entry point** for the hypervisor UI and all published services running in VMs / LXCs.

**Installing on Proxmox?** Start with [install-proxmox.md](install-proxmox.md).

## Design principles

1. **Subdomain = service** — each published app gets its own hostname (`gitea.<domain>`, `pve.<domain>`, …).
2. **Easy add** — publishing a service is one command (or one small YAML file); Traefik hot-reloads; no firewall changes per app.
3. **Self-maintained certificates** — Traefik issues and renews TLS certs automatically via ACME. No manual upload, no cron you babysit.
4. **One gate** — clients only hit `:443` (and `:80` for redirects / HTTP-01).

## Goals

- TLS Main Gate for Proxmox VE, optional PBS, and all guest services.
- Host-based (subdomain) routing as the default; path-based only when unavoidable.
- Proxmox-aware WebSocket / noVNC / xterm.js / API behavior.
- Guests stay on internal networks; only the proxy is exposed.
- Declarative config in Git; add/remove services without touching Traefik’s static config.

## Non-goals (initial)

- Full mesh mTLS between every guest.
- Replacing Proxmox auth (SSO can be layered later).
- Exposing arbitrary non-HTTP protocols without an explicit TCP router.

---

## Recommended stack

| Layer | Choice | Why |
|---|---|---|
| Edge proxy | **Traefik v3** | Hot-reload file provider, per-host ACME, middleware reuse, WebSockets |
| Runtime | **Debian LXC** on the Proxmox host | Cheap, backupable, isolated from PVE host OS |
| Certificates | **ACME (Let’s Encrypt)** via Traefik | Self-maintained: issue + renew automatically per subdomain (or one wildcard) |
| DNS | Wildcard `*.<domain>` → Traefik IP | New subdomains need **no DNS change** |
| Optional auth | Authelia / Authentik (later) | SSO in front of selected non-PVE apps |

**Why not Caddy / NPM / HAProxy as default?** Caddy is simpler but less flexible for mixed TCP and reusable middleware catalogs; NPM is UI-centric and weaker as GitOps; HAProxy needs more manual ACME wiring. Traefik best matches “versatile + easy subdomain adds + self-maintained certs.”

---

## Subdomain model

```text
https://pve.<domain>      → Proxmox VE :8006
https://pbs.<domain>      → Proxmox Backup Server :8007   (optional)
https://gitea.<domain>    → guest app
https://jellyfin.<domain> → guest app
https://<name>.<domain>   → any new service
```

Rules:

- One **subdomain per service** (not path prefixes).
- Name of the service file ≈ subdomain label (`gitea.yml` → `gitea.<domain>`).
- Wildcard DNS `*.<domain>` A/AAAA → Traefik, so adding a route does not require a new DNS record.
- Apex/`www` can point at a landing page or be unused.

---

## Easy service onboarding

### Happy path (recommended) — Admin GUI

Run the Gate web UI (`python -m gui`, published as `https://gate.<domain>`):

1. **Services → Add service** — enter subdomain label + upstream URL.
2. GUI writes `config/dynamic/apps/<name>.yml`.
3. Traefik hot-reloads; ACME issues/renews the cert.
4. Live at `https://<name>.<domain>`.

**Settings** in the same UI covers base domain, ACME email, and staging/production CA.

### CLI alternative

```bash
./scripts/add-service.sh gitea http://10.10.10.20:3000
./deploy/sync-config.sh
```

### Manual drop-in (same result)

Copy `config/dynamic/apps/_template.yml`, set subdomain + upstream URL, save as `config/dynamic/apps/<name>.yml`.

### Checklist when something is new on the network

1. Guest reachable from Traefik on the services network.
2. Add via GUI (or `add-service.sh` / YAML drop-in).
3. Confirm `https://<name>.<domain>` (cert appears automatically).
4. Remove any old direct port-forward for that app.

No per-service firewall hole. No manual certificate steps.

---

## Self-maintained certificates

Certificates are owned and lifecycle-managed by Traefik’s ACME client.

| Concern | Behavior |
|---|---|
| Issue | Automatic when a router with `tls.certResolver` first serves a host |
| Renew | Automatic before expiry (Traefik/ACME) |
| Storage | `/var/lib/traefik/acme.json` (mode `600`, persisted, backed up) |
| Redirect | `:80` → `:443` for all HTTP |
| Operator work | Set ACME email + domain once in `config/base.env`; then none per service |

### Two ACME modes

| Mode | When to use | New subdomain UX |
|---|---|---|
| **HTTP-01, per-host certs** | Simple; port 80 reachable from the internet (or LAN CA that speaks ACME HTTP-01) | First request triggers issuance; short wait |
| **DNS-01, wildcard `*.<domain>`** | No inbound 80, or instant certs for every new name | Zero ACME delay when adding services |

Default in this repo’s static config: **HTTP-01 per host** (fewest external dependencies). Switch to DNS-01 wildcard when the DNS API token is available — best “add subdomain, it just works” experience.

### Staging vs production

Use Let’s Encrypt **staging** until the gate validates end-to-end, then flip to production in `config/traefik.yml` to avoid rate limits.

### Backend TLS (separate from edge certs)

| Backend | Mode |
|---|---|
| Typical apps | Edge HTTPS → plain HTTP on services net |
| Proxmox VE / PBS | Edge HTTPS → HTTPS re-encrypt to `:8006` / `:8007` |
| Hardened apps | Edge HTTPS → HTTPS with internal CA (later; drop `insecureSkipVerify`) |

Edge certificates stay self-maintained either way; guest apps do not need public certs.

---

## High-level topology

```text
                         clients
                            |
                       :80 / :443
                            |
                 +----------+-----------+
                 | Traefik LXC          |
                 | subdomain routers +  |
                 | ACME cert store      |
                 +----------+-----------+
                            |
        +-------------------+-------------------+
        |                   |                   |
   pve.<domain>       gitea.<domain>     jellyfin.<domain>
        |                   |                   |
   PVE :8006          App :3000            App :8096
```

### Network model

1. **Front**: Traefik binds `80/443` (LAN and/or WAN).
2. **Services net**: guests + PVE API reachable from Traefik only (plus admin/VPN).
3. Edge firewall allows only Traefik `80/443` from untrusted networks.
4. **Break-glass**: VPN/mgmt VLAN still reaches PVE `:8006` if the proxy is down.

---

## Routing internals

### Providers

- **File provider** watches `config/dynamic/` (and `apps/*.yml`).
- Static config (`traefik.yml`) rarely changes — entrypoints, ACME, providers only.
- Optional Docker provider later; not required for VM/LXC labs.

### Per-service drop-in shape

```yaml
http:
  routers:
    gitea:
      rule: "Host(`gitea.{{ env "DOMAIN" }}`)"   # rendered, or literal host in generated files
      entryPoints: ["websecure"]
      tls:
        certResolver: le
      service: gitea
      middlewares: ["secured"]

  services:
    gitea:
      loadBalancer:
        servers:
          - url: "http://10.10.10.20:3000"
        passHostHeader: true
```

Generated files from `add-service.sh` use a concrete hostname from `base.env` (`DOMAIN=…`) so Traefik needs no templating at runtime.

### Shared middlewares

Defined once in `middlewares.yml` (security headers, compress, optional allowlist). Services attach by name — no copy-paste of header blocks.

### TCP / non-HTTP

Explicit Traefik TCP routers or VPN-only. Do not publish host SSH on the public gate by default.

---

## Proxmox VE behind the proxy

Needs WebSocket upgrades, generous timeouts, `passHostHeader`, and HTTPS upstream.

```yaml
# config/dynamic/pve.yml (committed example; adjust IP)
http:
  routers:
    pve:
      rule: "Host(`pve.example.com`)"
      entryPoints: ["websecure"]
      tls:
        certResolver: le
      service: pve
      middlewares: ["secured"]

  services:
    pve:
      loadBalancer:
        servers:
          - url: "https://172.16.0.2:8006"
        serversTransport: pve-transport
        passHostHeader: true

  serversTransports:
    pve-transport:
      insecureSkipVerify: true
```

Do not DNAT `:8006` publicly after cutover. Keep VPN break-glass.

---

## Deployment layout

### LXC profile

| Setting | Value |
|---|---|
| Template | Debian 12/13 |
| Unprivileged | Yes |
| Nesting | On only if Docker-in-LXC |
| CPU / RAM | 1–2 vCPU, 512 MB–1 GB |
| Disk | 4–8 GB + persist certs/config |
| Network | Static IP on front (and optional services NIC) |

### Paths inside the Traefik LXC

```text
/etc/traefik/traefik.yml
/etc/traefik/dynamic/middlewares.yml
/etc/traefik/dynamic/pve.yml
/etc/traefik/dynamic/apps/*.yml
/var/lib/traefik/acme.json
```

This repo mirrors that under `config/`.

---

## Security model

1. Only Traefik `80/443` exposed.
2. Admin UIs (`pve.`, Traefik dashboard): allowlist and/or VPN.
3. Phase-1 auth = native app auth; ForwardAuth later for non-PVE apps.
4. Protect `acme.json`; back it up encrypted.
5. Guests must not be directly reachable from untrusted networks.
6. Pin Traefik version; unattended OS security updates on the LXC.

---

## Phased delivery

### Phase 0 — Foundations

- Traefik LXC, wildcard DNS, firewall baseline, `base.env`.
- ACME staging + `whoami` subdomain smoke test.

### Phase 1 — Main Gate for Proxmox

- `pve.<domain>` (+ `pbs.` if needed), console/API validation.
- Remove public `:8006` / `:8007` forwards.

### Phase 2 — Guest services via add-service

- Migrate apps with `./scripts/add-service.sh <name> <url>`.
- Standard middlewares only unless a service needs extras.

### Phase 3 — Hardening

- DNS-01 wildcard (optional), backend CA, SSO, CrowdSec, optional HA VIP.

---

## Repository layout

```text
.
├── README.md
├── gui/                       # Web admin UI (services + settings)
├── docs/
│   ├── architecture.md
│   ├── networking.md
│   └── runbook.md
├── config/
│   ├── base.env.example       # DOMAIN, ACME_EMAIL, …
│   ├── traefik.yml            # static: entrypoints + ACME
│   └── dynamic/
│       ├── middlewares.yml
│       ├── pve.yml
│       └── apps/
│           ├── _template.yml
│           ├── gate.yml       # route to the admin GUI
│           └── whoami.yml
├── scripts/
│   └── add-service.sh         # CLI subdomain route generator
└── deploy/
    ├── sync-config.sh
    └── gate-admin.service
```

---

## Open decisions

1. **Domain** and whether wildcard DNS is available.
2. **HTTP-01 vs DNS-01** (DNS provider API for wildcard).
3. Public internet vs LAN-only gate.
4. Traefik binary vs Docker-in-LXC.
5. SSO timing.

---

## Success criteria

- New service = subdomain + one `add-service` (or one YAML); no cert ceremony.
- Certs issue and renew without operator intervention.
- `https://<name>.<domain>` distinguishes services cleanly.
- PVE UI/consoles work through the gate; backend ports not public.
