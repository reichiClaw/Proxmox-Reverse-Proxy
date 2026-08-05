from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .auth import (
    SESSION_USER_KEY,
    authenticate,
    bootstrap_admin,
    is_bootstrap_required,
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

APP_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(APP_DIR / "templates"))

app = FastAPI(title="Proxmox Gate", docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("GATE_SESSION_SECRET", secrets.token_hex(32)),
    session_cookie="gate_session",
    same_site="lax",
    https_only=os.environ.get("GATE_HTTPS_ONLY", "0") == "1",
)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


def _flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session["flash"] = {"message": message, "kind": kind}


def _pop_flash(request: Request) -> dict | None:
    return request.session.pop("flash", None)


def current_user(request: Request) -> str | None:
    return request.session.get(SESSION_USER_KEY)


def _ctx(request: Request, **extra):
    return {
        "request": request,
        "user": current_user(request),
        "settings": load_settings(),
        "flash": _pop_flash(request),
        "bootstrap": is_bootstrap_required(),
        **extra,
    }


def _require_login(request: Request) -> RedirectResponse | None:
    if is_bootstrap_required():
        return RedirectResponse("/setup", status_code=303)
    if not current_user(request):
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
    return None


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
    return TEMPLATES.TemplateResponse("setup.html", _ctx(request, title="Initial setup"))


@app.post("/setup")
async def setup_post(
    request: Request,
    username: Annotated[str, Form()] = "admin",
    password: Annotated[str, Form()] = "",
    password2: Annotated[str, Form()] = "",
):
    if not is_bootstrap_required():
        return RedirectResponse("/login", status_code=303)
    if password != password2:
        _flash(request, "Passwords do not match.", "error")
        return RedirectResponse("/setup", status_code=303)
    try:
        bootstrap_admin(username, password)
    except ValueError as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse("/setup", status_code=303)
    request.session[SESSION_USER_KEY] = username.strip() or "admin"
    _flash(request, "Admin account created.")
    return RedirectResponse("/services", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    if is_bootstrap_required():
        return RedirectResponse("/setup", status_code=303)
    if current_user(request):
        return RedirectResponse("/services", status_code=303)
    return TEMPLATES.TemplateResponse(
        "login.html",
        _ctx(request, title="Sign in", next=request.query_params.get("next", "/services")),
    )


@app.post("/login")
async def login_post(
    request: Request,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/services",
):
    if authenticate(username, password):
        request.session[SESSION_USER_KEY] = username
        dest = next if next.startswith("/") else "/services"
        return RedirectResponse(dest, status_code=303)
    _flash(request, "Invalid username or password.", "error")
    return RedirectResponse("/login", status_code=303)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/services", response_class=HTMLResponse)
async def services_list(request: Request):
    if redir := _require_login(request):
        return redir
    return TEMPLATES.TemplateResponse(
        "services.html",
        _ctx(request, title="Services", services=list_services()),
    )


@app.get("/services/new", response_class=HTMLResponse)
async def services_new(request: Request):
    if redir := _require_login(request):
        return redir
    return TEMPLATES.TemplateResponse(
        "service_form.html",
        _ctx(request, title="Add service", mode="create", service=None),
    )


@app.post("/services/new")
async def services_create(
    request: Request,
    name: Annotated[str, Form()] = "",
    upstream: Annotated[str, Form()] = "",
):
    if redir := _require_login(request):
        return redir
    try:
        svc = create_service(name, upstream)
    except ValueError as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse("/services/new", status_code=303)
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
        "service_form.html",
        _ctx(request, title=f"Edit {name}", mode="edit", service=svc),
    )


@app.post("/services/{name}/edit")
async def services_update(
    name: str,
    request: Request,
    upstream: Annotated[str, Form()] = "",
    new_name: Annotated[str, Form()] = "",
):
    if redir := _require_login(request):
        return redir
    try:
        svc = update_service(name, upstream=upstream, new_name=new_name or None)
    except ValueError as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse(f"/services/{name}/edit", status_code=303)
    _flash(request, f"Updated {svc.public_url}")
    return RedirectResponse("/services", status_code=303)


@app.post("/services/{name}/delete")
async def services_delete(name: str, request: Request):
    if redir := _require_login(request):
        return redir
    try:
        delete_service(name)
    except ValueError as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse("/services", status_code=303)
    _flash(request, f"Removed service '{name}'.")
    return RedirectResponse("/services", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request):
    if redir := _require_login(request):
        return redir
    return TEMPLATES.TemplateResponse("settings.html", _ctx(request, title="Settings"))


@app.post("/settings")
async def settings_post(
    request: Request,
    domain: Annotated[str, Form()] = "",
    acme_email: Annotated[str, Form()] = "",
    acme_staging: Annotated[str, Form()] = "",
):
    if redir := _require_login(request):
        return redir
    settings = load_settings()
    settings.domain = domain.strip().lower()
    settings.acme_email = acme_email.strip()
    settings.acme_staging = acme_staging == "on"
    try:
        save_settings(settings, rewrite_hosts=True)
    except ValueError as exc:
        _flash(request, str(exc), "error")
        return RedirectResponse("/settings", status_code=303)
    _flash(request, "Settings saved. Hostnames were rewritten if the domain changed.")
    return RedirectResponse("/settings", status_code=303)
