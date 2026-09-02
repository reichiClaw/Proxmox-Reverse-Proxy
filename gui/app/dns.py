"""Cloudflare DNS automation.

For every published host (``<name>.<domain>``) a CNAME record pointing at a
configured target (your gate's public A/AAAA or DDNS name) is created in the
matching Cloudflare zone. Uses only the standard library — no extra
dependencies to audit.

Safety rules:
- Records are tagged with a comment marker; the integration only ever
  updates or deletes records it created itself.
- Existing records of any other origin are treated as conflicts and left
  untouched.
- DNS failures never block route changes — Traefik config is the source of
  truth; DNS is converged best-effort and can be re-run via ``sync_all``.

Run ``python -m gui.app.dns status|sync`` for CLI use.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .paths import CONFIG_DIR

CLOUDFLARE_ENV = CONFIG_DIR / "cloudflare.env"
API_BASE = "https://api.cloudflare.com/client/v4"
MANAGED_COMMENT = "managed-by:proxmox-gate"
TIMEOUT = 15

logger = logging.getLogger("gate.dns")


class DnsError(Exception):
    """Cloudflare API failure with an operator-safe message."""


@dataclass
class DnsConfig:
    api_token: str = ""
    target: str = ""
    proxied: bool = False

    @property
    def enabled(self) -> bool:
        return bool(self.api_token and self.target)


def load_dns_config() -> DnsConfig:
    from .store import _read_env_file

    data = _read_env_file(CLOUDFLARE_ENV)
    return DnsConfig(
        api_token=data.get("CF_API_TOKEN", ""),
        target=data.get("CF_TARGET", "").strip().lower().rstrip("."),
        proxied=data.get("CF_PROXIED", "false").lower() in ("1", "true", "yes", "on"),
    )


def save_dns_config(config: DnsConfig) -> None:
    from .store import _write_env_file

    _write_env_file(
        CLOUDFLARE_ENV,
        {
            "CF_API_TOKEN": config.api_token,
            "CF_TARGET": config.target,
            "CF_PROXIED": "true" if config.proxied else "false",
        },
        "# Cloudflare DNS automation — contains an API token, do not commit.",
    )


def _api(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    url = f"{API_BASE}{path}"
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            doc = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            doc = json.loads(exc.read().decode())
            errors = "; ".join(e.get("message", "") for e in doc.get("errors", []))
        except Exception:
            errors = ""
        raise DnsError(f"Cloudflare API {exc.code}: {errors or exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise DnsError(f"Cloudflare API unreachable: {exc}") from exc
    if not doc.get("success", False):
        errors = "; ".join(e.get("message", "") for e in doc.get("errors", []))
        raise DnsError(f"Cloudflare API error: {errors or 'unknown'}")
    return doc


def list_zones(token: str) -> dict[str, str]:
    """Return {zone_name: zone_id} for every zone the token can see."""
    zones: dict[str, str] = {}
    page = 1
    while True:
        doc = _api("GET", f"/zones?per_page=50&page={page}", token)
        for zone in doc.get("result", []):
            zones[zone["name"].lower()] = zone["id"]
        info = doc.get("result_info") or {}
        if page >= int(info.get("total_pages") or 1):
            break
        page += 1
    return zones


def _find_zone(host: str, zones: dict[str, str]) -> tuple[str, str] | None:
    """Longest-suffix zone match for a host (supports delegated subzones)."""
    best: tuple[str, str] | None = None
    for name, zone_id in zones.items():
        if host == name or host.endswith("." + name):
            if best is None or len(name) > len(best[0]):
                best = (name, zone_id)
    return best


def _get_records(token: str, zone_id: str, host: str) -> list[dict]:
    query = urllib.parse.quote(host)
    doc = _api("GET", f"/zones/{zone_id}/dns_records?name={query}", token)
    return list(doc.get("result", []))


def ensure_cname(host: str, config: DnsConfig | None = None) -> str:
    """Converge a managed CNAME ``host -> target``.

    Returns one of: disabled, skipped-target, no-zone, created, updated,
    exists, conflict. Raises DnsError on API failures.
    """
    config = config or load_dns_config()
    if not config.enabled:
        return "disabled"
    host = host.strip().lower().rstrip(".")
    if not host or host == config.target:
        return "skipped-target"

    zones = list_zones(config.api_token)
    zone = _find_zone(host, zones)
    if zone is None:
        logger.warning("No Cloudflare zone matches %s (zones: %s)", host, ", ".join(zones) or "none")
        return "no-zone"
    _, zone_id = zone

    records = _get_records(config.api_token, zone_id, host)
    managed = [r for r in records if r.get("comment") == MANAGED_COMMENT]
    desired = {
        "type": "CNAME",
        "name": host,
        "content": config.target,
        "proxied": config.proxied,
        "ttl": 1,  # auto
        "comment": MANAGED_COMMENT,
    }

    if managed:
        record = managed[0]
        if (
            record.get("type") == "CNAME"
            and record.get("content", "").lower() == config.target
            and bool(record.get("proxied")) == config.proxied
        ):
            return "exists"
        _api("PUT", f"/zones/{zone_id}/dns_records/{record['id']}", config.api_token, desired)
        logger.info("DNS updated: %s -> %s", host, config.target)
        return "updated"

    if records:
        # A record for this exact name exists but was not created by us —
        # never overwrite operator- or third-party-managed DNS.
        logger.warning("DNS conflict: %s already has %d unmanaged record(s)", host, len(records))
        return "conflict"

    _api("POST", f"/zones/{zone_id}/dns_records", config.api_token, desired)
    logger.info("DNS created: %s -> %s (proxied=%s)", host, config.target, config.proxied)
    return "created"


def delete_cname(host: str, config: DnsConfig | None = None) -> str:
    """Delete the managed record for a host. Unmanaged records are kept.

    Returns one of: disabled, no-zone, deleted, not-managed, absent.
    """
    config = config or load_dns_config()
    if not config.enabled:
        return "disabled"
    host = host.strip().lower().rstrip(".")

    zones = list_zones(config.api_token)
    zone = _find_zone(host, zones)
    if zone is None:
        return "no-zone"
    _, zone_id = zone

    records = _get_records(config.api_token, zone_id, host)
    if not records:
        return "absent"
    managed = [r for r in records if r.get("comment") == MANAGED_COMMENT]
    if not managed:
        return "not-managed"
    for record in managed:
        _api("DELETE", f"/zones/{zone_id}/dns_records/{record['id']}", config.api_token)
        logger.info("DNS deleted: %s", host)
    return "deleted"


def sync_all() -> list[tuple[str, str]]:
    """Ensure a record for every currently published host (idempotent)."""
    from .store import list_services

    config = load_dns_config()
    results: list[tuple[str, str]] = []
    for svc in list_services():
        if not svc.host:
            continue
        try:
            results.append((svc.host, ensure_cname(svc.host, config)))
        except DnsError as exc:
            results.append((svc.host, f"error: {exc}"))
    return results


def _cli() -> int:
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    config = load_dns_config()

    if command == "status":
        if not config.enabled:
            print("Cloudflare DNS automation: disabled")
            print(f"Configure via the Gate GUI (Settings) or {CLOUDFLARE_ENV}")
            return 0
        print(f"Target:  {config.target}  (proxied={config.proxied})")
        try:
            zones = list_zones(config.api_token)
        except DnsError as exc:
            print(f"Token check FAILED: {exc}")
            return 1
        print(f"Zones:   {', '.join(sorted(zones)) or 'none visible to this token'}")
        return 0

    if command == "sync":
        if not config.enabled:
            print("Cloudflare DNS automation is not configured.")
            return 1
        failed = 0
        for host, status in sync_all():
            print(f"{host:40s} {status}")
            failed += status.startswith("error") or status in ("no-zone",)
        return 1 if failed else 0

    print(f"Usage: python -m gui.app.dns [status|sync]")
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
