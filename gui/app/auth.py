from __future__ import annotations

from passlib.context import CryptContext

from .store import load_settings, save_gui_auth

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SESSION_USER_KEY = "gate_user"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return pwd_context.verify(password, password_hash)
    except Exception:
        return False


def is_bootstrap_required() -> bool:
    settings = load_settings()
    return not bool(settings.admin_password_hash)


def authenticate(username: str, password: str) -> bool:
    settings = load_settings()
    if not settings.admin_password_hash:
        return False
    if username != settings.admin_user:
        return False
    return verify_password(password, settings.admin_password_hash)


def bootstrap_admin(username: str, password: str) -> None:
    username = (username or "admin").strip() or "admin"
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    save_gui_auth(username, hash_password(password))
