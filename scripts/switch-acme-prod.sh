#!/usr/bin/env bash
# Flip Traefik from Let’s Encrypt staging → production and force re-issue.
# Run inside the Gate CT as root after whoami/gate work on staging:
#   bash /opt/gate/scripts/switch-acme-prod.sh
set -euo pipefail

TRAEFIK_YML=/opt/gate/config/traefik.yml
ETC_YML=/etc/traefik/traefik.yml
ACME=/var/lib/traefik/acme.json
PROD=https://acme-v02.api.letsencrypt.org/directory

if [[ ! -f $TRAEFIK_YML ]]; then
  echo "Missing $TRAEFIK_YML" >&2
  exit 1
fi

# Point the active (uncommented) caServer at production.
sed -i -E \
  's|^([[:space:]]*caServer:[[:space:]]*).+$|\1'"${PROD}"'|' \
  "$TRAEFIK_YML"

# If Traefik reads a separate copy, sync it.
if [[ -e $ETC_YML ]] && [[ ! $ETC_YML -ef $TRAEFIK_YML ]]; then
  echo "NOTE: $ETC_YML is not a symlink to the repo file — patching it too"
  sed -i -E \
    's|^([[:space:]]*caServer:[[:space:]]*).+$|\1'"${PROD}"'|' \
    "$ETC_YML"
fi

echo "== active caServer line(s) =="
grep -nE '^[[:space:]]*caServer:' "$TRAEFIK_YML" || true

echo "== clearing staging ACME store (backed up) =="
systemctl stop traefik
if [[ -f $ACME ]]; then
  mv "$ACME" "${ACME}.staging.bak.$(date +%Y%m%d%H%M%S)"
fi
touch "$ACME"
chmod 600 "$ACME"
systemctl start traefik

echo "Traefik restarted. Hit https://whoami.<domain> and https://gate.<domain> once"
echo "to trigger issuance, then check the browser padlock (should be trusted)."
echo "Logs: journalctl -u traefik -f | grep -i acme"
