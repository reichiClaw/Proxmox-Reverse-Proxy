#!/usr/bin/env bash
# One-shot installer for the Proxmox TLS Main Gate.
#
# Run ON THE PROXMOX VE HOST as root. It will:
#   1. Download a Debian 12 CT template (if missing) and create an
#      unprivileged LXC
#   2. Install Traefik (pinned version, checksum-verified) + the Gate admin
#      GUI inside the container, both as hardened systemd services
#   3. Configure your domain, ACME email, and the Proxmox upstream
#   4. Install the PVE cluster CA so backend TLS is VERIFIED
#      (never insecureSkipVerify)
#
# Usage (interactive prompts fill anything you omit):
#   ./install-proxmox.sh --vmid 110 --ip 192.168.1.10/24 --gateway 192.168.1.1 \
#       --domain lab.example.com --acme-email you@lab.example.com
#
# Options:
#   --vmid <n>            CT ID (required)
#   --ip <cidr>           Static IP with prefix, e.g. 192.168.1.10/24 (required)
#   --gateway <ip>        Default gateway (required)
#   --domain <domain>     Base domain for all services (required)
#   --acme-email <email>  Let's Encrypt account email (required)
#   --hostname <name>     CT hostname                    (default: gate)
#   --bridge <br>         Network bridge                 (default: vmbr0)
#   --storage <id>        CT rootfs storage              (default: local-lvm)
#   --template-storage <id> Template storage             (default: local)
#   --memory <MB>         CT memory                      (default: 1024)
#   --cores <n>           CT cores                       (default: 2)
#   --disk <GB>           CT rootfs size                 (default: 8)
#   --pve-upstream <url>  Proxmox UI upstream            (default: https://<host-ip>:8006)
#   --repo <url|path>     Gate repository to clone       (default: GitHub upstream)
#   --traefik-version <v> Traefik release                (default: v3.5.3)
#   --no-whoami           Skip the whoami smoke-test service
#   --no-firewall         Skip ufw configuration inside the CT
#   --dry-run             Print host-side actions without executing
#   --inside              (internal) container-side phase
#   -h, --help            This help

set -euo pipefail

REPO_DEFAULT="https://github.com/reichiClaw/Proxmox-Reverse-Proxy.git"

VMID=""
CT_HOSTNAME="gate"
BRIDGE="vmbr0"
STORAGE="local-lvm"
TEMPLATE_STORAGE="local"
MEMORY="1024"
CORES="2"
DISK="8"
CT_IP=""
GATEWAY=""
DOMAIN=""
ACME_EMAIL=""
PVE_UPSTREAM=""
REPO_URL="$REPO_DEFAULT"
TRAEFIK_VERSION="v3.5.3"
WITH_WHOAMI=1
WITH_FIREWALL=1
DRY_RUN=0
INSIDE=0

log()  { echo -e "\033[1;32m[gate]\033[0m $*"; }
warn() { echo -e "\033[1;33m[gate]\033[0m $*" >&2; }
die()  { echo -e "\033[1;31m[gate]\033[0m $*" >&2; exit 1; }

usage() { sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vmid) VMID="$2"; shift 2 ;;
    --hostname) CT_HOSTNAME="$2"; shift 2 ;;
    --bridge) BRIDGE="$2"; shift 2 ;;
    --storage) STORAGE="$2"; shift 2 ;;
    --template-storage) TEMPLATE_STORAGE="$2"; shift 2 ;;
    --memory) MEMORY="$2"; shift 2 ;;
    --cores) CORES="$2"; shift 2 ;;
    --disk) DISK="$2"; shift 2 ;;
    --ip) CT_IP="$2"; shift 2 ;;
    --gateway) GATEWAY="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --acme-email) ACME_EMAIL="$2"; shift 2 ;;
    --pve-upstream) PVE_UPSTREAM="$2"; shift 2 ;;
    --repo) REPO_URL="$2"; shift 2 ;;
    --traefik-version) TRAEFIK_VERSION="$2"; shift 2 ;;
    --no-whoami) WITH_WHOAMI=0; shift ;;
    --no-firewall) WITH_FIREWALL=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --inside) INSIDE=1; shift ;;
    -h|--help) usage 0 ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
done

validate_domain() {
  [[ "$1" =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z][a-z0-9-]*$ ]]
}

