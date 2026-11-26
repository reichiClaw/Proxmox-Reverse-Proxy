"""Read Traefik's *active* runtime config (local API) and provider health."""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .paths import TRAEFIK_API_URL

logger = logging.getLogger("gate.runtime")

_URL_IN_LB = re.compile(r"url:\s*\"([^\"]+)\"")


@dataclass
class ActiveService:
    name: str
    provider: str = ""
    upstream: str = ""
    status: str = ""
    server_status: str = ""
    error: str = ""


@dataclass
class RuntimeSnapshot:
    ok: bool
    fetched_at: str
    api_url: str
    error: str = ""
    services: dict[str, ActiveService] = field(default_factory=dict)
    routers: dict[str, dict[str, Any]] = field(default_factory=dict)
    provider_errors: list[str] = field(default_factory=list)

    def active_upstream(self, name: str) -> str | None:
        svc = self.services.get(name)
        return svc.upstream if svc and svc.upstream else None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_json(path: str, timeout: float = 2.0) -> Any:
    url = f"{TRAEFIK_API_URL.rstrip('/')}{path}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_runtime() -> RuntimeSnapshot:
    """Query Traefik's local API. Never raises — returns ok=False on failure."""
    snap = RuntimeSnapshot(ok=False, fetched_at=_now(), api_url=TRAEFIK_API_URL)
    try:
        raw_services = _get_json("/api/http/services")
        raw_routers = _get_json("/api/http/routers")
    except urllib.error.URLError as exc:
        snap.error = f"Traefik API unreachable ({TRAEFIK_API_URL}): {exc}"
        logger.warning("%s", snap.error)
        return snap
    except Exception as exc:  # noqa: BLE001 — surface any API/parse issue
        snap.error = f"Traefik API error: {exc}"
        logger.warning("%s", snap.error)
        return snap

    if not isinstance(raw_services, list):
        snap.error = "Unexpected Traefik services payload"
        return snap

    for item in raw_services:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        # names look like "pve@file"
        short = name.split("@", 1)[0] if name else ""
        if not short:
            continue
        provider = name.split("@", 1)[1] if "@" in name else ""
        lb = item.get("loadBalancer") or {}
        servers = lb.get("servers") or []
        upstream = ""
        server_status = ""
        if servers and isinstance(servers[0], dict):
            upstream = str(servers[0].get("url") or "")
            server_status = str(servers[0].get("status") or "")
        err = ""
        status = str(item.get("status") or "")
        if status and status != "enabled":
            err = str(item.get("error") or status)
            if err:
                snap.provider_errors.append(f"{name}: {err}")
        snap.services[short] = ActiveService(
            name=short,
            provider=provider,
            upstream=upstream,
            status=status,
            server_status=server_status,
            error=err,
        )

    if isinstance(raw_routers, list):
        for item in raw_routers:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            short = name.split("@", 1)[0] if name else ""
            if not short:
                continue
            snap.routers[short] = {
                "rule": item.get("rule") or "",
                "service": item.get("service") or "",
                "status": item.get("status") or "",
                "error": item.get("error") or "",
                "provider": name.split("@", 1)[1] if "@" in name else "",
            }
            if item.get("error"):
                snap.provider_errors.append(f"router {name}: {item.get('error')}")

    snap.ok = True
    return snap
