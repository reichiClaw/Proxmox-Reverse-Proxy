# Proxmox Reverse Proxy

TLS **Main Gate** for a Proxmox instance: one Traefik proxy terminates HTTPS and routes by **subdomain** to Proxmox VE and all guest services. Certificates are **self-maintained** (issued and renewed by Traefik ACME).

## Quick mental model

```text
https://pve.<domain>       → Proxmox VE
https://gitea.<domain>     → guest app
https://<name>.<domain>    → any new service
```

Wildcard DNS `*.<domain>` → Traefik IP. Adding a service does not require a new DNS record or a manual certificate.

## Add a service

```bash
cp config/base.env.example config/base.env   # once — set DOMAIN
./scripts/add-service.sh gitea http://10.10.10.20:3000
TRAEFIK_LXC=101 ./deploy/sync-config.sh      # or TRAEFIK_HOST=user@proxy
```

Traefik hot-reloads the drop-in under `config/dynamic/apps/` and obtains/renews TLS for that subdomain automatically.

## Docs

| Doc | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Full design: topology, TLS, security, phases |
| [docs/runbook.md](docs/runbook.md) | Add/remove services, certs, PVE cutover |
| [docs/networking.md](docs/networking.md) | Domains, IPs, firewall worksheet |

## Layout

```text
config/traefik.yml           # static: entrypoints + ACME
config/dynamic/apps/*.yml    # one file per subdomain/service
scripts/add-service.sh       # generate a route in one command
deploy/sync-config.sh        # push config to the Traefik LXC
```