validate_email() {
  [[ "$1" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]
}

prompt_missing() {
  local var="$1" text="$2"
  if [[ -z "${!var}" ]]; then
    [[ -t 0 ]] || die "Missing required value: --${var,,} (non-interactive shell)"
    read -r -p "$text: " "${var?}"
  fi
}

# ---------------------------------------------------------------------------
# Container-side phase
# ---------------------------------------------------------------------------
inside_phase() {
  [[ -n "$DOMAIN" && -n "$ACME_EMAIL" && -n "$PVE_UPSTREAM" ]] \
    || die "--inside requires --domain, --acme-email and --pve-upstream"
  validate_domain "$DOMAIN" || die "Invalid domain: $DOMAIN"
  validate_email "$ACME_EMAIL" || die "Invalid ACME email: $ACME_EMAIL"

  export DEBIAN_FRONTEND=noninteractive

  log "Installing OS packages"
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl git python3 python3-venv \
    ufw jq dnsutils openssl >/dev/null

  log "Creating gate user and directories"
  id gate &>/dev/null || useradd --system --home /opt/gate --shell /usr/sbin/nologin gate
  mkdir -p /etc/traefik/certs /var/lib/traefik /var/log/traefik
  touch /var/lib/traefik/acme.json
  chmod 600 /var/lib/traefik/acme.json

  if [[ -f /tmp/pve-root-ca.pem ]]; then
    install -m 0644 /tmp/pve-root-ca.pem /etc/traefik/certs/pve-root-ca.pem
    log "Installed PVE cluster CA for verified backend TLS"
  elif [[ ! -f /etc/traefik/certs/pve-root-ca.pem ]]; then
    warn "No PVE root CA found — the pve route will fail closed until you copy"
    warn "/etc/pve/pve-root-ca.pem to /etc/traefik/certs/pve-root-ca.pem"
  fi

  local arch
  case "$(uname -m)" in
    x86_64) arch=amd64 ;;
    aarch64) arch=arm64 ;;
    *) die "Unsupported architecture: $(uname -m)" ;;
  esac

  if [[ ! -x /usr/local/bin/traefik ]] || ! /usr/local/bin/traefik version 2>/dev/null | grep -q "${TRAEFIK_VERSION#v}"; then
    log "Installing Traefik ${TRAEFIK_VERSION} (checksum-verified)"
    local tmp tarball
    tmp="$(mktemp -d)"
    tarball="traefik_${TRAEFIK_VERSION}_linux_${arch}.tar.gz"
    curl -fsSL -o "${tmp}/${tarball}" \
      "https://github.com/traefik/traefik/releases/download/${TRAEFIK_VERSION}/${tarball}"
    curl -fsSL -o "${tmp}/checksums.txt" \
      "https://github.com/traefik/traefik/releases/download/${TRAEFIK_VERSION}/traefik_${TRAEFIK_VERSION}_checksums.txt"
    (cd "$tmp" && sha256sum --check --ignore-missing checksums.txt >/dev/null) \
      || die "Traefik checksum verification FAILED — aborting."
    tar -C "$tmp" -xzf "${tmp}/${tarball}" traefik
    install -m 0755 "${tmp}/traefik" /usr/local/bin/traefik
    rm -rf "$tmp"
  else
    log "Traefik ${TRAEFIK_VERSION} already installed"
  fi

  if [[ -d /opt/gate/.git ]]; then
    log "Updating existing /opt/gate checkout"
    git -C /opt/gate pull --ff-only || warn "git pull failed — keeping current checkout"
  else
    log "Cloning ${REPO_URL} to /opt/gate"
    git clone --depth 1 "$REPO_URL" /opt/gate
  fi

  log "Configuring domain=${DOMAIN}, ACME email, PVE upstream=${PVE_UPSTREAM}"
  umask 077
  cat >/opt/gate/config/base.env <<EOF
# Managed by install-proxmox.sh
DOMAIN=${DOMAIN}
ACME_EMAIL=${ACME_EMAIL}
EOF
  umask 022
  sed -i "s|^\(\s*email:\s*\).*|\1${ACME_EMAIL}|" /opt/gate/config/traefik.yml
  sed -i "s/example\.com/${DOMAIN}/g" \
    /opt/gate/config/dynamic/pve.yml \
    /opt/gate/config/dynamic/apps/gate.yml \
    /opt/gate/config/dynamic/apps/whoami.yml
  sed -i "s|url: \"https://.*:8006\"|url: \"${PVE_UPSTREAM}\"|" /opt/gate/config/dynamic/pve.yml

  if [[ "$WITH_WHOAMI" -eq 1 ]]; then
    log "Installing whoami smoke-test service (127.0.0.1:8099)"
    cat >/usr/local/bin/gate-whoami.py <<'PY'
#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = f"whoami ok\nhost: {self.headers.get('Host')}\npath: {self.path}\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args): pass
HTTPServer(("127.0.0.1", 8099), H).serve_forever()
PY
    chmod +x /usr/local/bin/gate-whoami.py
    cat >/etc/systemd/system/gate-whoami.service <<'EOF'
