# Proxmox TLS Gateway — Architecture Plan

A single reverse proxy sits in front of this Proxmox instance and becomes the **only public TLS entry point** for the hypervisor UI and all published services running in VMs / LXCs.

## Goals

- **One gate**: Internet / LAN clients hit only `:443` (and `:80` for ACME redirects) on the proxy.
- **TLS everywhere at the edge**: Terminate (or re-encrypt) TLS at the proxy; issue and renew certificates automatically.
- **Versatile routing**: Host-based (preferred) and optional path-based routes to Proxmox VE, PBS, and any app in guests.
- **Proxmox-aware**: Correct WebSocket / noVNC / xterm.js / API behavior for the PVE UI.
- **Least privilege**: Guests stay on internal networks; only the proxy is exposed.
- **Operable**: Declarative config, health checks, structured logs, easy add/remove of services.

## Non-goals (initial)

- Full mesh service mesh / mTLS between every guest (can be added later).
- Replacing Proxmox auth (SSO can be layered later via Authentik/Authelia).
- Exposing arbitrary non-HTTP protocols without an explicit TCP/UDP router.

---

## Recommended stack

| Layer | Choice | Why |
|---|---|---|
| Edge proxy | **Traefik v3** | Versatile: HTTP + TCP/UDP routers, ACME, middlewares, file/Docker providers, good WebSocket support, dashboard for ops |
| Runtime | **Debian LXC** (unprivileged, nesting if Docker needed) on the Proxmox host | Cheap, easy to back up, clear blast radius vs installing on the PVE host OS |
| Certificates | **Let’s Encrypt** (DNS-01 or HTTP-01) via Traefik ACME | Automatic renewals; DNS-01 if wildcard or no inbound `:80` |
| Internal DNS | Pi-hole / AdGuard / CoreDNS / router DNS | `*.lab.example.com` → proxy VIP |
| Optional auth gate | Authelia or Authentik (phase 2) | SSO / 2FA in front of non-Proxmox apps |
| Optional WAF | CrowdSec Traefik bouncer (phase 2) | Abuse / brute-force filtering |

**Alternatives considered**

- **Caddy**: Excellent automatic HTTPS, simpler config; less flexible for mixed TCP routers and dynamic discovery.
- **Nginx Proxy Manager**: Friendly UI; weaker as infrastructure-as-code and for advanced TCP/middleware cases.
- **HAProxy**: Outstanding performance; more manual TLS/ACME and less “versatile” out of the box.

Traefik is the default recommendation for *versatile* multi-service Proxmox labs.

---

## High-level topology

```text
                    Internet / LAN clients
                              |
                         :80 / :443
                              |
                    +---------+---------+
                    |  Traefik (LXC)    |   TLS termination / re-encrypt
                    |  Main Gate        |
                    +---------+---------+
                              |
           +------------------+------------------+
           |                  |                  |
      pve.example.com   app.example.com    git.example.com
           |                  |                  |
      Proxmox VE :8006   App LXC/VM :8080   Gitea VM :3000
      (HTTPS backend)    (HTTP backend)     (HTTP backend)
```

### Network model (recommended)

1. **Front network** (`vmbr0` / WAN-facing or LAN VIP): Traefik binds `80/443` (and optionally a VIP via Keepalived later).
2. **Services network** (`vmbr1` or SDN/VLAN): Proxmox guests and management UIs reachable only from Traefik (and admin jump hosts).
3. Firewall on the Proxmox host / edge router:
   - Allow WAN/LAN → Traefik `:80/:443` only.
   - Allow Traefik → backends on required ports.
   - Deny direct WAN → PVE `:8006`, PBS `:8007`, guest app ports.

Keep a **break-glass** admin path (VPN or management VLAN) to PVE `:8006` if the proxy is down.

---

## TLS strategy (Main Gate)

### Edge certificates

- One certificate per hostname, **or** a wildcard `*.lab.example.com` via DNS-01.
- Traefik ACME storage on a persistent volume (`/var/lib/traefik/acme.json`, mode `600`).
- Force HTTPS redirect from `:80` → `:443`.
- Modern TLS only (TLS 1.2+), strong cipher suites (Traefik defaults are fine; tighten later if needed).
- HSTS enabled on public hostnames once cutover is stable.

