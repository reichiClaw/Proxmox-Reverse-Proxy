from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .paths import (
    APPS_DIR,
    BASE_ENV,
    BASE_ENV_EXAMPLE,
    PVE_YML,
    SETTINGS_FILE,
    TRAEFIK_YML,
)

NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
HOST_RE = re.compile(r"Host\(`([^`]+)`\)")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RESERVED_NAMES = {"_template", "admin", "gate", "traefik"}

STAGING_CA = "https://acme-staging-v02.api.letsencrypt.org/directory"
PROD_CA = "https://acme-v02.api.letsencrypt.org/directory"


@dataclass
class Service:
    name: str
    host: str
    upstream: str
    middlewares: list[str] = field(default_factory=list)
    source: str = "app"  # app | system
    path: Path | None = None
    tls: bool = True

    @property
    def public_url(self) -> str:
        return f"https://{self.host}" if self.host else ""


@dataclass
class Settings:
    domain: str = "example.com"
    acme_email: str = "admin@example.com"
    acme_staging: bool = True
    admin_user: str = "admin"
    # password hash stored in gui.env; empty means not configured yet
    admin_password_hash: str = ""


def _read_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def _write_env_file(path: Path, values: dict[str, str], header: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [header.rstrip(), ""]
    for key, value in values.items():
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_base_env() -> None:
    if not BASE_ENV.exists() and BASE_ENV_EXAMPLE.exists():
        shutil.copy(BASE_ENV_EXAMPLE, BASE_ENV)


def load_settings() -> Settings:
    ensure_base_env()
    base = _read_env_file(BASE_ENV)
    gui = _read_env_file(SETTINGS_FILE)
    traefik = {}
    staging = True
    email = base.get("ACME_EMAIL", "admin@example.com")
    if TRAEFIK_YML.exists():
        with TRAEFIK_YML.open(encoding="utf-8") as fh:
            traefik = yaml.safe_load(fh) or {}
        acme = (
            traefik.get("certificatesResolvers", {})
            .get("le", {})
            .get("acme", {})
        )
        email = acme.get("email") or email
        ca = acme.get("caServer") or STAGING_CA
        staging = "staging" in str(ca)
    return Settings(
        domain=base.get("DOMAIN") or gui.get("DOMAIN") or "example.com",
        acme_email=email,
        acme_staging=staging,
        admin_user=gui.get("ADMIN_USER") or "admin",
        admin_password_hash=gui.get("ADMIN_PASSWORD_HASH") or "",
    )


def save_gui_auth(user: str, password_hash: str) -> None:
    existing = _read_env_file(SETTINGS_FILE)
    existing["ADMIN_USER"] = user
    existing["ADMIN_PASSWORD_HASH"] = password_hash
    _write_env_file(
        SETTINGS_FILE,
        existing,
        "# Gate admin GUI credentials — do not commit.",
    )


def save_settings(settings: Settings, *, rewrite_hosts: bool = True) -> None:
    if not settings.domain or "." not in settings.domain:
        raise ValueError("DOMAIN must look like example.com")
    if not EMAIL_RE.match(settings.acme_email):
        raise ValueError("ACME email looks invalid")

    old = load_settings()
    _write_env_file(
        BASE_ENV,
        {
            "DOMAIN": settings.domain,
            "ACME_EMAIL": settings.acme_email,
        },
        "# Managed by Gate admin GUI / scripts.",
    )

    if not TRAEFIK_YML.exists():
        raise ValueError(f"Missing {TRAEFIK_YML}")

    _patch_traefik_acme(
        email=settings.acme_email,
        staging=settings.acme_staging,
    )

    # Preserve auth fields
    gui = _read_env_file(SETTINGS_FILE)
    if settings.admin_password_hash:
        gui["ADMIN_PASSWORD_HASH"] = settings.admin_password_hash
    gui["ADMIN_USER"] = settings.admin_user
    if gui:
        _write_env_file(
            SETTINGS_FILE,
            gui,
            "# Gate admin GUI credentials — do not commit.",
        )

    if rewrite_hosts and old.domain != settings.domain:
        _rewrite_all_hosts(old.domain, settings.domain)


def validate_name(name: str) -> str:
    name = name.strip().lower()
    if not NAME_RE.match(name):
        raise ValueError("Name must be a lowercase DNS label (e.g. gitea, my-app).")
    if name in RESERVED_NAMES:
        raise ValueError(f"Name '{name}' is reserved.")
    return name


def validate_upstream(upstream: str) -> str:
    upstream = upstream.strip()
    if not re.match(r"^https?://", upstream):
        raise ValueError("Upstream must start with http:// or https://")
    return upstream


def _parse_service_doc(path: Path, source: str) -> Service | None:
    if path.name.startswith("_") or path.suffix not in {".yml", ".yaml"}:
        return None
    with path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    http = doc.get("http") or {}
    routers = http.get("routers") or {}
    services = http.get("services") or {}
    if not routers:
        return None

    # Prefer router key matching filename stem
    stem = path.stem
    router_name = stem if stem in routers else next(iter(routers))
    router = routers[router_name] or {}
    rule = router.get("rule") or ""
    host_match = HOST_RE.search(rule)
    host = host_match.group(1) if host_match else ""

    service_name = router.get("service") or router_name
    svc = services.get(service_name) or {}
    servers = ((svc.get("loadBalancer") or {}).get("servers")) or []
    upstream = servers[0].get("url", "") if servers else ""
    middlewares = list(router.get("middlewares") or [])
    tls = bool(router.get("tls"))

    name = stem if source == "app" else router_name
    return Service(
        name=name,
        host=host,
        upstream=upstream,
        middlewares=middlewares,
        source=source,
        path=path,
        tls=tls,
    )


def list_services() -> list[Service]:
    items: list[Service] = []
    if APPS_DIR.exists():
        for path in sorted(APPS_DIR.glob("*.yml")):
            svc = _parse_service_doc(path, "app")
            if svc:
                items.append(svc)
    if PVE_YML.exists():
        svc = _parse_service_doc(PVE_YML, "system")
        if svc:
            items.append(svc)
    # system services first, then apps alpha
    items.sort(key=lambda s: (0 if s.source == "system" else 1, s.name))
    return items


def get_service(name: str) -> Service | None:
    for svc in list_services():
        if svc.name == name:
            return svc
    return None


def _render_app_yaml(name: str, domain: str, upstream: str, middlewares: list[str] | None = None) -> str:
    mws = middlewares or ["secured"]
    mw_yaml = "\n".join(f"        - {m}" for m in mws)
    host = f"{name}.{domain}"
    return (
        f"# Managed by Gate admin GUI\n"
        f"# https://{host}  →  {upstream}\n"
        f"# Certificate: automatic via Traefik ACME (certResolver: le)\n"
        f"\n"
        f"http:\n"
        f"  routers:\n"
        f"    {name}:\n"
        f"      rule: \"Host(`{host}`)\"\n"
        f"      entryPoints:\n"
        f"        - websecure\n"
        f"      tls:\n"
        f"        certResolver: le\n"
        f"      service: {name}\n"
        f"      middlewares:\n"
        f"{mw_yaml}\n"
        f"\n"
        f"  services:\n"
        f"    {name}:\n"
        f"      loadBalancer:\n"
        f"        passHostHeader: true\n"
        f"        servers:\n"
        f"          - url: \"{upstream}\"\n"
    )


def create_service(name: str, upstream: str) -> Service:
    name = validate_name(name)
    upstream = validate_upstream(upstream)
    settings = load_settings()
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    path = APPS_DIR / f"{name}.yml"
    if path.exists():
        raise ValueError(f"Service '{name}' already exists.")
    path.write_text(
        _render_app_yaml(name, settings.domain, upstream),
        encoding="utf-8",
    )
    svc = get_service(name)
    assert svc is not None
    return svc


def update_service(name: str, *, upstream: str | None = None, new_name: str | None = None) -> Service:
    svc = get_service(name)
    if svc is None:
        raise ValueError(f"Service '{name}' not found.")
    if svc.source == "system":
        return update_system_service(name, upstream=upstream)

    upstream = validate_upstream(upstream if upstream is not None else svc.upstream)
    settings = load_settings()
    target_name = validate_name(new_name) if new_name and new_name != name else name
    new_path = APPS_DIR / f"{target_name}.yml"

    if target_name != name and new_path.exists():
        raise ValueError(f"Service '{target_name}' already exists.")

    content = _render_app_yaml(target_name, settings.domain, upstream, svc.middlewares)
    new_path.write_text(content, encoding="utf-8")
    if target_name != name and svc.path and svc.path.exists():
        svc.path.unlink()
    out = get_service(target_name)
    assert out is not None
    return out


def update_system_service(name: str, *, upstream: str | None = None) -> Service:
    svc = get_service(name)
    if svc is None or svc.path is None:
        raise ValueError(f"System service '{name}' not found.")
    upstream = validate_upstream(upstream if upstream is not None else svc.upstream)
    with svc.path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    http = doc.setdefault("http", {})
    services = http.setdefault("services", {})
    # update first / matching service servers
    key = name if name in services else next(iter(services), None)
    if key is None:
        raise ValueError("No upstream service block found.")
    lb = services[key].setdefault("loadBalancer", {})
    servers = lb.setdefault("servers", [{"url": upstream}])
    if servers:
        servers[0]["url"] = upstream
    else:
        servers.append({"url": upstream})
    svc.path.write_text(
        yaml.safe_dump(doc, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    out = get_service(name)
    assert out is not None
    return out


def delete_service(name: str) -> None:
    svc = get_service(name)
    if svc is None:
        raise ValueError(f"Service '{name}' not found.")
    if svc.source == "system":
        raise ValueError("System services (e.g. pve) cannot be deleted from the GUI.")
    if svc.path and svc.path.exists():
        svc.path.unlink()


def _rewrite_host_in_file(path: Path, old_domain: str, new_domain: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated = text.replace(f".{old_domain}", f".{new_domain}")
    # Also rewrite Host(`old`) if someone used bare domain equals
    if updated != text:
        path.write_text(updated, encoding="utf-8")
        return
    # YAML-safe path: parse and rewrite Host rules
    with path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    routers = (doc.get("http") or {}).get("routers") or {}
    changed = False
    for router in routers.values():
        rule = router.get("rule") or ""
        match = HOST_RE.search(rule)
        if not match:
            continue
        host = match.group(1)
        if host.endswith("." + old_domain):
            new_host = host[: -len(old_domain)] + new_domain
            router["rule"] = f"Host(`{new_host}`)"
            changed = True
        elif host == old_domain:
            router["rule"] = f"Host(`{new_domain}`)"
            changed = True
    if changed:
        path.write_text(
            yaml.safe_dump(doc, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )


def _rewrite_all_hosts(old_domain: str, new_domain: str) -> None:
    if not old_domain or old_domain == new_domain:
        return
    paths: list[Path] = []
    if APPS_DIR.exists():
        paths.extend(p for p in APPS_DIR.glob("*.yml") if not p.name.startswith("_"))
    if PVE_YML.exists():
        paths.append(PVE_YML)
    for path in paths:
        _rewrite_host_in_file(path, old_domain, new_domain)


def _patch_traefik_acme(*, email: str, staging: bool) -> None:
    """Update ACME email/CA in traefik.yml while preserving comments."""
    text = TRAEFIK_YML.read_text(encoding="utf-8")
    ca = STAGING_CA if staging else PROD_CA

    if re.search(r"(?m)^\s*email:\s*", text):
        text = re.sub(r"(?m)^(\s*email:\s*).*$", rf"\1{email}", text, count=1)
    else:
        text = re.sub(
            r"(?m)^(\s*acme:\s*)$",
            rf"\1\n      email: {email}",
            text,
            count=1,
        )

    if re.search(r"(?m)^\s*caServer:\s*", text):
        # Prefer updating an active (uncommented) caServer line.
        text = re.sub(r"(?m)^(\s*caServer:\s*).*$", rf"\1{ca}", text, count=1)
    else:
        # Uncomment / inject near email.
        text = re.sub(
            r"(?m)^(\s*email:\s*.*)$",
            rf"\1\n      caServer: {ca}",
            text,
            count=1,
        )

    TRAEFIK_YML.write_text(text, encoding="utf-8")