[Unit]
Description=Tiny whoami for Gate smoke tests
After=network.target
[Service]
ExecStart=/usr/local/bin/gate-whoami.py
Restart=on-failure
DynamicUser=true
NoNewPrivileges=true
ProtectSystem=strict
[Install]
WantedBy=multi-user.target
EOF
    sed -i "s|url: \"http://.*\"|url: \"http://127.0.0.1:8099\"|" \
      /opt/gate/config/dynamic/apps/whoami.yml
  else
    rm -f /opt/gate/config/dynamic/apps/whoami.yml
  fi

  log "Linking /etc/traefik to the repo config (GUI edits go live instantly)"
  ln -sfn /opt/gate/config/traefik.yml /etc/traefik/traefik.yml
  rm -rf /etc/traefik/dynamic
  ln -sfn /opt/gate/config/dynamic /etc/traefik/dynamic

  log "Creating Python venv for the Gate GUI"
  chown -R gate:gate /opt/gate
  sudo -u gate python3 -m venv /opt/gate/.venv
  sudo -u gate /opt/gate/.venv/bin/pip install --quiet --upgrade pip
  sudo -u gate /opt/gate/.venv/bin/pip install --quiet -r /opt/gate/gui/requirements.txt

  if [[ ! -f /etc/gate-admin.env ]]; then
    log "Generating session secret"
    umask 077
    echo "GATE_SESSION_SECRET=$(openssl rand -hex 32)" >/etc/gate-admin.env
    umask 022
  fi

  log "Installing systemd units"
  cat >/etc/systemd/system/traefik.service <<'EOF'
[Unit]
Description=Traefik (Proxmox TLS Main Gate)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/traefik --configFile=/etc/traefik/traefik.yml
Restart=on-failure
RestartSec=5
LimitNOFILE=65536
NoNewPrivileges=true
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
ProtectSystem=full
ReadWritePaths=/var/lib/traefik /var/log/traefik
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
  cp /opt/gate/deploy/gate-admin.service /etc/systemd/system/gate-admin.service

  systemctl daemon-reload
  systemctl enable --now traefik.service gate-admin.service
  [[ "$WITH_WHOAMI" -eq 1 ]] && systemctl enable --now gate-whoami.service

  if [[ "$WITH_FIREWALL" -eq 1 ]]; then
    log "Configuring ufw (allow 80, 443, and 22 if sshd is active)"
    ufw default deny incoming >/dev/null
    ufw default allow outgoing >/dev/null
    ufw allow 80/tcp >/dev/null
    ufw allow 443/tcp >/dev/null
    systemctl is-active ssh &>/dev/null && ufw allow 22/tcp >/dev/null
    ufw --force enable >/dev/null
  fi

  log "Health checks"
  local ok=1
  systemctl is-active --quiet traefik.service || { warn "traefik is NOT active"; ok=0; }
  systemctl is-active --quiet gate-admin.service || { warn "gate-admin is NOT active"; ok=0; }
  for _ in $(seq 1 10); do
    curl -fsS http://127.0.0.1:8080/healthz >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS http://127.0.0.1:8080/healthz >/dev/null 2>&1 \
    || { warn "Gate GUI /healthz not responding"; ok=0; }
  ss -lnt "sport = :443" | grep -q 443 || { warn "Traefik is not listening on :443"; ok=0; }
  [[ "$ok" -eq 1 ]] && log "All services healthy" || warn "Some checks failed — see journalctl -u traefik -u gate-admin"
}

# ---------------------------------------------------------------------------
# Host-side phase
# ---------------------------------------------------------------------------
run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "DRY-RUN: $*"
  else
    "$@"
  fi
}

