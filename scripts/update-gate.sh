#!/usr/bin/env bash
# Update /opt/gate from origin/main without losing DOMAIN / PVE upstream.
# Local sed edits (domain, allowlist experiments) often block `git pull` —
# this script resets known template files, pulls, then re-applies DOMAIN.
#
# Run inside the Gate CT as root:
#   bash /opt/gate/scripts/update-gate.sh
set -euo pipefail

ROOT=/opt/gate
cd "$ROOT"

if [[ ! -f config/base.env ]]; then
  echo "Missing $ROOT/config/base.env — cannot recover DOMAIN" >&2
  exit 1
fi
DOMAIN=$(grep -E '^DOMAIN=' config/base.env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
DOMAIN=${DOMAIN:-}
if [[ -z $DOMAIN ]]; then
  echo "DOMAIN empty in config/base.env" >&2
  exit 1
fi

# Preserve PVE upstream URL if present before reset.
PVE_URL=""
if [[ -f config/dynamic/pve.yml ]]; then
  PVE_URL=$(grep -E '^\s*-\s*url:\s*"https://' config/dynamic/pve.yml | head -1 | sed -E 's/.*url:\s*"([^"]+)".*/\1/' || true)
fi

echo "== discarding local drift on template route files (DOMAIN will be re-applied) =="
# These are shipped templates; live custom services under apps/*.yml stay.
runuser -u gate -- git -C "$ROOT" checkout -- \
  config/dynamic/apps/gate.yml \
  config/dynamic/apps/whoami.yml \
  config/dynamic/pve.yml \
  config/dynamic/middlewares.yml \
  2>/dev/null || true

echo "== git pull =="
runuser -u gate -- git -C "$ROOT" pull --ff-only origin main

echo "== re-apply DOMAIN=${DOMAIN} =="
sed -i "s/example\.com/${DOMAIN}/g" \
  config/dynamic/apps/gate.yml \
  config/dynamic/pve.yml \
  config/dynamic/apps/whoami.yml 2>/dev/null || true

if [[ -n $PVE_URL ]]; then
  sed -i -E "s|url: \"https://[^\"]+\"|url: \"${PVE_URL}\"|" config/dynamic/pve.yml
  echo "Restored PVE upstream: ${PVE_URL}"
fi

echo "== GUI deps =="
runuser -u gate -- "$ROOT/.venv/bin/pip" install -r "$ROOT/gui/requirements.txt"

bash "$ROOT/scripts/make-gate-public.sh"
