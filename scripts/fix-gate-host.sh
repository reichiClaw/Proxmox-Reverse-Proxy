#!/usr/bin/env bash
# Ensure the Gate Traefik router Host() matches DOMAIN from base.env
# (or $1). Fixes Traefik 404 on https://gate.<domain> after a template reset.
#   bash /opt/gate/scripts/fix-gate-host.sh
#   bash /opt/gate/scripts/fix-gate-host.sh rcserver.io
set -euo pipefail

DOMAIN=${1:-}
if [[ -z $DOMAIN && -f /opt/gate/config/base.env ]]; then
  DOMAIN=$(grep -E '^DOMAIN=' /opt/gate/config/base.env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
fi
if [[ -z ${DOMAIN:-} ]]; then
  echo "Usage: $0 <domain>   (e.g. $0 rcserver.io)" >&2
  exit 1
fi

# Keep base.env in sync so future update-gate.sh runs stay correct.
if [[ -f /opt/gate/config/base.env ]]; then
  if grep -qE '^DOMAIN=' /opt/gate/config/base.env; then
    sed -i -E "s|^DOMAIN=.*|DOMAIN=${DOMAIN}|" /opt/gate/config/base.env
  else
    echo "DOMAIN=${DOMAIN}" >>/opt/gate/config/base.env
  fi
fi

for f in /opt/gate/config/dynamic/apps/gate.yml \
         /opt/gate/config/dynamic/apps/whoami.yml \
         /opt/gate/config/dynamic/pve.yml; do
  [[ -f $f ]] || continue
  # Replace any Host(`….something`) subdomain prefix we care about.
  base=$(basename "$f" .yml)
  case $base in
    gate) name=gate ;;
    whoami) name=whoami ;;
    pve) name=pve ;;
    *) continue ;;
  esac
  sed -i -E "s|Host\(\`${name}\.[^\`]+\`\)|Host(\`${name}.${DOMAIN}\`)|g" "$f"
  # Fallback: any leftover example.com
  sed -i "s/example\.com/${DOMAIN}/g" "$f"
  echo "-- $f --"
  grep -n 'Host(' "$f" || true
done

systemctl restart gate-admin
# Traefik watches dynamic files; bounce it so a bad prior state is cleared.
systemctl restart traefik
sleep 1

echo "== gate-admin =="
systemctl is-active gate-admin
curl -sS -o /dev/null -w "local /login -> %{http_code}\n" http://127.0.0.1:8080/login || true

echo "== traefik Host gate.${DOMAIN} =="
curl -skS -o /tmp/gate-host.body -w "%{http_code} ctype=%{content_type}\n" \
  -H "Host: gate.${DOMAIN}" https://127.0.0.1/ || true
code=$(curl -skS -o /dev/null -w "%{http_code}" -H "Host: gate.${DOMAIN}" https://127.0.0.1/ || true)
if [[ $code == 404 ]]; then
  echo "Still 404 — Traefik routers:"
  journalctl -u traefik -n 40 --no-pager | tail -40
  exit 1
fi
echo "OK — try https://gate.${DOMAIN}/ (expect setup/login redirect, not 404)."
