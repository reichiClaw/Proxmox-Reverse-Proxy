from __future__ import annotations

import pyotp
import pytest

from gui.app import auth, totp
from gui.app.store import save_totp_secret
from .conftest import TEST_PASSWORD, do_login, do_setup, get_csrf


class TestTotpHelpers:
    def test_generate_and_verify(self):
        secret = totp.generate_secret()
        code = pyotp.TOTP(secret).now()
        ok, step = totp.verify_code(secret, code)
        assert ok
        assert step is not None

    def test_rejects_bad_code(self):
        secret = totp.generate_secret()
        assert totp.verify_code(secret, "000000")[0] is False
        assert totp.verify_code(secret, "abc")[0] is False
        assert totp.verify_code(secret, "")[0] is False

    def test_replay_blocked(self):
        secret = totp.generate_secret()
        code = pyotp.TOTP(secret).now()
        ok, step = totp.verify_code(secret, code)
        assert ok
        assert totp.verify_code(secret, code, last_timestep=step)[0] is False

    def test_qr_and_manual(self):
        secret = totp.generate_secret()
        uri = totp.provisioning_uri(secret, "admin")
        assert uri.startswith("otpauth://totp/")
        assert "Proxmox%20Gate" in uri or "Proxmox Gate" in uri
        data = totp.qr_svg_data_uri(secret, "admin")
        assert data.startswith("data:image/svg+xml,")
        assert " " in totp.manual_entry_key(secret)


class TestTotpLoginFlow:
    def test_password_alone_insufficient_when_enabled(self, client):
        do_setup(client)
        secret = totp.generate_secret()
        save_totp_secret(secret)
        client.post("/logout", data={"csrf_token": get_csrf(client, "/services")})

        resp = do_login(client)
        assert resp.headers["location"] == "/login/totp"
        assert client.get("/services", follow_redirects=False).headers["location"] == "/login/totp"

        token = get_csrf(client, "/login/totp")
        bad = client.post(
            "/login/totp",
            data={"code": "000000", "csrf_token": token},
            follow_redirects=False,
        )
        assert bad.headers["location"] == "/login/totp"

        token = get_csrf(client, "/login/totp")
        code = pyotp.TOTP(secret).now()
        ok = client.post(
            "/login/totp",
            data={"code": code, "csrf_token": token},
            follow_redirects=False,
        )
        assert ok.headers["location"] == "/services"
        assert client.get("/services").status_code == 200

    def test_enable_via_settings(self, client):
        do_setup(client)
        assert not auth.totp_enabled()

        token = get_csrf(client, "/settings")
        client.post("/settings/totp/start", data={"csrf_token": token}, follow_redirects=False)
        page = client.get("/settings")
        assert "Confirm and enable" in page.text
        assert "data:image/svg+xml" in page.text

        # Pull pending secret from session via store not yet written
        assert not auth.totp_enabled()

        # Confirm with wrong code first
        token = get_csrf(client, "/settings")
        client.post(
            "/settings/totp/confirm",
            data={"code": "000000", "csrf_token": token},
            follow_redirects=False,
        )
        assert not auth.totp_enabled()

        # Read secret from session by confirming with a real code:
        # regenerate by inspecting SESSION — use store after we confirm
        # Extract secret from page manual key
        import re

        page = client.get("/settings")
        match = re.search(r"<code>([A-Z0-9 ]+)</code>", page.text)
        assert match
        secret = match.group(1).replace(" ", "")
        token = get_csrf(client, "/settings")
        code = pyotp.TOTP(secret).now()
        client.post(
            "/settings/totp/confirm",
            data={"code": code, "csrf_token": token},
            follow_redirects=False,
        )
        assert auth.totp_enabled()

        # Disable requires password + code
        token = get_csrf(client, "/settings")
        client.post(
            "/settings/totp/disable",
            data={
                "password": TEST_PASSWORD,
                "code": pyotp.TOTP(secret).now(),
                "csrf_token": token,
            },
            follow_redirects=False,
        )
        # May fail if same timestep was used at confirm — try once more if still enabled
        if auth.totp_enabled():
            # wait not needed if window advanced; use verify_totp which blocks replay
            # Force clear last timestep for disable path
            auth._last_totp_timestep = None
            token = get_csrf(client, "/settings")
            client.post(
                "/settings/totp/disable",
                data={
                    "password": TEST_PASSWORD,
                    "code": pyotp.TOTP(secret).now(),
                    "csrf_token": token,
                },
                follow_redirects=False,
            )
        assert not auth.totp_enabled()

    def test_auth_verify_totp_helper(self):
        do_setup_secret = totp.generate_secret()
        save_totp_secret(do_setup_secret)
        assert auth.totp_enabled()
        code = pyotp.TOTP(do_setup_secret).now()
        assert auth.verify_totp(code)
        assert not auth.verify_totp(code)  # replay
