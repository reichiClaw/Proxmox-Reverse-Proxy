"""Connection probes: direct backend vs via Traefik (SNI)."""
from __future__ import annotations

import logging
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger("gate.probe")


@dataclass
class ProbeResult:
    kind: str  # direct | via_traefik
    ok: bool
    target: str
    http_status: int | None = None
    duration_ms: int | None = None
    tls_ok: bool | None = None
    tls_error: str = ""
    error: str = ""
    detail: str = ""
    checked_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tcp_connect(host: str, port: int, timeout: float) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, ""
    except OSError as exc:
        return False, str(exc)


def probe_direct(upstream: str, *, timeout: float = 5.0, verify_tls: bool = True) -> ProbeResult:
    """Reach the backend URL from the Gate host (same view Traefik uses)."""
    result = ProbeResult(kind="direct", ok=False, target=upstream, checked_at=_now())
    try:
        parts = urlsplit(upstream)
    except ValueError as exc:
        result.error = f"Invalid upstream URL: {exc}"
        return result
    if parts.scheme not in ("http", "https") or not parts.hostname:
        result.error = "Upstream must be http(s)://host[:port]"
        return result

    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    t0 = time.perf_counter()
    ok, err = _tcp_connect(host, port, timeout)
    if not ok:
        result.error = f"TCP connect failed: {err}"
        result.duration_ms = int((time.perf_counter() - t0) * 1000)
        return result

    if parts.scheme == "https":
        ctx = ssl.create_default_context()
        if not verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            result.detail = "TLS verification disabled for this probe"
        try:
            with socket.create_connection((host, port), timeout=timeout) as sock:
                with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                    result.tls_ok = True
                    cert = ssock.getpeercert()
                    subj = ""
                    if cert:
                        for rdn in cert.get("subject", ()):
                            for k, v in rdn:
                                if k == "commonName":
                                    subj = v
                    result.detail = (result.detail + f"; peer CN={subj}").strip("; ")
        except ssl.SSLError as exc:
            result.tls_ok = False
            result.tls_error = str(exc)
            result.duration_ms = int((time.perf_counter() - t0) * 1000)
            result.error = f"TLS handshake failed: {exc}"
            return result
        except OSError as exc:
            result.tls_ok = False
            result.tls_error = str(exc)
            result.duration_ms = int((time.perf_counter() - t0) * 1000)
            result.error = f"TLS connect failed: {exc}"
            return result

    # HTTP GET (may be 401/302 — still proves reachability)
    url = upstream if parts.path else upstream.rstrip("/") + "/"
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "GateProbe/1.0"})
    try:
        # For direct HTTPS with optional skip-verify we already validated TLS above;
        # use unverified context for the HTTP layer when verify_tls is False.
        context = None
        if parts.scheme == "https" and not verify_tls:
            context = ssl._create_unverified_context()  # noqa: S323 — explicit probe mode
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            result.http_status = getattr(resp, "status", None) or resp.getcode()
            result.ok = True
    except urllib.error.HTTPError as exc:
        # 401/403/404 still mean the service answered.
        result.http_status = exc.code
        result.ok = True
        result.detail = (result.detail + f"; HTTP {exc.code}").strip("; ")
    except Exception as exc:  # noqa: BLE001
        result.error = str(exc)
    result.duration_ms = int((time.perf_counter() - t0) * 1000)
    return result


def probe_via_traefik(
    public_host: str,
    *,
    path: str = "/",
    traefik_ip: str = "127.0.0.1",
    timeout: float = 8.0,
    verify_tls: bool = False,
) -> ProbeResult:
    """HTTPS to Traefik with correct SNI/Host (like --resolve host:443:ip)."""
    path = path or "/"
    if not path.startswith("/"):
        path = "/" + path
    target = f"https://{public_host}{path}"
    result = ProbeResult(kind="via_traefik", ok=False, target=target, checked_at=_now())
    t0 = time.perf_counter()

    context = ssl.create_default_context()
    if not verify_tls:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        result.detail = "Client→Traefik TLS not verified (local SNI probe)"

    try:
        # Manual resolve: connect to traefik_ip with SNI=public_host
        with socket.create_connection((traefik_ip, 443), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=public_host) as ssock:
                result.tls_ok = True
                # Minimal HTTP/1.1 request
                payload = (
                    f"GET {path} HTTP/1.1\r\n"
                    f"Host: {public_host}\r\n"
                    f"User-Agent: GateProbe/1.0\r\n"
                    f"Connection: close\r\n\r\n"
                ).encode()
                ssock.sendall(payload)
                buf = b""
                while b"\r\n\r\n" not in buf and len(buf) < 65536:
                    chunk = ssock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
        head = buf.split(b"\r\n", 1)[0].decode("latin-1", errors="replace")
        # HTTP/1.1 200 OK
        parts = head.split()
        if len(parts) >= 2 and parts[1].isdigit():
            result.http_status = int(parts[1])
            result.ok = True
            result.detail = (result.detail + f"; {head}").strip("; ")
        else:
            result.error = f"Unexpected response: {head[:120]}"
    except ssl.SSLError as exc:
        result.tls_ok = False
        result.tls_error = str(exc)
        result.error = f"TLS/SNI failed (often missing ACME cert): {exc}"
    except OSError as exc:
        result.error = f"Connect failed: {exc}"
    result.duration_ms = int((time.perf_counter() - t0) * 1000)
    return result
