"""Persist per-service saved / active / checked status (outside Traefik watch)."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import BACKUP_DIR, STATUS_FILE

logger = logging.getLogger("gate.status")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> dict[str, Any]:
    if not STATUS_FILE.exists():
        return {"services": {}}
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read status file: %s", exc)
        return {"services": {}}


def _save(data: dict[str, Any]) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(STATUS_FILE)
    os.chmod(STATUS_FILE, 0o600)


def get_service_status(name: str) -> dict[str, Any]:
    data = _load()
    return dict((data.get("services") or {}).get(name) or {})


def record_saved(name: str, *, upstream: str, host: str) -> None:
    data = _load()
    services = data.setdefault("services", {})
    entry = services.setdefault(name, {})
    entry["saved_at"] = _now()
    entry["saved_upstream"] = upstream
    entry["saved_host"] = host
    entry.pop("publish_error", None)
    _save(data)


def record_active(
    name: str,
    *,
    active_upstream: str | None,
    matched: bool | None,
    error: str = "",
) -> None:
    data = _load()
    services = data.setdefault("services", {})
    entry = services.setdefault(name, {})
    entry["active_at"] = _now()
    entry["active_upstream"] = active_upstream or ""
    entry["active_matched"] = matched
    if error:
        entry["active_error"] = error
    else:
        entry.pop("active_error", None)
    _save(data)


def record_checked(name: str, *, direct: dict, via: dict | None = None) -> None:
    data = _load()
    services = data.setdefault("services", {})
    entry = services.setdefault(name, {})
    entry["checked_at"] = _now()
    entry["last_direct"] = direct
    if via is not None:
        entry["last_via"] = via
    _save(data)


def record_publish_error(name: str, message: str) -> None:
    data = _load()
    services = data.setdefault("services", {})
    entry = services.setdefault(name, {})
    entry["publish_error"] = message
    entry["publish_error_at"] = _now()
    _save(data)


def backup_file(path: Path) -> Path | None:
    """Copy a config file into backups/ (outside Traefik watch)."""
    if not path.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = BACKUP_DIR / f"{path.stem}.{stamp}{path.suffix}"
    dest.write_bytes(path.read_bytes())
    os.chmod(dest, 0o600)
    return dest


def all_statuses() -> dict[str, Any]:
    return _load().get("services") or {}
