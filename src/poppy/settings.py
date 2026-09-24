"""The user-editable slice of config.json, shared by the review UI and tests.

The UI posts only the fields the user changed; values are validated here before
anything is written, so a typo cannot leave a broken config behind. The same
spec drives the panel (`fields()`), the checks (`validate()`), and the write
(`apply()`).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import set_dotted
from .util import PoppyError

_PROMPT_HELP = (
    "One argv token per line. `{prompt}` or `{prompt_file}` is replaced with the prompt; "
    "without either, the prompt goes to stdin."
)

FIELDS = (
    {
        "key": "agent.miner.cmd",
        "label": "Miner command",
        "kind": "lines",
        "help": _PROMPT_HELP,
    },
    {
        "key": "agent.miner.timeout_sec",
        "label": "Miner timeout (seconds)",
        "kind": "number",
        "min": 1,
        "max": 86400,
    },
    {
        "key": "lookback_days",
        "label": "Mining lookback (days)",
        "kind": "number",
        "min": 1,
        "max": 3650,
    },
    {
        "key": "agent.writer.cmd",
        "label": "Writer command",
        "kind": "lines",
        "help": _PROMPT_HELP,
    },
    {
        "key": "agent.writer.timeout_sec",
        "label": "Writer timeout (seconds)",
        "kind": "number",
        "min": 1,
        "max": 86400,
    },
    {
        "key": "decay_after_days",
        "label": "Decay after (unused days)",
        "kind": "number",
        "min": 1,
        "max": 3650,
    },
    {
        "key": "skills_dirs",
        "label": "Skill directories",
        "kind": "lines",
        "help": "Where your agents load skills from; accepted skills are mirrored here.",
    },
)

COMMAND_KEYS = {"agent.miner.cmd", "agent.writer.cmd"}
KEYS = tuple(spec["key"] for spec in FIELDS)


def fields(cfg: dict) -> list[dict]:
    """The panel's view of the editable fields (lines are newline-joined)."""
    out: list[dict] = []
    for spec in FIELDS:
        value = _get(cfg, spec["key"])
        item = {k: v for k, v in spec.items()}
        if spec["kind"] == "lines":
            item["value"] = "\n".join(str(part) for part in (value or []))
        else:
            item["value"] = value
        out.append(item)
    return out


def validate(values: dict, check_binaries: bool = True) -> dict:
    """Validate and normalize submitted fields. Raises PoppyError on the first problem."""
    if not isinstance(values, dict):
        raise PoppyError("settings must be an object")
    unknown = sorted(set(values) - set(KEYS))
    if unknown:
        raise PoppyError(f"unknown setting(s): {', '.join(unknown)}")
    normalized: dict = {}
    for spec in FIELDS:
        key = spec["key"]
        if key not in values:
            continue
        raw = values[key]
        if spec["kind"] == "lines":
            items = _lines(raw, spec["label"])
            if key in COMMAND_KEYS:
                if not items:
                    raise PoppyError(f"{spec['label']}: needs at least the program to run")
                if check_binaries:
                    _require_executable(items[0], spec["label"])
            normalized[key] = items
        else:
            normalized[key] = _number(raw, spec)
    return normalized


def apply(cfg: dict, normalized: dict) -> dict:
    """Apply validated values to cfg in place."""
    for key, value in normalized.items():
        set_dotted(cfg, key, value)
    return cfg


def _get(cfg: dict, key: str):
    node = cfg
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _lines(raw, label: str) -> list[str]:
    if isinstance(raw, str):
        parts: list = raw.splitlines()
    elif isinstance(raw, list):
        parts = raw
    else:
        raise PoppyError(f"{label}: expected one item per line")
    items = [str(part).strip() for part in parts]
    return [part for part in items if part]


def _number(raw, spec: dict) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise PoppyError(f"{spec['label']}: expected a whole number") from None
    low, high = spec.get("min"), spec.get("max")
    if low is not None and value < low:
        raise PoppyError(f"{spec['label']}: must be at least {low}")
    if high is not None and value > high:
        raise PoppyError(f"{spec['label']}: must be at most {high}")
    return value


def _require_executable(token: str, label: str) -> None:
    if "/" in token:
        path = Path(token).expanduser()
        if not (path.is_file() and os.access(path, os.X_OK)):
            raise PoppyError(f"{label}: not an executable file: {token}")
        return
    if shutil.which(token) is None:
        raise PoppyError(f"{label}: command not found on PATH: {token}")
