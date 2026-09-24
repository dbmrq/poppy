"""Configuration handling for ~/.poppy/config.json."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from .util import PoppyError, load_json, save_json

DEFAULT_CONFIG = {
    "version": 1,
    "lookback_days": 14,
    "max_candidates_per_run": 5,
    "max_sessions_per_run": 500,
    "min_evidence": 1,
    "decay_after_days": 90,
    "decay_scan": True,
    "skills_dirs": [],
    "agent": {
        "miner": {"cmd": None, "timeout_sec": 1800},
        "writer": {"cmd": None, "timeout_sec": 900},
    },
    "ui": {"host": "127.0.0.1", "port": 8788, "token": ""},
    "publish": {"target": "", "subdir": ""},
    "sync": {
        "enabled": False,
        "remote": "",
        "branch": "main",
        "machine": "",
        "interval_min": 30,
        "schedule": True,
    },
}

COMMON_SKILL_DIRS = (
    "~/.agents/skills",
    "~/.config/opencode/skills",
    "~/.claude/skills",
    "~/.pi/agent/skills",
    "~/.codex/skills",
    "~/.cursor/skills",
    "~/.gemini/skills",
)


def config_path(home: Path) -> Path:
    return home / "config.json"


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(home: Path) -> dict:
    return _merge(DEFAULT_CONFIG, load_json(config_path(home), {}) or {})


def save_config(home: Path, cfg: dict) -> None:
    save_json(config_path(home), cfg, mode=0o600)


def get_dotted(cfg: dict, key: str):
    node = cfg
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise PoppyError(f"unknown config key: {key}")
        node = node[part]
    return node


def set_dotted(cfg: dict, key: str, value) -> None:
    parts = key.split(".")
    node = cfg
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def detect_skills_dirs() -> list[str]:
    return [p for p in COMMON_SKILL_DIRS if Path(p).expanduser().is_dir()]


def init_config(home: Path, force: bool = False) -> tuple[dict, bool]:
    """Create config.json if missing (or overwrite with --force). Returns (cfg, created)."""
    path = config_path(home)
    if path.exists() and not force:
        return load_config(home), False
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["skills_dirs"] = detect_skills_dirs()
    save_config(home, cfg)
    return cfg, True


def load_config_or_fail(home: Path) -> dict:
    if not config_path(home).exists():
        raise PoppyError(f"missing {config_path(home)} — run `poppy init` first")
    return load_config(home)


def parse_value(text: str):
    """Parse a CLI value as JSON when possible, else keep it as a string."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
