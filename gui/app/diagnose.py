"""Build a redacted diagnose report for copy/paste."""
from __future__ import annotations

import platform
import re
from datetime import datetime, timezone
from typing import Any

from . import runtime, statusstore
from .paths import TRAEFIK_API_URL, TRAEFIK_YML
from .store import list_services, list_unreadable_dynamic, load_settings

_SECRET_RE = re.compile(
    r"(?i)(token|password|secret|authorization|api[_-]?key|x-gotify-key)\s*[:=]\s*\S+"
)
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")


def _redact(text: str) -> str:
    text = _SECRET_RE.sub(r"\1: ***", text)
    text = _BEARER_RE.sub("Bearer ***", text)
    return text


def build_diagnose_report() -> str:
    settings = load_settings()
    snap = runtime.fetch_runtime()
    statuses = statusstore.all_statuses()
    lines: list[str] = []
    lines.append(f"Gate diagnose report — {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"Python: {platform.python_version()}  Platform: {platform.platform()}")
    lines.append(f"Domain: {settings.domain}")
    lines.append(f"Traefik API: {TRAEFIK_API_URL}  reachable={snap.ok}")
    if snap.error:
        lines.append(f"Traefik API error: {_redact(snap.error)}")
    if TRAEFIK_YML.exists():
        text = TRAEFIK_YML.read_text(encoding="utf-8")
        if "127.0.0.1:8090" in text:
            lines.append("Local Traefik API entryPoint: configured")
        ca = "staging" if "staging" in text else "production-or-custom"
        lines.append(f"ACME hint: {ca}")
    unreadable = list_unreadable_dynamic()
    if unreadable:
        lines.append("")
        lines.append("=== Unreadable dynamic YAML (blocks Traefik reload) ===")
        for item in unreadable:
            lines.append(f"- {item}")
    lines.append("")
    lines.append("=== Services (saved vs active) ===")
    for svc in list_services():
        st = statuses.get(svc.name) or {}
        active = snap.active_upstream(svc.name) if snap.ok else st.get("active_upstream")
        matched = (
            active is not None and active.rstrip("/") == svc.upstream.rstrip("/")
            if active
            else st.get("active_matched")
        )
        lines.append(f"- {svc.name} ({svc.source})")
        lines.append(f"  public: {svc.public_url}")
        lines.append(f"  saved:  {svc.upstream}")
        lines.append(f"  active: {active or '(unknown)'}")
        lines.append(f"  matched: {matched}")
        lines.append(f"  saved_at: {st.get('saved_at')}  active_at: {st.get('active_at')}  checked_at: {st.get('checked_at')}")
        if st.get("publish_error"):
            lines.append(f"  publish_error: {_redact(str(st['publish_error']))}")
        if st.get("last_direct"):
            d = st["last_direct"]
            lines.append(
                f"  last_direct: ok={d.get('ok')} status={d.get('http_status')} "
                f"err={_redact(str(d.get('error') or ''))}"
            )
        if st.get("last_via"):
            v = st["last_via"]
            lines.append(
                f"  last_via: ok={v.get('ok')} status={v.get('http_status')} "
                f"err={_redact(str(v.get('error') or ''))}"
            )
    if snap.provider_errors:
        lines.append("")
        lines.append("=== Traefik provider / router errors ===")
        for err in snap.provider_errors[:20]:
            lines.append(f"- {_redact(err)}")
    lines.append("")
    lines.append("Secrets redacted. Do not paste gui.env / cloudflare.env / tokens.")
    return "\n".join(lines) + "\n"
