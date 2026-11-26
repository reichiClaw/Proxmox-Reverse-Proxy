from __future__ import annotations

from gui.app.diagnose import build_diagnose_report, _redact
from gui.app import store


def test_redact_secrets():
    assert "***" in _redact("Authorization: Bearer abc.def.ghi")
    assert "***" in _redact("X-Gotify-Key: supersecret")
    assert "***" in _redact("api_key=abc123xyz")


def test_diagnose_report_includes_services():
    store.create_service("diag", "http://10.0.0.5:80")
    report = build_diagnose_report()
    assert "diag" in report
    assert "http://10.0.0.5:80" in report
    assert "Secrets redacted" in report
