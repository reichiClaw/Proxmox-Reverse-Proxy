from __future__ import annotations

import re
import secrets
import threading
import time

import bcrypt

from .store import load_settings, save_gui_auth

SESSION_USER_KEY = "gate_user"

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
MIN_PASSWORD_LENGTH = 10

# Verified against when no account exists or the username does not match, so
# a login attempt always costs one bcrypt verification (no timing oracle for
# valid usernames).
_DUMMY_HASH = bcrypt.hashpw(secrets.token_urlsafe(24).encode(), bcrypt.gensalt(rounds=12)).decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        password_hash = _DUMMY_HASH
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def is_bootstrap_required() -> bool:
    settings = load_settings()
    return not bool(settings.admin_password_hash)


def authenticate(username: str, password: str) -> bool:
    settings = load_settings()
    stored_hash = settings.admin_password_hash
    # Evaluate both factors unconditionally to keep timing independent of
    # which one failed.
    user_ok = secrets.compare_digest(
        username.encode("utf-8"), settings.admin_user.encode("utf-8")
    )
    pass_ok = verify_password(password, stored_hash)
    return bool(stored_hash) and user_ok and pass_ok


def bootstrap_admin(username: str, password: str) -> str:
    username = (username or "admin").strip()
    if not USERNAME_RE.match(username):
        raise ValueError(
            "Username must be 1-32 characters: letters, digits, '.', '_' or '-'."
        )
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    save_gui_auth(username, hash_password(password))
    return username


class LoginThrottle:
    """In-memory failed-login throttle.

    Locks out a key (username and client address are tracked separately)
    after `max_failures` failures within `window` seconds, for `lockout`
    seconds. State is process-local, which matches the single-process
    deployment model of the Gate GUI.
    """

    def __init__(self, max_failures: int = 5, window: float = 300.0, lockout: float = 900.0):
        self.max_failures = max_failures
        self.window = window
        self.lockout = lockout
        self._lock = threading.Lock()
        self._state: dict[str, tuple[list[float], float]] = {}

    def _prune(self, now: float) -> None:
        if len(self._state) < 10000:
            return
        for key in list(self._state):
            failures, locked_until = self._state[key]
            if locked_until < now and all(now - t > self.window for t in failures):
                del self._state[key]

    def is_locked(self, *keys: str) -> bool:
        now = time.monotonic()
        with self._lock:
            for key in keys:
                _, locked_until = self._state.get(key, ([], 0.0))
                if locked_until > now:
                    return True
        return False

    def record_failure(self, *keys: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            for key in keys:
                failures, locked_until = self._state.get(key, ([], 0.0))
                failures = [t for t in failures if now - t <= self.window]
                failures.append(now)
                if len(failures) >= self.max_failures:
                    locked_until = now + self.lockout
                    failures = []
                self._state[key] = (failures, locked_until)

    def record_success(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._state.pop(key, None)


login_throttle = LoginThrottle()
