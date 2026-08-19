# Security model

How the Gate protects a Proxmox instance, what it trusts, and what the
operator must still do. Read together with [install-proxmox.md](install-proxmox.md).

## Protected assets

- Proxmox VE web UI / API (`:8006`) — hypervisor credentials, tickets,
  CSRF-prevention tokens, console sessions
- Guest application services on the internal network
- Traefik ACME account and issued certificates (`acme.json`)
- Gate GUI admin credential (`config/gui.env`) and session secret
- The Traefik configuration itself (whoever writes it controls routing)

## Trust boundaries

```text
Internet ──(untrusted)──> Traefik :80/:443 ──(semi-trusted LAN)──> backends
                              │
                              └──> 127.0.0.1:8080 Gate GUI (config writer)
```

| Boundary | Policy |
|---|---|
| Client → Traefik | TLS 1.2+ (dynamic/tls.yml), sniStrict, security headers; client-supplied `X-Forwarded-*` is dropped by Traefik unless `forwardedHeaders.trustedIPs` is configured |
| Traefik → PVE | HTTPS, **certificate verified** against `/etc/traefik/certs/pve-root-ca.pem` (never `insecureSkipVerify`) |
| Traefik → guest apps | Plain HTTP on the isolated services network, or verified HTTPS |
| Traefik → Gate GUI | Localhost only; GUI never binds a public interface by default |
| Gate GUI → Traefik config | Strict input allowlists; only complete rendered route files are written |

## Authentication

| Surface | Mechanism |
|---|---|
| Proxmox UI/API | Native PVE auth (tickets, CSRF tokens) passes through the proxy untouched; the gate adds no header rewriting on that path |
| Gate GUI | Single local admin account: bcrypt (cost 12), constant-time username/password evaluation, per-user and per-IP lockout (5 failures → 15 min), session rotation on login, CSRF token on every state-changing form, `SameSite=Lax` + `HttpOnly` (+`Secure` with `GATE_HTTPS_ONLY=1`) cookies, 8 h session lifetime |
| Traefik edge | `admin-allowlist` (direct peer IP) + `auth-ratelimit` middlewares on `gate.<domain>`; recommended on `pve.<domain>` |

### First-run window

Until the first admin account is created, `/setup` will accept whoever
reaches it first. Mitigations: the shipped `gate.yml` route is behind
`admin-allowlist`, and the GUI binds to `127.0.0.1`. **Complete setup
immediately after enabling the service.**

## What the GUI can and cannot write

The GUI is a config generator, not a general YAML editor:

- Service names: DNS labels (`[a-z0-9-]`, max 63), reserved names blocked
- Upstreams: `http(s)://host[:port][/path]` with a strict character
  allowlist — no credentials, query strings, quotes, backticks, whitespace,
  or control characters (blocks Traefik-config/Host-rule injection)
- Domain: validated DNS name; changing it rewrites `Host()` rules
- Deleting the `pve` system route from the GUI is not possible
- `gui.env` / `base.env` are written with mode `600`; values may not
  contain newlines (blocks env-file smuggling into systemd)

An authenticated GUI admin can still point a subdomain at any address the
gate can reach (that is its job). Keep the services network segmented so
"any address the gate can reach" is the set you intend.

## Forwarded headers and client IPs

- Traefik does not trust incoming `X-Forwarded-*` from clients; it sets its
  own values. Only configure `entryPoints.websecure.forwardedHeaders.trustedIPs`
  if a load balancer/CDN you control sits in front, and list exactly those
  addresses.
- `ipAllowList` middlewares match on the direct TCP peer, not on forwarded
  headers.
- The GUI's login throttle keys on the direct peer address and the attempted
  username; it deliberately ignores `X-Forwarded-For`.

## Denial of service

- `web` entrypoint: 30 s read timeout (only redirects + ACME).
- `websecure`: read/write timeouts disabled **by design** — Proxmox ISO
  uploads, backup restores, and noVNC WebSockets are long-lived; slow-client
  abuse is bounded by the 180 s idle timeout, `auth-ratelimit`, and
  `gate-buffering` (1 MiB body cap on the admin UI).
- GUI lockout limits credential stuffing regardless of edge rate limits.

## Logging

- Traefik access logs contain request metadata only; header logging stays
  off so cookies, tickets, and `Authorization` values never reach disk.
- The GUI logs auth successes/failures (username + direct peer IP only),
  lockouts, CSRF rejections, and config changes. Passwords, hashes, and
  session tokens are never logged.

## Secrets on disk

| File | Mode | Contents |
|---|---|---|
| `/opt/gate/config/gui.env` | `600` | admin username + bcrypt hash |
| `/etc/gate-admin.env` | `600` | `GATE_SESSION_SECRET` |
| `/var/lib/traefik/acme.json` | `600` | ACME account + private keys |
| `/etc/traefik/certs/pve-root-ca.pem` | `644` | public CA cert (not secret) |

The systemd unit (`deploy/gate-admin.service`) runs as the unprivileged
`gate` user with `ProtectSystem=strict`, no capabilities, and only
`/opt/gate/config` writable. The password hash is read from disk by the app
and is not exported into the process environment.

## Residual risks (by design or requiring operator action)

1. **Compromised Gate LXC = compromised routing.** The gate terminates TLS
   for everything behind it. Keep the LXC minimal, updated, and backed up.
2. **PVE console/API pass-through.** The proxy intentionally does not add
   auth in front of PVE (breaking tickets/CSRF/noVNC); protect `pve.<domain>`
   with `admin-allowlist` or VPN-only DNS.
3. **Single-admin GUI.** No roles/audit trail beyond logs; suitable for a
   homelab operator, not multi-tenant delegation.
4. **In-memory lockout state** resets on GUI restart. Edge `auth-ratelimit`
   still applies.

## Verification commands

```bash
# tests (GUI)
.venv/bin/pip install -r gui/requirements-dev.txt
.venv/bin/python -m pytest gui/tests

# Traefik config sanity (inside the LXC)
traefik --configFile=/etc/traefik/traefik.yml --dry-run 2>/dev/null || journalctl -u traefik -n 20

# backend chain verification
curl --cacert /etc/traefik/certs/pve-root-ca.pem -sI https://<pve-ip>:8006 | head -1

# edge TLS floor
openssl s_client -connect gate.<domain>:443 -tls1_1 </dev/null 2>&1 | grep -q "no protocols available" && echo "TLS 1.1 rejected OK"
```
