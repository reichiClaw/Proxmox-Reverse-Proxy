# Runbook

## One-time setup

1. Copy `config/base.env.example` → `config/base.env` and set `DOMAIN` + `ACME_EMAIL`.
2. Set the same ACME email in `config/traefik.yml`.
3. Point wildcard DNS `*.<DOMAIN>` (and optionally apex) at the Traefik IP.
4. Deploy Traefik with `config/` mounted/synced to `/etc/traefik`.
5. Keep ACME on **staging** until `https://whoami.<DOMAIN>` works, then switch to production CA in `traefik.yml`.

## Add a service (normal path)

```bash
./scripts/add-service.sh <name> <upstream-url>
# example:
./scripts/add-service.sh gitea http://10.10.10.20:3000

TRAEFIK_LXC=101 ./deploy/sync-config.sh   # or TRAEFIK_HOST=...
```

Result:

- Route: `https://<name>.<DOMAIN>`
- Certificate: requested and renewed by Traefik ACME automatically
- No new firewall rule if the guest is already on the services network
- No new DNS record if wildcard `*.<DOMAIN>` already points at Traefik

## Add a service (manual)

1. Copy `config/dynamic/apps/_template.yml` → `apps/<name>.yml`
2. Replace `SERVICE_NAME`, `DOMAIN`, `UPSTREAM_URL`
3. Sync / wait for hot-reload

## Remove a service

```bash
rm config/dynamic/apps/<name>.yml
./deploy/sync-config.sh
```

Traefik drops the router on reload. Certificate entries in `acme.json` can remain harmlessly.

## Certificates (self-maintained)

| Task | Action |
|---|---|
| New subdomain | None — ACME runs when the router appears |
| Renewal | Automatic |
| Force re-issue | Delete host entry from `acme.json` or temporarily rename the route (prefer waiting) |
| Wildcard | Configure DNS-01 in `traefik.yml` (see comments) |
| Backup | Include `/var/lib/traefik/acme.json` in LXC backups (mode `600`) |

## Proxmox UI

1. Edit `config/dynamic/pve.yml` host + upstream IP.
2. Sync; open `https://pve.<DOMAIN>`.
3. Verify login, node view, noVNC / xterm.js.
4. Remove public DNAT to `:8006`; keep VPN break-glass.