host_phase() {
  [[ "$(id -u)" -eq 0 ]] || die "Run as root on the Proxmox VE host."
  command -v pct >/dev/null || die "pct not found — this must run on a Proxmox VE host."

  prompt_missing VMID "CT ID (e.g. 110)"
  prompt_missing CT_IP "Static IP with prefix (e.g. 192.168.1.10/24)"
  prompt_missing GATEWAY "Gateway (e.g. 192.168.1.1)"
  prompt_missing DOMAIN "Base domain (e.g. lab.example.com)"
  prompt_missing ACME_EMAIL "ACME email (e.g. you@lab.example.com)"

  DOMAIN="${DOMAIN,,}"
  validate_domain "$DOMAIN" || die "Invalid domain: $DOMAIN"
  validate_email "$ACME_EMAIL" || die "Invalid ACME email: $ACME_EMAIL"
  [[ "$VMID" =~ ^[0-9]+$ ]] || die "VMID must be numeric."
  [[ "$CT_IP" == */* ]] || die "--ip must include a prefix, e.g. 192.168.1.10/24"

  if [[ -z "$PVE_UPSTREAM" ]]; then
    local host_ip
    host_ip="$(hostname -I | awk '{print $1}')"
    PVE_UPSTREAM="https://${host_ip}:8006"
    log "Defaulting PVE upstream to ${PVE_UPSTREAM}"
  fi

  if pct status "$VMID" &>/dev/null; then
    die "CT ${VMID} already exists. Choose another --vmid (or remove it first)."
  fi

  log "Looking for a Debian 12 CT template"
  local template
  template="$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null | awk '/debian-12-standard/ {print $1}' | sort -V | tail -1)"
  if [[ -z "$template" ]]; then
    run pveam update
    local remote
    remote="$(pveam available --section system | awk '/debian-12-standard/ {print $2}' | sort -V | tail -1)"
    [[ -n "$remote" || "$DRY_RUN" -eq 1 ]] || die "No debian-12-standard template available."
    log "Downloading template ${remote:-<latest>}"
    run pveam download "$TEMPLATE_STORAGE" "${remote:-debian-12-standard}"
    template="${TEMPLATE_STORAGE}:vztmpl/${remote:-debian-12-standard}"
  fi
  log "Using template: ${template:-<dry-run>}"

  log "Creating unprivileged CT ${VMID} (${CT_HOSTNAME}, ${CT_IP} on ${BRIDGE})"
  # No root password is set: access the container with `pct enter ${VMID}`.
  run pct create "$VMID" "$template" \
    --hostname "$CT_HOSTNAME" \
    --memory "$MEMORY" \
    --cores "$CORES" \
    --rootfs "${STORAGE}:${DISK}" \
    --net0 "name=eth0,bridge=${BRIDGE},ip=${CT_IP},gw=${GATEWAY},firewall=1" \
    --unprivileged 1 \
    --features nesting=1 \
    --onboot 1 \
    --start 1

  log "Waiting for network inside the CT"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    local up=0
    for _ in $(seq 1 20); do
      if pct exec "$VMID" -- ping -c1 -W2 deb.debian.org &>/dev/null; then
        up=1; break
      fi
      sleep 3
    done
    [[ "$up" -eq 1 ]] || die "CT has no outbound network — check bridge/IP/gateway/DNS."
  fi

  log "Pushing installer and PVE cluster CA into the CT"
  run pct push "$VMID" "$(readlink -f "$0")" /root/gate-install.sh
  if [[ -f /etc/pve/pve-root-ca.pem ]]; then
    run pct push "$VMID" /etc/pve/pve-root-ca.pem /tmp/pve-root-ca.pem
  else
    warn "/etc/pve/pve-root-ca.pem not found on this host — backend TLS verification"
    warn "for the pve route must be set up manually (docs/install-proxmox.md §8.4)."
  fi

  local inside_args=(--inside --domain "$DOMAIN" --acme-email "$ACME_EMAIL"
    --pve-upstream "$PVE_UPSTREAM" --repo "$REPO_URL" --traefik-version "$TRAEFIK_VERSION")
  [[ "$WITH_WHOAMI" -eq 0 ]] && inside_args+=(--no-whoami)
  [[ "$WITH_FIREWALL" -eq 0 ]] && inside_args+=(--no-firewall)

  log "Running container-side installation (this takes a few minutes)"
  run pct exec "$VMID" -- bash /root/gate-install.sh "${inside_args[@]}"

  local ip_only="${CT_IP%%/*}"
  cat <<EOF

============================================================
 Gate installed successfully.
============================================================
 Container:      ${VMID} (${CT_HOSTNAME}) — access with: pct enter ${VMID}
 Gate IP:        ${ip_only}
 Admin GUI:      https://gate.${DOMAIN}
 Proxmox UI:     https://pve.${DOMAIN}  →  ${PVE_UPSTREAM}
$( [[ "$WITH_WHOAMI" -eq 1 ]] && echo " Smoke test:     https://whoami.${DOMAIN}" )

 NEXT STEPS (required):
 1. DNS:   point *.${DOMAIN} (wildcard A record) at ${ip_only}
           (or at your WAN IP with 80/443 forwarded to it)
 2. Ports: forward/allow TCP 80 and 443 to ${ip_only} — nothing else
 3. Visit  https://gate.${DOMAIN} and CREATE THE ADMIN ACCOUNT NOW
           (until it exists, anyone who can reach the page can claim it;
            the default route only allows private networks)
 4. ACME starts on Let's Encrypt STAGING (untrusted certs by design).
    After https://whoami.${DOMAIN} works end-to-end, switch to
    production in Gate → Settings.
 5. Remove any old router port-forward to :8006.

 Docs: docs/install-proxmox.md · docs/security.md · docs/runbook.md
============================================================
EOF
}

if [[ "$INSIDE" -eq 1 ]]; then
  inside_phase
else
  host_phase
fi