### Backend TLS

| Backend | Mode | Notes |
|---|---|---|
| Proxmox VE / PBS | HTTPS → HTTPS (re-encrypt) | PVE serves TLS on 8006/8007; use `serversTransport` with custom CA or `insecureSkipVerify` only on trusted internal nets |
| Typical apps | HTTPS → HTTP | Terminate at Traefik; plain HTTP on services net |
| Sensitive apps | HTTPS → HTTPS | Guest has its own cert (internal CA) |

### Hostname convention

```text
pve.<domain>      → Proxmox VE UI + API
pbs.<domain>      → Proxmox Backup Server (if present)
*.<domain>        → guest services (one hostname per published app)
```

Prefer **subdomain-per-service** over path-based routing; it avoids cookie/path breakage and certificate ambiguity.

---

## Routing model

### Providers

Use **file provider** (YAML) as source of truth for Proxmox labs:

- Stable when guests are VMs/LXCs without Docker labels.
- Easy to version in Git (this repo).
- Optional: Docker provider later for a compose stack behind Traefik.

### Router pattern (per service)

Each published service declares:

1. **Entrypoint**: `websecure` (`:443`)
2. **Rule**: `Host(`app.example.com`)`
3. **TLS**: certresolver `le`
4. **Service**: upstream URL(s) + health check
5. **Middlewares** (as needed): headers, compress, auth, IP allowlist, rate limit

### TCP / non-HTTP

For SSH, game servers, databases, MQTT, etc.:

- Dedicated Traefik **TCP routers** with SNI or dedicated ports.
- Or publish only via VPN — preferred for admin protocols.

Do **not** put raw SSH to the Proxmox host on the public gate unless required; use VPN/WireGuard.

---

## Proxmox VE behind the proxy (critical details)

Proxmox UI and API need:

- WebSocket upgrade support (noVNC, xterm.js consoles)
- Long-lived connections / adequate timeouts
- Correct `Host` / `X-Forwarded-*` headers
- Sticky behavior is usually unnecessary for a single node; for clusters, pin to the node that owns the resource or use the cluster endpoint carefully

### Traefik service sketch

```yaml
http:
  routers:
    pve:
      rule: "Host(`pve.example.com`)"
      entryPoints: ["websecure"]
      tls:
        certResolver: le
      service: pve
      middlewares: ["pve-headers"]

  middlewares:
    pve-headers:
      headers:
        customRequestHeaders:
          X-Forwarded-Proto: "https"

  services:
    pve:
      loadBalancer:
        servers:
          - url: "https://172.16.0.2:8006"
        serversTransport: pve-transport
        passHostHeader: true

  serversTransports:
    pve-transport:
      insecureSkipVerify: true   # replace with rootCAs once internal CA is ready
```

### PVE-side expectations

- Datacenter → Options → **ACM E / reverse proxy** related settings: ensure the node knows it may be reached via the external hostname where relevant (e.g. cookie / link generation).
- Prefer leaving PVE listening on the services network only; do not port-forward `8006` publicly.
- API tokens / scripts should target `https://pve.example.com` after cutover.

---

## Deployment layout on Proxmox

### LXC profile

| Setting | Value |
|---|---|
| Template | Debian 12/13 |
| Unprivileged | Yes |
| Nesting | On if using Docker inside |
| CPU / RAM | 1–2 vCPU, 512 MB–1 GB |
| Disk | 4–8 GB + bind-mount for certs/config |
| NICs | eth0 on services net; optional second NIC on front net |
| Features | Static IP; firewall enabled |

### On-disk layout (inside Traefik LXC)

```text
/etc/traefik/
  traefik.yml           # static config: entrypoints, ACME, providers
  dynamic/
    middlewares.yml
    pve.yml
    pbs.yml
    apps/
      gitea.yml
      ...
/var/lib/traefik/
  acme.json
/var/log/traefik/
  access.log
```

