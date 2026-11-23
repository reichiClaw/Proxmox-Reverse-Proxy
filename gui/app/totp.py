"""TOTP (RFC 6238) helpers for Gate admin 2FA."""
from __future__ import annotations

import time
from urllib.parse import quote

import pyotp
import qrcode
import qrcode.image.svg

ISSUER = "Proxmox Gate"


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def verify_code(
    secret: str, code: str, *, last_timestep: int | None = None
) -> tuple[bool, int | None]:
    """Verify a 6-digit code. Returns (ok, timestep_used).

    Rejects reuse of the same timestep when ``last_timestep`` is provided
    (blocks replaying the same code within its 30s window).
    """
    code = (code or "").strip().replace(" ", "")
    if not secret or not code.isdigit() or len(code) != 6:
        return False, None

    totp = pyotp.TOTP(secret)
    now = time.time()
    interval = totp.interval
    for offset in (0, -1, 1):
        for_time = now + offset * interval
        timestep = int(for_time) // interval
        if last_timestep is not None and timestep == last_timestep:
            continue
        if totp.verify(code, for_time=for_time, valid_window=0):
            return True, timestep
    return False, None


def qr_svg_data_uri(secret: str, username: str) -> str:
    """Return a data: URI SVG QR code for authenticator apps."""
    uri = provisioning_uri(secret, username)
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
    buf = __import__("io").BytesIO()
    img.save(buf)
    return f"data:image/svg+xml,{quote(buf.getvalue().decode('utf-8'))}"


def manual_entry_key(secret: str) -> str:
    """Grouped base32 for easier manual entry."""
    s = secret.replace(" ", "").upper()
    return " ".join(s[i : i + 4] for i in range(0, len(s), 4))
