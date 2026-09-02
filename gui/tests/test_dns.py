from __future__ import annotations

import pytest

from gui.app import dns
from gui.app.dns import DnsConfig, MANAGED_COMMENT


class FakeCloudflare:
    """Stands in for dns._api — records state and mutations."""

    def __init__(self, zones: dict[str, str], records: dict[str, list[dict]] | None = None):
        self.zones = zones  # name -> id
        self.records = records or {}  # zone_id -> [record]
        self.calls: list[tuple[str, str]] = []
        self._next_id = 1000

    def __call__(self, method: str, path: str, token: str, payload: dict | None = None) -> dict:
        self.calls.append((method, path))
        if method == "GET" and path.startswith("/zones?"):
            return {
                "success": True,
                "result": [{"name": n, "id": i} for n, i in self.zones.items()],
                "result_info": {"total_pages": 1},
            }
        if method == "GET" and "/dns_records" in path:
            zone_id = path.split("/")[2]
            name = path.split("name=")[1]
            import urllib.parse

            name = urllib.parse.unquote(name)
            matching = [r for r in self.records.get(zone_id, []) if r["name"] == name]
            return {"success": True, "result": matching}
        if method == "POST" and path.endswith("/dns_records"):
            zone_id = path.split("/")[2]
            record = dict(payload or {}, id=str(self._next_id))
            self._next_id += 1
            self.records.setdefault(zone_id, []).append(record)
            return {"success": True, "result": record}
        if method == "PUT":
            zone_id, record_id = path.split("/")[2], path.split("/")[4]
            for i, record in enumerate(self.records.get(zone_id, [])):
                if record["id"] == record_id:
                    self.records[zone_id][i] = dict(payload or {}, id=record_id)
            return {"success": True, "result": payload}
        if method == "DELETE":
            zone_id, record_id = path.split("/")[2], path.split("/")[4]
            self.records[zone_id] = [
                r for r in self.records.get(zone_id, []) if r["id"] != record_id
            ]
            return {"success": True, "result": {"id": record_id}}
        raise AssertionError(f"unexpected API call {method} {path}")


CFG = DnsConfig(api_token="test-token", target="gate.example.com", proxied=False)


@pytest.fixture
def fake(monkeypatch):
    def _install(zones=None, records=None):
        api = FakeCloudflare(zones or {"example.com": "z1"}, records)
        monkeypatch.setattr(dns, "_api", api)
        return api

    return _install


class TestEnsure:
    def test_disabled_without_config(self):
        assert dns.ensure_cname("app.example.com", DnsConfig()) == "disabled"

    def test_creates_record(self, fake):
        api = fake()
        assert dns.ensure_cname("app.example.com", CFG) == "created"
        record = api.records["z1"][0]
        assert record["type"] == "CNAME"
        assert record["name"] == "app.example.com"
        assert record["content"] == "gate.example.com"
        assert record["comment"] == MANAGED_COMMENT
        assert record["proxied"] is False

    def test_idempotent(self, fake):
        fake(
            records={
                "z1": [
                    {
                        "id": "1",
                        "type": "CNAME",
                        "name": "app.example.com",
                        "content": "gate.example.com",
                        "proxied": False,
                        "comment": MANAGED_COMMENT,
                    }
                ]
            }
        )
        assert dns.ensure_cname("app.example.com", CFG) == "exists"

    def test_updates_managed_record_with_stale_target(self, fake):
        api = fake(
            records={
                "z1": [
                    {
                        "id": "1",
                        "type": "CNAME",
                        "name": "app.example.com",
                        "content": "old-target.example.com",
                        "proxied": False,
                        "comment": MANAGED_COMMENT,
                    }
                ]
            }
        )
        assert dns.ensure_cname("app.example.com", CFG) == "updated"
        assert api.records["z1"][0]["content"] == "gate.example.com"

    def test_never_touches_unmanaged_records(self, fake):
        api = fake(
            records={
                "z1": [
                    {
                        "id": "1",
                        "type": "A",
                        "name": "app.example.com",
                        "content": "203.0.113.7",
                        "comment": "",
                    }
                ]
            }
        )
        assert dns.ensure_cname("app.example.com", CFG) == "conflict"
        assert api.records["z1"][0]["content"] == "203.0.113.7"

    def test_no_matching_zone(self, fake):
        fake(zones={"other.net": "z9"})
        assert dns.ensure_cname("app.example.com", CFG) == "no-zone"

    def test_longest_suffix_zone_wins(self, fake):
        api = fake(zones={"example.com": "z1", "lab.example.com": "z2"})
        assert dns.ensure_cname("app.lab.example.com", CFG) == "created"
        assert api.records["z2"]  # created in the more specific zone

    def test_skips_self_target(self, fake):
        fake()
        assert dns.ensure_cname("gate.example.com", CFG) == "skipped-target"


class TestDelete:
    def test_deletes_managed_only(self, fake):
        api = fake(
            records={
                "z1": [
                    {
                        "id": "1",
                        "type": "CNAME",
                        "name": "app.example.com",
                        "content": "gate.example.com",
                        "comment": MANAGED_COMMENT,
                    }
                ]
            }
        )
        assert dns.delete_cname("app.example.com", CFG) == "deleted"
        assert api.records["z1"] == []

    def test_keeps_unmanaged(self, fake):
        api = fake(
            records={
                "z1": [
                    {"id": "1", "type": "A", "name": "app.example.com", "content": "203.0.113.7"}
                ]
            }
        )
        assert dns.delete_cname("app.example.com", CFG) == "not-managed"
        assert len(api.records["z1"]) == 1

    def test_absent(self, fake):
        fake()
        assert dns.delete_cname("app.example.com", CFG) == "absent"


class TestConfigStore:
    def test_roundtrip_and_permissions(self):
        import stat

        dns.save_dns_config(DnsConfig(api_token="tok", target="Gate.Example.COM.", proxied=True))
        loaded = dns.load_dns_config()
        assert loaded.api_token == "tok"
        assert loaded.target == "gate.example.com"
        assert loaded.proxied is True
        mode = stat.S_IMODE(dns.CLOUDFLARE_ENV.stat().st_mode)
        assert mode == 0o600

    def test_disabled_when_incomplete(self):
        assert not DnsConfig(api_token="tok").enabled
        assert not DnsConfig(target="t.example.com").enabled
        assert DnsConfig(api_token="tok", target="t.example.com").enabled


class TestSyncAll:
    def test_syncs_every_service_host(self, fake):
        from gui.app import store

        api = fake(zones={"test.example.com": "z1"})
        dns.save_dns_config(DnsConfig(api_token="tok", target="gate.test.example.com"))
        store.create_service("app1", "http://10.0.0.5:80")
        results = dict(dns.sync_all())
        assert results["app1.test.example.com"] == "created"
        # target host itself is skipped, never self-CNAMEd
        assert results.get("gate.test.example.com") in (None, "skipped-target")
        assert any("dns_records" in p for _, p in api.calls)
