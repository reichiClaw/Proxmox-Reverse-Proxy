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
TEMPLATE_YML = APPS_DIR / "_template.yml.example"
SETTINGS_FILE = CONFIG_DIR / "gui.env"
# Outside Traefik's watched tree — revision/status/backups live here.
STATUS_FILE = CONFIG_DIR / "runtime-status.json"
BACKUP_DIR = CONFIG_DIR / "backups"
# Touched after every publish so Traefik's top-level directory watcher reloads
# even when only a nested apps/*.yml changed (or a prior sibling read failed).
REVISION_YML = DYNAMIC_DIR / "gate-revision.yml"
# Local-only Traefik API (entryPoint traefik on 127.0.0.1:8090).
TRAEFIK_API_URL = os.environ.get("GATE_TRAEFIK_API", "http://127.0.0.1:8090")
