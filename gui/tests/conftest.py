"""Test fixtures.

GATE_CONFIG_DIR must be set before any gui.app import because paths are
resolved at import time; conftest is imported by pytest before the tests.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_TMP = Path(tempfile.mkdtemp(prefix="gate-test-"))

os.environ["GATE_CONFIG_DIR"] = str(_TMP / "config")
os.environ["GATE_REPO_ROOT"] = str(_TMP)
os.environ.pop("GATE_SESSION_SECRET", None)
os.environ.pop("GATE_HTTPS_ONLY", None)

import pytest  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

TEST_DOMAIN = "test.example.com"
TEST_PASSWORD = "correct-horse-battery"


def _reset_config() -> None:
    cfg = _TMP / "config"
    if cfg.exists():
        shutil.rmtree(cfg)
    shutil.copytree(REPO_ROOT / "config", cfg)
    (cfg / "base.env").write_text(
        f"DOMAIN={TEST_DOMAIN}\nACME_EMAIL=admin@{TEST_DOMAIN}\n", encoding="utf-8"
    )
    gui_env = cfg / "gui.env"
    if gui_env.exists():
        gui_env.unlink()


@pytest.fixture(autouse=True)
def fresh_config():
    _reset_config()
    from gui.app.auth import login_throttle

    login_throttle._state.clear()
    yield


@pytest.fixture
def client():
    from gui.app.main import app

    with TestClient(app) as c:
        yield c


def get_csrf(client: TestClient, path: str) -> str:
    resp = client.get(path)
    match = re.search(r'name="csrf_token" value="([^"]+)"', resp.text)
    assert match, f"no csrf token found on {path}"
    return match.group(1)


def do_setup(client: TestClient, username: str = "admin", password: str = TEST_PASSWORD):
    token = get_csrf(client, "/setup")
    return client.post(
        "/setup",
        data={
            "username": username,
            "password": password,
            "password2": password,
            "csrf_token": token,
        },
        follow_redirects=False,
    )


def do_login(client: TestClient, username: str = "admin", password: str = TEST_PASSWORD):
    token = get_csrf(client, "/login")
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=False,
    )
