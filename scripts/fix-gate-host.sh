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

# Always ensure error-pages exists first — gate router is rejected without it.
# Do this before any git pull so a dirty gate.yml cannot block the fix.
mkdir -p /opt/gate/config/dynamic
cat >/opt/gate/config/dynamic/error-pages.yml <<'EOF'
# Shared Traefik service used by the gate-errors middleware (403 HTML).
http:
  services:
    gate-error-pages:
      loadBalancer:
        servers:
          - url: "http://127.0.0.1:8080"
EOF
chown gate:gate /opt/gate/config/dynamic/error-pages.yml 2>/dev/null || true
echo "Ensured config/dynamic/error-pages.yml"

# Keep base.env in sync so future update-gate.sh runs stay correct.
if [[ -f /opt/gate/config/base.env ]]; then
  if grep -qE '^DOMAIN=' /opt/gate/config/base.env; then
    sed -i -E "s|^DOMAIN=.*|DOMAIN=${DOMAIN}|" /opt/gate/config/base.env
  else
    echo "DOMAIN=${DOMAIN}" >>/opt/gate/config/base.env
  fi
fi

rewrite_hosts() {
  local f=$1
  [[ -f $f ]] || return 0
  local base name
  base=$(basename "$f" .yml)
  case $base in
    gate) name=gate ;;
    whoami) name=whoami ;;
    pve) name=pve ;;
    *) return 0 ;;
  esac
  sed -i -E "s|Host\(\`${name}\.[^\`]+\`\)|Host(\`${name}.${DOMAIN}\`)|g" "$f"
  sed -i "s/example\.com/${DOMAIN}/g" "$f"
  echo "-- $f --"
  grep -n 'Host(' "$f" || true
}

echo "== dynamic provider path =="
ls -la /etc/traefik/dynamic /etc/traefik/dynamic/apps 2>/dev/null || true
if [[ -L /etc/traefik/dynamic ]]; then
  echo "OK: /etc/traefik/dynamic -> $(readlink /etc/traefik/dynamic)"
elif [[ -d /etc/traefik/dynamic ]]; then
  echo "WARN: /etc/traefik/dynamic is a real directory (not a symlink)."
  echo "      Edits under /opt/gate/config/dynamic may NOT reach Traefik."
fi

for f in /opt/gate/config/dynamic/apps/gate.yml \
         /opt/gate/config/dynamic/apps/whoami.yml \
         /opt/gate/config/dynamic/pve.yml; do
  rewrite_hosts "$f"
done

# If Traefik uses a separate copy, rewrite that too.
if [[ -d /etc/traefik/dynamic/apps ]] && [[ ! /etc/traefik/dynamic -ef /opt/gate/config/dynamic ]]; then
  echo "NOTE: syncing Host() into /etc/traefik/dynamic/apps (not the same tree as the repo)"
  for f in /etc/traefik/dynamic/apps/gate.yml \
           /etc/traefik/dynamic/apps/whoami.yml \
           /etc/traefik/dynamic/pve.yml; do
    rewrite_hosts "$f"
  done
fi

systemctl restart gate-admin
systemctl restart traefik
sleep 2

echo "== gate-admin =="
systemctl is-active gate-admin
curl -sS -o /dev/null -w "local /login -> %{http_code}\n" http://127.0.0.1:8080/login || true

# sniStrict requires the real hostname in SNI — use --resolve, not Host: on 127.0.0.1
echo "== traefik https://gate.${DOMAIN}/ (SNI via --resolve) =="
code=$(curl -skS -o /tmp/gate-host.body -w "%{http_code}" \
  --resolve "gate.${DOMAIN}:443:127.0.0.1" \
  "https://gate.${DOMAIN}/" || echo "err")
ctype=$(curl -skS -o /dev/null -w "%{content_type}" \
  --resolve "gate.${DOMAIN}:443:127.0.0.1" \
  "https://gate.${DOMAIN}/" 2>/dev/null || true)
echo "HTTP ${code} ctype=${ctype}"
head -c 200 /tmp/gate-host.body 2>/dev/null; echo

if [[ $code == 404 ]]; then
  echo "STILL 404 — dump routers Traefik loaded and recent errors:"
  echo "-- gate.yml (repo) --"; cat /opt/gate/config/dynamic/apps/gate.yml
  if [[ -f /etc/traefik/dynamic/apps/gate.yml ]]; then
    echo "-- gate.yml (etc) --"; cat /etc/traefik/dynamic/apps/gate.yml
  fi
  journalctl -u traefik -n 50 --no-pager | grep -iE 'error|gate|404|acme' || journalctl -u traefik -n 30 --no-pager
  exit 1
fi
if [[ $code == err || $code == 000 ]]; then
  echo "TLS/connect failed. ACME may still be issuing. Check:"
  echo "  journalctl -u traefik -n 80 --no-pager | grep -i acme"
  exit 1
fi
echo "OK — try https://gate.${DOMAIN}/ (expect setup/login, not 404)."
