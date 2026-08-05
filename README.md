# Proxmox Reverse Proxy

TLS **Main Gate** for a Proxmox instance: one Traefik proxy terminates HTTPS and routes by **subdomain** to Proxmox VE and all guest services. Certificates are **self-maintained** (issued and renewed by Traefik ACME).

## Quick mental model

```text
https://gate.<domain>      → Admin GUI (add services & settings)
https://pve.<domain>       → Proxmox VE
https://gitea.<domain>     → guest app
https://<name>.<domain>    → any new service
```

Wildcard DNS `*.<domain>` → Traefik IP. Adding a service does not require a new DNS record or a manual certificate.

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

## Docs

| Doc | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Full design: topology, TLS, security, phases |
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
