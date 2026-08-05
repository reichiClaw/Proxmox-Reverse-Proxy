from __future__ import annotations

import os
from pathlib import Path

# gui/app/paths.py → repo root is parents[2]
REPO_ROOT = Path(os.environ.get("GATE_REPO_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("GATE_CONFIG_DIR", REPO_ROOT / "config"))
BASE_ENV = CONFIG_DIR / "base.env"
BASE_ENV_EXAMPLE = CONFIG_DIR / "base.env.example"
TRAEFIK_YML = CONFIG_DIR / "traefik.yml"
DYNAMIC_DIR = CONFIG_DIR / "dynamic"
APPS_DIR = DYNAMIC_DIR / "apps"
PVE_YML = DYNAMIC_DIR / "pve.yml"
MIDDLEWARES_YML = DYNAMIC_DIR / "middlewares.yml"
TEMPLATE_YML = APPS_DIR / "_template.yml"
SETTINGS_FILE = CONFIG_DIR / "gui.env"
