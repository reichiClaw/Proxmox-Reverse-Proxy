#!/usr/bin/env bash
# Disable Traefik admin-allowlist on the Gate route so gate.<domain> is
# reachable from the internet (password / TOTP protect the GUI).
# Run inside the Gate CT as root:
#   bash /opt/gate/scripts/make-gate-public.sh
set -euo pipefail

GATE_YML=/opt/gate/config/dynamic/apps/gate.yml
TRAEFIK_YML=/etc/traefik/dynamic/apps/gate.yml

echo "== paths =="
ls -la /etc/traefik/dynamic 2>/dev/null || true
ls -la "$GATE_YML" "$TRAEFIK_YML" 2>/dev/null || true

disable_allowlist() {
  local f=$1
  [[ -f $f ]] || return 0
  # Comment any active allowlist attachment (leave the commented example).
  sed -i -E 's/^([[:space:]]*)-[[:space:]]+admin-allowlist[[:space:]]*$/\1# - admin-allowlist/' "$f"
  echo "-- $f middlewares --"
  grep -n -E 'admin-allowlist|middlewares:|^[[:space:]]+- ' "$f" | head -40
}

disable_allowlist "$GATE_YML"
# If Traefik uses a real copy (not a symlink), fix that file too.
if [[ -e $TRAEFIK_YML ]] && [[ ! $TRAEFIK_YML -ef $GATE_YML ]]; then
  echo "NOTE: /etc/traefik/dynamic/apps/gate.yml is not the same inode as the repo file"
  disable_allowlist "$TRAEFIK_YML"
fi

systemctl restart gate-admin
systemctl restart traefik
sleep 1

echo "== local GUI =="
curl -sS -o /dev/null -w "http://127.0.0.1:8080/login -> %{http_code}\n" http://127.0.0.1:8080/login || true

echo "== via Traefik (Host gate) =="
# Prefer the live DOMAIN from base.env when present.
host="gate.example.com"
if [[ -f /opt/gate/config/base.env ]]; then
  # shellcheck disable=SC1091
  domain=$(grep -E '^DOMAIN=' /opt/gate/config/base.env | head -1 | cut -d= -f2- | tr -d '"' || true)
  [[ -n ${domain:-} ]] && host="gate.${domain}"
fi
curl -skS -o /tmp/gate-probe.body -w "https://127.0.0.1/ (Host: ${host}) -> %{http_code} ctype=%{content_type}\n" \
  -H "Host: ${host}" https://127.0.0.1/ || true
if grep -qi '^Forbidden$' /tmp/gate-probe.body 2>/dev/null; then
  echo "STILL Forbidden — Traefik is rejecting the client IP. Recent 403s:"
  journalctl -u traefik -n 30 --no-pager | grep 403 || true
  exit 1
fi
echo "OK — open https://${host}/ in the browser (expect login or setup, not a Forbidden download)."
