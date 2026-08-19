from __future__ import annotations

import os

import uvicorn


def main() -> None:
    # Localhost by default: the GUI is published through Traefik
    # (gate.<domain>), never bound to a public interface directly.
    # Override with GATE_HOST only for deliberate LAN-only setups.
    host = os.environ.get("GATE_HOST", "127.0.0.1")
    port = int(os.environ.get("GATE_PORT", "8080"))
    uvicorn.run("gui.app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
