from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("GATE_HOST", "0.0.0.0")
    port = int(os.environ.get("GATE_PORT", "8080"))
    uvicorn.run("gui.app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
