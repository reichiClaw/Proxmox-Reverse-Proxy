#!/usr/bin/env bash
# Sync this repo's Traefik config into the proxy LXC.
#
# Usage:
#   TRAEFIK_LXC=101 ./deploy/sync-config.sh
#   TRAEFIK_HOST=root@10.10.10.10 ./deploy/sync-config.sh
#
# Prefers pct push when TRAEFIK_LXC is set on the Proxmox host;
# otherwise uses rsync/scp over SSH via TRAEFIK_HOST.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_DIR="${REMOTE_DIR:-/etc/traefik}"

if [[ -n "${TRAEFIK_LXC:-}" ]]; then
  echo "Syncing to LXC ${TRAEFIK_LXC}:${REMOTE_DIR} via pct"
  pct exec "$TRAEFIK_LXC" -- mkdir -p "$REMOTE_DIR/dynamic/apps" /var/lib/traefik
  pct push "$TRAEFIK_LXC" "${ROOT}/config/traefik.yml" "${REMOTE_DIR}/traefik.yml"
  pct push "$TRAEFIK_LXC" "${ROOT}/config/dynamic/middlewares.yml" "${REMOTE_DIR}/dynamic/middlewares.yml"
  pct push "$TRAEFIK_LXC" "${ROOT}/config/dynamic/pve.yml" "${REMOTE_DIR}/dynamic/pve.yml"
  for f in "${ROOT}/config/dynamic/apps/"*.yml; do
    base="$(basename "$f")"
    [[ "$base" == _template.yml ]] && continue
    pct push "$TRAEFIK_LXC" "$f" "${REMOTE_DIR}/dynamic/apps/${base}"
  done
  pct exec "$TRAEFIK_LXC" -- chmod 600 /var/lib/traefik/acme.json 2>/dev/null || true
  echo "Done. File provider watch should hot-reload routes."
  exit 0
fi

if [[ -n "${TRAEFIK_HOST:-}" ]]; then
  echo "Syncing to ${TRAEFIK_HOST}:${REMOTE_DIR} via rsync"
  rsync -av --delete \
    --exclude '_template.yml' \
    --exclude 'base.env' \
    --exclude 'base.env.example' \
    "${ROOT}/config/traefik.yml" \
    "${TRAEFIK_HOST}:${REMOTE_DIR}/traefik.yml"
  rsync -av --delete \
    --exclude '_template.yml' \
    "${ROOT}/config/dynamic/" \
    "${TRAEFIK_HOST}:${REMOTE_DIR}/dynamic/"
  echo "Done. File provider watch should hot-reload routes."
  exit 0
fi

echo "Set TRAEFIK_LXC=<vmid> (on Proxmox host) or TRAEFIK_HOST=user@proxy" >&2
exit 1
