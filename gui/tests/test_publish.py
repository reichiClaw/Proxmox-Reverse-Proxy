from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

from gui.app import publish, runtime, statusstore, store
from gui.app.paths import APPS_DIR, DYNAMIC_DIR, REVISION_YML, STATUS_FILE


def test_create_bumps_revision_and_keeps_0644():
    store.create_service("app1", "http://10.0.0.5:80")
    assert REVISION_YML.exists()
    text = REVISION_YML.read_text()
    assert "app1.yml" in text or "revised:" in text
    assert (REVISION_YML.stat().st_mode & 0o777) == 0o644
    assert ((APPS_DIR / "app1.yml").stat().st_mode & 0o777) == 0o644


def test_ensure_dynamic_readable_fixes_0600():
    store.create_service("locked", "http://10.0.0.5:80")
    path = APPS_DIR / "locked.yml"
    os.chmod(path, 0o600)
    assert (path.stat().st_mode & 0o777) == 0o600
    fixed = store.ensure_dynamic_readable()
    assert any("locked.yml" in f for f in fixed)
    assert (path.stat().st_mode & 0o777) == 0o644
    assert store.list_unreadable_dynamic() == []


def test_list_unreadable_reports_0600():
    store.create_service("secretish", "http://10.0.0.5:80")
    path = APPS_DIR / "secretish.yml"
    os.chmod(path, 0o600)
    bad = store.list_unreadable_dynamic()
    assert any("secretish.yml" in b for b in bad)


def test_backup_outside_watch_tree():
    store.create_service("bak", "http://10.0.0.5:80")
    path = APPS_DIR / "bak.yml"
    dest = statusstore.backup_file(path)
    assert dest is not None
    assert "backups" in str(dest)
    assert DYNAMIC_DIR not in dest.parents or dest.parent.name == "backups"
    assert dest.read_text() == path.read_text()


def test_record_saved_status():
    store.create_service("st", "http://10.0.0.5:80")
    assert STATUS_FILE.exists()
    st = statusstore.get_service_status("st")
    assert st.get("saved_upstream") == "http://10.0.0.5:80"
    assert st.get("saved_at")


def test_verify_matches_when_runtime_agrees():
    store.create_service("vr", "http://10.0.0.5:80")
    snap = runtime.RuntimeSnapshot(
        ok=True,
        fetched_at="now",
        api_url="http://127.0.0.1:8090",
    )
    snap.services["vr"] = runtime.ActiveService(
        name="vr", provider="file", upstream="http://10.0.0.5:80", status="enabled"
    )
    with mock.patch.object(runtime, "fetch_runtime", return_value=snap):
        with mock.patch.object(publish.probe, "probe_direct") as pd:
            with mock.patch.object(publish.probe, "probe_via_traefik") as pv:
                pd.return_value = publish.probe.ProbeResult(
                    kind="direct", ok=True, target="http://10.0.0.5:80", http_status=200
                )
                pv.return_value = publish.probe.ProbeResult(
                    kind="via_traefik", ok=True, target="https://vr.test.example.com/", http_status=200
                )
                result = publish.verify_service("vr", run_probes=True)
    assert result.ok
    assert result.active_matched is True
    assert result.saved_upstream == "http://10.0.0.5:80"


def test_verify_detects_stale_active_upstream():
    store.create_service("stale", "http://10.0.0.4:8006")
    snap = runtime.RuntimeSnapshot(
        ok=True,
        fetched_at="now",
        api_url="http://127.0.0.1:8090",
    )
    snap.services["stale"] = runtime.ActiveService(
        name="stale",
        provider="file",
        upstream="https://172.16.0.2:8006",
        status="enabled",
    )
    with mock.patch.object(runtime, "fetch_runtime", return_value=snap):
        with mock.patch.object(publish.probe, "probe_direct") as pd:
            with mock.patch.object(publish.probe, "probe_via_traefik") as pv:
                pd.return_value = publish.probe.ProbeResult(
                    kind="direct", ok=False, target="x", error="skip"
                )
                pv.return_value = publish.probe.ProbeResult(
                    kind="via_traefik", ok=False, target="x", error="skip"
                )
                result = publish.verify_service("stale", run_probes=True)
    assert not result.ok
    assert result.active_matched is False
    assert "172.16.0.2" in (result.active_upstream or "")
    assert "10.0.0.4" in result.message


def test_update_system_pve_preserves_transport():
    store.update_system_service("pve", upstream="https://10.0.0.4:8006")
    from gui.app.paths import PVE_YML
    import yaml

    doc = yaml.safe_load(PVE_YML.read_text())
    pve = doc["http"]["services"]["pve"]["loadBalancer"]
    assert pve["servers"][0]["url"] == "https://10.0.0.4:8006"
    assert pve.get("serversTransport") == "pve-transport"
    assert "pve-transport" in doc["http"]["serversTransports"]
