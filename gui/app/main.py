from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .auth import (
    SESSION_USER_KEY,
    authenticate,
    bootstrap_admin,
    is_bootstrap_required,
    login_throttle,
)
from .store import (
    create_service,
    delete_service,
    get_service,
    list_services,
    load_settings,
    save_settings,
    update_service,
)

logger = logging.getLogger("gate")

APP_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(APP_DIR / "templates"))

SESSION_MAX_AGE = int(os.environ.get("GATE_SESSION_MAX_AGE", str(8 * 3600)))

_session_secret = os.environ.get("GATE_SESSION_SECRET", "")
if not _session_secret:
    # Ephemeral secret: sessions are invalidated on restart and cannot be
    # shared across processes. Fine for a single local process; set
    # GATE_SESSION_SECRET in production (see deploy/gate-admin.service).
    _session_secret = secrets.token_hex(32)
    logger.warning(
        "GATE_SESSION_SECRET is not set; using an ephemeral session secret. "
        "Sessions will not survive a restart."
    )

app = FastAPI(title="Proxmox Gate", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    session_cookie="gate_session",
    same_site="lax",
    https_only=os.environ.get("GATE_HTTPS_ONLY", "0") == "1",
    max_age=SESSION_MAX_AGE,
)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

CSP = (
    "default-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "script-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("Cache-Control", "no-store")
    return response


def _flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session["flash"] = {"message": message, "kind": kind}


def _pop_flash(request: Request) -> dict | None:
    return request.session.pop("flash", None)


def current_user(request: Request) -> str | None:
    return request.session.get(SESSION_USER_KEY)


def _csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def _check_csrf(request: Request, token: str) -> bool:
    expected = request.session.get("csrf") or ""
    return bool(expected) and bool(token) and secrets.compare_digest(expected, token)


def _csrf_reject(request: Request, dest: str = "/") -> RedirectResponse:
    logger.warning("CSRF token mismatch on %s", request.url.path)
    _flash(request, "The form expired or was invalid. Please try again.", "error")
    return RedirectResponse(dest, status_code=303)


def _safe_next(next_path: str) -> str:
    # Same-origin relative paths only; "//host" and "/\host" would be treated
    # as protocol-relative URLs by browsers.
    if (
        next_path.startswith("/")
        and not next_path.startswith("//")
        and "\\" not in next_path
        and not next_path.startswith("/\t")
    ):
        return next_path
    return "/services"


def _client_ip(request: Request) -> str:
    # Direct peer address only. The Gate GUI sits behind Traefik on
    # localhost; we intentionally do not trust X-Forwarded-For here because
    # throttling by a spoofable header would be useless.
    return request.client.host if request.client else "unknown"


def _ctx(request: Request, **extra):
    return {
        "request": request,
        "user": current_user(request),
        "settings": load_settings(),
        "flash": _pop_flash(request),
        "bootstrap": is_bootstrap_required(),
        "csrf_token": _csrf_token(request),
        **extra,
    }


def _require_login(request: Request) -> RedirectResponse | None:
    if is_bootstrap_required():
        return RedirectResponse("/setup", status_code=303)
    if not current_user(request):
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
    return None


@app.get("/healthz")
async def healthz():
    return JSONResponse({"status": "ok"})


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if is_bootstrap_required():
        return RedirectResponse("/setup", status_code=303)
    if not current_user(request):
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/services", status_code=303)


@app.get("/setup", response_class=HTMLResponse)
async def setup_get(request: Request):
    if not is_bootstrap_required():
        return RedirectResponse("/login", status_code=303)
    return TEMPLATES.TemplateResponse(request, "setup.html", _ctx(request, title="Initial setup"))


@app.post("/setup")
async def setup_post(
    request: Request,
    username: Annotated[str, Form()] = "admin",
    password: Annotated[str, Form()] = "",
    password2: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
):
    if not is_bootstrap_required():
        return RedirectResponse("/login", status_code=303)
    if not _check_csrf(request, csrf_token):
        return _csrf_reject(request, "/setup")
    if password != password2:
        _flash(request, "Passwords do not match.", "error")
        return RedirectResponse("/setup", status_code=303)
    try:
        username = bootstrap_admin(username, password)
    except ValueError as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse("/setup", status_code=303)
    # Fresh session for the fresh account.
    request.session.clear()
    request.session[SESSION_USER_KEY] = username
    _csrf_token(request)
    logger.info("Admin account created (user=%s)", username)
    _flash(request, "Admin account created.")
    return RedirectResponse("/services", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    if is_bootstrap_required():
        return RedirectResponse("/setup", status_code=303)
    if current_user(request):
        return RedirectResponse("/services", status_code=303)
    return TEMPLATES.TemplateResponse(
        request,
        "login.html",
        _ctx(request, title="Sign in", next=_safe_next(request.query_params.get("next", "/services"))),
    )


@app.post("/login")
async def login_post(
    request: Request,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/services",
    csrf_token: Annotated[str, Form()] = "",
):
    if not _check_csrf(request, csrf_token):
        return _csrf_reject(request, "/login")

    ip = _client_ip(request)
    throttle_keys = (f"user:{username.strip().lower()[:64]}", f"ip:{ip}")
    if login_throttle.is_locked(*throttle_keys):
        logger.warning("Login lockout active (ip=%s)", ip)
        _flash(request, "Too many failed attempts. Try again later.", "error")
        return RedirectResponse("/login", status_code=303)

    if authenticate(username, password):
        login_throttle.record_success(*throttle_keys)
        # Rotate the session on privilege change to prevent fixation.
        request.session.clear()
        request.session[SESSION_USER_KEY] = username
        _csrf_token(request)
        logger.info("Login succeeded (user=%s, ip=%s)", username, ip)
        return RedirectResponse(_safe_next(next), status_code=303)

    login_throttle.record_failure(*throttle_keys)
    logger.warning("Login failed (ip=%s)", ip)
    _flash(request, "Invalid username or password.", "error")
    return RedirectResponse("/login", status_code=303)


@app.post("/logout")
async def logout(request: Request, csrf_token: Annotated[str, Form()] = ""):
    if not _check_csrf(request, csrf_token):
        return _csrf_reject(request, "/")
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/services", response_class=HTMLResponse)
async def services_list(request: Request):
    if redir := _require_login(request):
        return redir
    return TEMPLATES.TemplateResponse(
        request,
        "services.html",
        _ctx(request, title="Services", services=list_services()),
    )


@app.get("/services/new", response_class=HTMLResponse)
async def services_new(request: Request):
    if redir := _require_login(request):
        return redir
    return TEMPLATES.TemplateResponse(
        request,
        "service_form.html",
        _ctx(request, title="Add service", mode="create", service=None),
    )


@app.post("/services/new")
async def services_create(
    request: Request,
    name: Annotated[str, Form()] = "",
    upstream: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
):
    if redir := _require_login(request):
        return redir
    if not _check_csrf(request, csrf_token):
        return _csrf_reject(request, "/services/new")
    try:
        svc = create_service(name, upstream)
    except ValueError as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse("/services/new", status_code=303)
    logger.info("Service created (name=%s, upstream=%s)", svc.name, svc.upstream)
    _flash(request, f"Added {svc.public_url} — certificate will be issued automatically.")
    return RedirectResponse("/services", status_code=303)


@app.get("/services/{name}/edit", response_class=HTMLResponse)
async def services_edit(name: str, request: Request):
    if redir := _require_login(request):
        return redir
    svc = get_service(name)
    if svc is None:
        _flash(request, "Service not found.", "error")
        return RedirectResponse("/services", status_code=303)
    return TEMPLATES.TemplateResponse(
        request,
        "service_form.html",
        _ctx(request, title=f"Edit {name}", mode="edit", service=svc),
    )


@app.post("/services/{name}/edit")
async def services_update(
    name: str,
    request: Request,
    upstream: Annotated[str, Form()] = "",
    new_name: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
):
    if redir := _require_login(request):
        return redir
    if not _check_csrf(request, csrf_token):
        return _csrf_reject(request, "/services")
    try:
        svc = update_service(name, upstream=upstream, new_name=new_name or None)
    except ValueError as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse(f"/services/{name}/edit", status_code=303)
    logger.info("Service updated (name=%s, upstream=%s)", svc.name, svc.upstream)
    _flash(request, f"Updated {svc.public_url}")
    return RedirectResponse("/services", status_code=303)


@app.post("/services/{name}/delete")
async def services_delete(
    name: str,
    request: Request,
    csrf_token: Annotated[str, Form()] = "",
):
    if redir := _require_login(request):
        return redir
    if not _check_csrf(request, csrf_token):
        return _csrf_reject(request, "/services")
    try:
        delete_service(name)
    except ValueError as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse("/services", status_code=303)
    logger.info("Service deleted (name=%s)", name)
    _flash(request, f"Removed service '{name}'.")
    return RedirectResponse("/services", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request):
    if redir := _require_login(request):
        return redir
    return TEMPLATES.TemplateResponse(request, "settings.html", _ctx(request, title="Settings"))


@app.post("/settings")
async def settings_post(
    request: Request,
    domain: Annotated[str, Form()] = "",
    acme_email: Annotated[str, Form()] = "",
    acme_staging: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
):
    if redir := _require_login(request):
        return redir
    if not _check_csrf(request, csrf_token):
        return _csrf_reject(request, "/settings")
    settings = load_settings()
    settings.domain = domain.strip().lower()
    settings.acme_email = acme_email.strip()
    settings.acme_staging = acme_staging == "on"
    try:
        save_settings(settings, rewrite_hosts=True)
    except ValueError as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse("/settings", status_code=303)
    logger.info(
        "Settings saved (domain=%s, staging=%s)", settings.domain, settings.acme_staging
    )
    _flash(request, "Settings saved. Hostnames were rewritten if the domain changed.")
    return RedirectResponse("/settings", status_code=303)
