"""Remove Poppy from a machine: schedule, mirrors, context wiring, and data.

Deliberately conservative: without ``--yes`` the command only prints a plan;
only mirrors Poppy manages are removed; and the CLI checkout is left in place
with instructions, since it may be the thing running the command.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from . import digest
from .schedule import uninstall as schedule_uninstall
from .skills import load_manifest, remove_mirrors
from .update import install_mode
from .util import PACKAGE_DIR, REPO_ROOT, PoppyError


def plan(home: Path, cfg: dict) -> dict:
    manifest = load_manifest(home).get("skills", {})
    return {
        "applied": False,
        "data": str(home),
        "mirrors": sorted(manifest),
        "wiring": [str(target.get("file")) for target in digest.list_wiring(home)],
        "schedule": "mining + sync timers (if installed)",
    }


def _cli_hint() -> dict:
    mode = install_mode()
    hint: dict = {"path": str(REPO_ROOT if mode == "checkout" else PACKAGE_DIR), "command": None}
    if mode == "pipx":
        hint["command"] = "pipx uninstall poppy-ai"
    elif mode == "brew":
        hint["command"] = "brew uninstall poppy-ai"
    elif mode == "checkout":
        hint["command"] = (
            f"rm -rf {REPO_ROOT}  # and remove the poppy symlink from your PATH "
            "(for example ~/.local/bin/poppy)"
        )
    else:
        hint["command"] = "pip uninstall poppy-ai"
    return hint


def purge(home: Path, cfg: dict, yes: bool = False, keep_data: bool = False) -> dict:
    """Remove Poppy's integration (and data, unless kept). Returns a result dict."""
    if not yes:
        return plan(home, cfg)

    result: dict = {
        "applied": True,
        "removed": {"schedule": [], "mirrors": [], "wiring": [], "data": None},
        "errors": [],
        "cli": _cli_hint(),
    }

    try:
        sched = schedule_uninstall(home)
        result["removed"]["schedule"] = sched.get("removed", [])
        if sched.get("detail"):
            result["errors"].append(f"schedule: {sched['detail']}")
    except Exception as exc:  # best-effort: keep going
        result["errors"].append(f"schedule: {exc}")

    manifest = load_manifest(home).get("skills", {})
    for name in sorted(manifest):
        try:
            result["removed"]["mirrors"].extend(remove_mirrors(home, name))
        except PoppyError as exc:
            result["errors"].append(f"mirror {name}: {exc}")

    for target in digest.list_wiring(home):
        path = Path(str(target.get("file", ""))).expanduser()
        if not path.is_file():
            continue
        try:
            digest.unwire(home, cfg, path)
            result["removed"]["wiring"].append(str(path))
        except PoppyError as exc:
            result["errors"].append(f"wiring {path}: {exc}")
    digest.clear_wiring(home)

    if keep_data:
        result["kept_data"] = str(home)
    elif not home.exists():
        result["errors"].append(f"data: {home} does not exist")
    elif REPO_ROOT == home or REPO_ROOT.is_relative_to(home):
        result["errors"].append(f"data: refusing to delete {home} — the running checkout lives inside it")
    else:
        try:
            shutil.rmtree(home)
            result["removed"]["data"] = str(home)
        except OSError as exc:
            result["errors"].append(f"data {home}: {exc}")

    return result