This repository mirrors that tree under `config/` so changes are reviewed via PR and deployed by sync/CI.

### Process supervision

- Official Traefik binary or package, managed by **systemd**.
- Or Docker Compose in the LXC if you want image-based upgrades — keep volumes for `acme.json` and `dynamic/`.

---

## Security model

1. **Exposure**: Only Traefik ports publicly reachable.
2. **Admin UIs**: IP allowlist middleware and/or VPN for `pve.`, `pbs.`, Traefik dashboard.
3. **Auth**: Phase 1 = native app auth; Phase 2 = ForwardAuth (Authelia/Authentik) for selected apps. Be careful putting SSO in front of PVE itself until tested — console/API breakage is common; prefer VPN/allowlist for PVE.
4. **Certificates**: Restrict permissions on `acme.json`; back it up encrypted.
5. **Headers**: Trusted `X-Forwarded-For` only from Traefik; guests must not be directly reachable from untrusted networks.
6. **Updates**: Unattended security updates on the Traefik LXC; Traefik version pinned and upgraded deliberately.
7. **Observability**: Access logs + Prometheus metrics endpoint (internal only).

---

## Service onboarding checklist

For each new guest service:

1. Assign internal IP / DNS name on the services network.
2. Choose public hostname `app.<domain>`.
3. Add a dynamic Traefik file (router + service + middlewares).
4. Create DNS A/AAAA (or CNAME) → Traefik.
5. Wait for ACME issuance; verify HTTPS and WebSockets if needed.
6. Close any direct port forwards that bypass the gate.
7. Document owner, upstream URL, and auth mode in the service catalog.

---

## Phased delivery

### Phase 0 — Foundations

- Create Traefik LXC, networks, DNS zone, firewall baseline.
- Deploy static Traefik config with ACME (staging first).
- Publish a trivial whoami/health service for end-to-end validation.

### Phase 1 — Main Gate for Proxmox

- Route `pve.<domain>` (and `pbs.<domain>` if applicable).
- Validate UI login, node status, noVNC, xterm.js, API calls.
- Remove public DNAT to `:8006` / `:8007`.
- Keep VPN break-glass access.

### Phase 2 — Guest services

- Migrate existing published apps behind host-based routes.
- Standard middlewares: compress, security headers, optional allowlists.
- Catalog all routes in Git.

### Phase 3 — Hardening & extras

- Internal CA for backend re-encrypt (drop `insecureSkipVerify`).
- Optional ForwardAuth SSO for non-PVE apps.
- CrowdSec / rate limits / geo blocks as needed.
- Optional HA: second Traefik LXC + Keepalived VIP + shared cert storage strategy.

---

## Repository layout (proposed)

```text
.
├── README.md
├── docs/
│   ├── architecture.md          # this plan
│   ├── networking.md            # VLANs, IPs, firewall rules
│   └── runbook.md               # issue certs, add service, restore
├── config/
│   ├── traefik.yml              # static config
│   └── dynamic/
│       ├── middlewares.yml
│       ├── pve.yml
│       └── apps/
├── deploy/
│   ├── lxc-create.sh            # idempotent LXC bootstrap notes/script
│   └── sync-config.sh           # push config into Traefik LXC
└── examples/
    └── whoami.yml
```

---

## Open decisions (need environment specifics)

Capture these before implementation:

1. **Domain / DNS provider** (drives HTTP-01 vs DNS-01 and wildcard feasibility).
2. **Public vs LAN-only gate** (split-horizon DNS? VPN-only admin?).
3. **Single node vs cluster** (routing to local node vs cluster VIP).
4. **IPv6** required or v4-only.
5. **Docker-in-LXC** vs bare Traefik binary.
6. **SSO** in scope for phase 2 or later.
7. **Existing port forwards** inventory to migrate.

---

## Success criteria

- All intended services reachable only via `https://<service>.<domain>`.
- Valid public certificates with automatic renewal.
- Proxmox UI fully usable (including consoles) through the proxy.
- Direct exposure of backend ports removed from the edge firewall.
- Adding a new service is a small Git change + DNS record, without firewall sprawl.
