# Gate admin GUI

Web UI to add/edit/delete subdomain services and change domain / ACME settings. Writes the same Traefik drop-in files as `scripts/add-service.sh`.

For full Proxmox deployment (LXC + Traefik + this GUI), see **[docs/install-proxmox.md](../docs/install-proxmox.md)**.

## Run locally

```bash
cd /path/to/Proxmox-Reverse-Proxy
python3 -m venv .venv
source .venv/bin/activate
pip install -r gui/requirements.txt
cp config/base.env.example config/base.env   # set DOMAIN
python -m gui
# open http://127.0.0.1:8080
```

First visit creates the admin user (stored in `config/gui.env`, gitignored).

## Run on the Traefik LXC

```bash
export GATE_REPO_ROOT=/opt/gate
export GATE_SESSION_SECRET='long-random-string'
export GATE_HTTPS_ONLY=1
python -m gui
```

Publish it through Traefik as `gate.<domain>` using `config/dynamic/apps/gate.yml` (point upstream at the GUI port).

## What it manages

| UI | On disk |
|---|---|
| Services CRUD | `config/dynamic/apps/<name>.yml` |
| PVE upstream edit | `config/dynamic/pve.yml` |
| Domain / ACME email / staging | `config/base.env`, `config/traefik.yml` |
| Admin login | `config/gui.env` |
