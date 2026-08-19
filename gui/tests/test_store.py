from __future__ import annotations

import stat

import pytest

from gui.app import store
from gui.app.paths import APPS_DIR, SETTINGS_FILE


class TestValidateName:
    def test_accepts_simple_labels(self):
        assert store.validate_name("gitea") == "gitea"
        assert store.validate_name("my-app") == "my-app"
        assert store.validate_name("  Gitea  ") == "gitea"

    @pytest.mark.parametrize(
        "bad",
        ["", "-app", "app-", "a b", "../etc", "a/b", "app.name", "a" * 64, "über"],
    )
    def test_rejects_invalid(self, bad):
        with pytest.raises(ValueError):
            store.validate_name(bad)

    def test_rejects_reserved(self):
        with pytest.raises(ValueError):
            store.validate_name("gate")


class TestValidateUpstream:
    @pytest.mark.parametrize(
        "ok",
        [
            "http://10.0.0.5:3000",
            "https://pve.internal:8006",
            "http://host",
            "http://[fd00::1]:9000",
            "https://app.example.com/base/path",
        ],
    )
    def test_accepts_valid(self, ok):
        assert store.validate_upstream(ok) == ok

    @pytest.mark.parametrize(
        "bad",
        [
            "ftp://host",
            "host:3000",
            "http://user:pass@host",
            "http://host:99999",
            "http://host?x=1",
            "http://host#frag",
            'http://host"/x',
            "http://host`whoami`",
            "http://host/path with space",
            "http://host/x\ny",
            "http://host|evil",
        ],
    )
    def test_rejects_invalid(self, bad):
        with pytest.raises(ValueError):
            store.validate_upstream(bad)


class TestValidateDomain:
    def test_accepts_valid(self):
        assert store.validate_domain("Lab.Example.COM") == "lab.example.com"

    @pytest.mark.parametrize(
        "bad",
        [
            "localhost",  # no dot
            "",
            "exa mple.com",
            "example.com`) || Host(`evil.com",
            "example..com",
            "-bad.example.com",
        ],
    )
    def test_rejects_invalid(self, bad):
        with pytest.raises(ValueError):
            store.validate_domain(bad)


class TestServices:
    def test_create_and_delete(self):
        svc = store.create_service("gitea", "http://10.0.0.5:3000")
        assert svc.host == "gitea.test.example.com"
        path = APPS_DIR / "gitea.yml"
        assert path.exists()
        content = path.read_text()
        assert 'url: "http://10.0.0.5:3000"' in content
        assert "Host(`gitea.test.example.com`)" in content

        store.delete_service("gitea")
        assert not path.exists()

    def test_duplicate_create_rejected(self):
        store.create_service("gitea", "http://10.0.0.5:3000")
        with pytest.raises(ValueError):
            store.create_service("gitea", "http://10.0.0.6:3000")

    def test_system_service_cannot_be_deleted(self):
        with pytest.raises(ValueError):
            store.delete_service("pve")

    def test_rename_service(self):
        store.create_service("gitea", "http://10.0.0.5:3000")
        svc = store.update_service("gitea", upstream="http://10.0.0.5:3000", new_name="forgejo")
        assert svc.name == "forgejo"
        assert not (APPS_DIR / "gitea.yml").exists()
        assert (APPS_DIR / "forgejo.yml").exists()

    def test_invalid_middleware_names_rejected_on_render(self):
        with pytest.raises(ValueError):
            store._render_app_yaml(
                "app", "test.example.com", "http://h:80", ["ok", "bad`injection"]
            )


class TestSecrets:
    def test_gui_env_written_0600(self):
        store.save_gui_auth("admin", "$2b$12$" + "x" * 53)
        mode = stat.S_IMODE(SETTINGS_FILE.stat().st_mode)
        assert mode == 0o600

    def test_env_values_reject_newlines(self):
        with pytest.raises(ValueError):
            store._write_env_file(
                SETTINGS_FILE, {"A": "value\nB=injected"}, "# test"
            )


class TestSettings:
    def test_save_settings_rewrites_hosts(self):
        store.create_service("gitea", "http://10.0.0.5:3000")
        settings = store.load_settings()
        settings.domain = "new.example.org"
        store.save_settings(settings)
        content = (APPS_DIR / "gitea.yml").read_text()
        assert "gitea.new.example.org" in content
        assert "test.example.com" not in content

    def test_save_settings_rejects_injection_domain(self):
        settings = store.load_settings()
        settings.domain = "evil.com`) || PathPrefix(`/"
        with pytest.raises(ValueError):
            store.save_settings(settings)

    def test_acme_toggle_patches_traefik_yml(self):
        from gui.app.paths import TRAEFIK_YML

        settings = store.load_settings()
        settings.acme_staging = False
        store.save_settings(settings)
        text = TRAEFIK_YML.read_text()
        assert "acme-v02.api.letsencrypt.org" in text.split("# Production:")[0]
        assert store.load_settings().acme_staging is False
