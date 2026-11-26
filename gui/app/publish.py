"""Publish + verify: wait for Traefik active config to match saved YAML."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from . import probe, runtime, statusstore
from .store import (
    Service,
    ensure_dynamic_readable,
    get_service,
    list_unreadable_dynamic,
)

logger = logging.getLogger("gate.publish")


@dataclass
class PublishResult:
    name: str
    saved: bool
    saved_upstream: str = ""
    active_upstream: str | None = None
    active_matched: bool | None = None
    active_error: str = ""
    provider_errors: list[str] = field(default_factory=list)
    runtime_ok: bool = False
    fixed_perms: list[str] = field(default_factory=list)
    direct: dict[str, Any] | None = None
    via: dict[str, Any] | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.saved and self.active_matched is True


def wait_for_active(
    name: str,
    expected_upstream: str,
    *,
    timeout_s: float = 8.0,
    interval_s: float = 0.4,
) -> runtime.RuntimeSnapshot:
    """Poll Traefik API until active upstream matches or timeout."""
    deadline = time.monotonic() + timeout_s
    last = runtime.fetch_runtime()
    while time.monotonic() < deadline:
        last = runtime.fetch_runtime()
        if last.ok:
            active = last.active_upstream(name)
            if active == expected_upstream:
                return last
            # Also accept match ignoring trailing slash differences
            if active and active.rstrip("/") == expected_upstream.rstrip("/"):
                return last
        time.sleep(interval_s)
    return last


def verify_service(
    name: str,
    *,
    run_probes: bool = True,
    verify_backend_tls: bool = True,
) -> PublishResult:
    """Compare saved YAML vs Traefik runtime; optionally probe connectivity."""
    svc = get_service(name)
    result = PublishResult(name=name, saved=False)
    if svc is None:
        result.message = f"Service '{name}' not found on disk."
        statusstore.record_publish_error(name, result.message)
        return result

    result.saved = True
    result.saved_upstream = svc.upstream
    result.fixed_perms = ensure_dynamic_readable()
    still_bad = list_unreadable_dynamic()
    if still_bad:
        result.active_error = (
            "Unreadable Traefik config files remain: " + ", ".join(still_bad)
        )
        result.message = (
            f"Saved {svc.upstream}, but Traefik may not reload — "
            f"{result.active_error}. Previous active config may still be in use."
        )
        statusstore.record_publish_error(name, result.message)

    snap = wait_for_active(name, svc.upstream)
    result.runtime_ok = snap.ok
    result.provider_errors = list(snap.provider_errors)
    if still_bad:
        result.provider_errors = still_bad + result.provider_errors
    if not snap.ok:
        result.active_error = snap.error or "Traefik runtime status unknown"
        result.active_matched = None
        if not result.message:
            result.message = (
                f"Saved {svc.upstream}, but could not confirm Traefik active config: "
                f"{result.active_error}"
            )
        statusstore.record_active(
            name, active_upstream=None, matched=None, error=result.active_error
        )
    else:
        active = snap.active_upstream(name)
        result.active_upstream = active
        matched = active is not None and active.rstrip("/") == svc.upstream.rstrip("/")
        result.active_matched = matched
        if not matched:
            # Router error?
            router = snap.routers.get(name) or {}
            if router.get("error"):
                result.active_error = str(router["error"])
            elif name not in snap.services:
                result.active_error = (
                    "Service not present in Traefik runtime — a sibling file may "
                    "have blocked the file provider (check provider errors / Diagnose)."
                )
            else:
                result.active_error = "Active upstream differs from saved config"
            result.message = (
                f"Saved target {svc.upstream}; active target "
                f"{active or '(missing)'}. {result.active_error}"
            )
            if result.provider_errors:
                result.message += " Provider: " + "; ".join(result.provider_errors[:3])
            statusstore.record_active(
                name,
                active_upstream=active,
                matched=False,
                error=result.message,
            )
            statusstore.record_publish_error(name, result.message)
        else:
            result.message = f"Active Traefik upstream matches saved ({svc.upstream})."
            statusstore.record_active(name, active_upstream=active, matched=True)
            if still_bad:
                result.message += (
                    " Warning: other unreadable files were detected: "
                    + ", ".join(still_bad)
                )

    if run_probes:
        # PVE backend uses private CA — allow insecure direct TLS unless CA present
        direct_verify = verify_backend_tls
        if svc.name == "pve" or svc.upstream.startswith("https://"):
            # Still try verified first; probe_direct reports TLS errors clearly.
            pass
        direct = probe.probe_direct(svc.upstream, verify_tls=direct_verify)
        # If verified TLS failed for pve, retry with diagnose note
        if (
            not direct.ok
            and direct.tls_ok is False
            and svc.upstream.startswith("https://")
        ):
            insecure = probe.probe_direct(svc.upstream, verify_tls=False)
            if insecure.ok:
                insecure.detail = (
                    (insecure.detail or "")
                    + "; reachable with TLS verify disabled — check Traefik rootCAs/serverName"
                ).strip("; ")
                direct = insecure
        via = None
        if svc.host:
            via = probe.probe_via_traefik(svc.host, path=svc.route_path or "/")
        result.direct = direct.to_dict()
        result.via = via.to_dict() if via else None
        statusstore.record_checked(
            name,
            direct=result.direct,
            via=result.via,
        )
        if result.active_matched and direct.ok:
            result.message += f" Direct probe HTTP {direct.http_status} in {direct.duration_ms}ms."
        elif result.active_matched and not direct.ok:
            result.message += f" Config active, but direct probe failed: {direct.error}"

    return result


def service_view(svc: Service, statuses: dict, snap: runtime.RuntimeSnapshot | None) -> dict:
    """Merge disk service + status file + runtime for templates."""
    st = dict(statuses.get(svc.name) or {})
    active = None
    active_err = ""
    if snap and snap.ok:
        active = snap.active_upstream(svc.name)
        router = snap.routers.get(svc.name) or {}
        if router.get("error"):
            active_err = str(router["error"])
    elif snap and not snap.ok:
        active_err = snap.error

    saved_up = st.get("saved_upstream") or svc.upstream
    matched = None
    if active is not None:
        matched = active.rstrip("/") == svc.upstream.rstrip("/")
    elif "active_matched" in st:
        matched = st.get("active_matched")

    return {
        "service": svc,
        "saved_at": st.get("saved_at"),
        "saved_upstream": saved_up,
        "active_at": st.get("active_at"),
        "active_upstream": active if active is not None else st.get("active_upstream"),
        "active_matched": matched,
        "active_error": active_err or st.get("active_error") or st.get("publish_error"),
        "checked_at": st.get("checked_at"),
        "last_direct": st.get("last_direct"),
        "last_via": st.get("last_via"),
        "publish_error": st.get("publish_error"),
    }
