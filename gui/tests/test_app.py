from __future__ import annotations

from gui.app.paths import APPS_DIR

from .conftest import TEST_PASSWORD, do_login, do_setup, get_csrf


class TestHealth:
    def test_healthz_public_and_minimal(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestBootstrapFlow:
    def test_everything_redirects_to_setup_before_bootstrap(self, client):
        for path in ("/", "/services", "/settings", "/login"):
            resp = client.get(path, follow_redirects=False)
            assert resp.status_code == 303
            assert resp.headers["location"] == "/setup"

    def test_setup_creates_admin_and_signs_in(self, client):
        resp = do_setup(client)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/services"
        assert client.get("/services").status_code == 200

    def test_setup_rejects_missing_csrf(self, client):
        client.get("/setup")
        resp = client.post(
            "/setup",
            data={"username": "admin", "password": TEST_PASSWORD, "password2": TEST_PASSWORD},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        # still requires setup — no account was created
        from gui.app.auth import is_bootstrap_required

        assert is_bootstrap_required()

    def test_setup_unavailable_after_bootstrap(self, client):
        do_setup(client)
        resp = client.get("/setup", follow_redirects=False)
        assert resp.headers["location"] == "/login"


class TestLogin:
    def test_login_and_logout(self, client):
        do_setup(client)
        client.post("/logout", data={"csrf_token": get_csrf(client, "/services")})
        resp = client.get("/services", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/login")

        resp = do_login(client)
        assert resp.headers["location"] == "/services"
        assert client.get("/services").status_code == 200

    def test_wrong_password_rejected(self, client):
        do_setup(client)
        client.post("/logout", data={"csrf_token": get_csrf(client, "/services")})
        resp = do_login(client, password="not-the-password!")
        assert resp.headers["location"] == "/login"
        resp = client.get("/services", follow_redirects=False)
        assert resp.status_code == 303

    def test_open_redirect_blocked(self, client):
        do_setup(client)
        client.post("/logout", data={"csrf_token": get_csrf(client, "/services")})
        token = get_csrf(client, "/login")
        resp = client.post(
            "/login",
            data={
                "username": "admin",
                "password": TEST_PASSWORD,
                "next": "//evil.example.com/phish",
                "csrf_token": token,
            },
            follow_redirects=False,
        )
        assert resp.headers["location"] == "/services"

    def test_lockout_after_repeated_failures(self, client):
        do_setup(client)
        client.post("/logout", data={"csrf_token": get_csrf(client, "/services")})
        for _ in range(5):
            do_login(client, password="wrong-password-x")
        # correct password now rejected while locked
        resp = do_login(client)
        assert resp.headers["location"] == "/login"
        follow = client.get("/login")
        assert "Too many failed attempts" in follow.text

    def test_session_cookie_flags(self, client):
        resp = client.get("/setup", follow_redirects=False)
        set_cookie = resp.headers.get("set-cookie", "")
        assert "httponly" in set_cookie.lower()
        assert "samesite=lax" in set_cookie.lower()


class TestSecurityHeaders:
    def test_headers_present(self, client):
        resp = client.get("/healthz")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert "default-src 'self'" in resp.headers["Content-Security-Policy"]
        assert "script-src 'self'" in resp.headers["Content-Security-Policy"]


class TestServiceCrud:
    def test_create_requires_auth(self, client):
        do_setup(client)
        client.post("/logout", data={"csrf_token": get_csrf(client, "/services")})
        resp = client.post(
            "/services/new",
            data={"name": "sneaky", "upstream": "http://10.0.0.9:80", "csrf_token": "x"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert not (APPS_DIR / "sneaky.yml").exists()

    def test_create_requires_csrf(self, client):
        do_setup(client)
        resp = client.post(
            "/services/new",
            data={"name": "sneaky", "upstream": "http://10.0.0.9:80"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert not (APPS_DIR / "sneaky.yml").exists()

    def test_create_edit_delete_with_csrf(self, client):
        do_setup(client)
        token = get_csrf(client, "/services/new")
        client.post(
            "/services/new",
            data={"name": "gitea", "upstream": "http://10.0.0.5:3000", "csrf_token": token},
        )
        assert (APPS_DIR / "gitea.yml").exists()

        token = get_csrf(client, "/services/gitea/edit")
        client.post(
            "/services/gitea/edit",
            data={"upstream": "http://10.0.0.5:3001", "new_name": "gitea", "csrf_token": token},
        )
        assert 'url: "http://10.0.0.5:3001"' in (APPS_DIR / "gitea.yml").read_text()

        token = get_csrf(client, "/services")
        client.post("/services/gitea/delete", data={"csrf_token": token})
        assert not (APPS_DIR / "gitea.yml").exists()

    def test_create_rejects_bad_upstream(self, client):
        do_setup(client)
        token = get_csrf(client, "/services/new")
        client.post(
            "/services/new",
            data={"name": "bad", "upstream": 'http://h"/inject', "csrf_token": token},
        )
        assert not (APPS_DIR / "bad.yml").exists()

    def test_system_service_delete_blocked(self, client):
        do_setup(client)
        token = get_csrf(client, "/services")
        client.post("/services/pve/delete", data={"csrf_token": token})
        from gui.app.paths import PVE_YML

        assert PVE_YML.exists()


class TestSettingsPage:
    def test_settings_update_and_injection_rejected(self, client):
        do_setup(client)
        token = get_csrf(client, "/settings")
        client.post(
            "/settings",
            data={
                "domain": "new.example.org",
                "acme_email": "ops@new.example.org",
                "acme_staging": "on",
                "csrf_token": token,
            },
        )
        from gui.app.store import load_settings

        assert load_settings().domain == "new.example.org"

        token = get_csrf(client, "/settings")
        client.post(
            "/settings",
            data={
                "domain": "evil.com`) || Host(`x",
                "acme_email": "ops@new.example.org",
                "acme_staging": "on",
                "csrf_token": token,
            },
        )
        assert load_settings().domain == "new.example.org"
