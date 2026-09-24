"""Update an installed Poppy, whichever way it was installed.

`poppy update` is for agents and humans alike: it detects pipx, a plain pip
install, or a source checkout, runs the right command, then refreshes the
builtin skills with the new code (so the agent-facing skills track the CLI).
`poppy update --check` reports whether an update is available without changing
anything.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__
from .util import PACKAGE_DIR, PoppyError, REPO_ROOT, launch_command, tail

DIST_NAME = "poppy-ai"
UPDATE_TIMEOUT = 300
PYPI_URL = f"https://pypi.org/pypi/{DIST_NAME}/json"


def install_mode() -> str:
    """One of: pipx, checkout, pip."""
    if "pipx" in str(Path(PACKAGE_DIR)):
        return "pipx"
    if (REPO_ROOT / "bin" / "poppy").is_file() and (REPO_ROOT / ".git").is_dir():
        return "checkout"
    return "pip"


def _run(args: list[str], timeout: int = UPDATE_TIMEOUT) -> tuple[int, str]:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise PoppyError(f"command not found: {args[0]}")
    except subprocess.TimeoutExpired:
        raise PoppyError(f"{' '.join(args)} timed out after {timeout}s")
    return proc.returncode, tail((proc.stdout + proc.stderr).strip(), 500)


def _refresh() -> tuple[bool, str]:
    """Re-run `poppy init` with the updated code to refresh builtins and mirrors."""
    try:
        proc = subprocess.run(
            [*launch_command(), "init"], capture_output=True, text=True, timeout=120
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, tail((proc.stdout + proc.stderr).strip(), 300)
    return True, ""


def version() -> str:
    """The version the updated install reports (queried fresh)."""
    try:
        proc = subprocess.run(
            [*launch_command(), "--version"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired):
        return __version__
    return (proc.stdout or proc.stderr).strip() or "unknown"


def _fetch_json(url: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def _latest_from_pypi() -> tuple[str | None, str]:
    try:
        data = _fetch_json(PYPI_URL)
        return str(data["info"]["version"]), ""
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
        return None, f"could not reach PyPI: {exc}"


def _checkout_behind() -> tuple[int | None, str]:
    code, detail = _run(["git", "-C", str(REPO_ROOT), "fetch", "--quiet"], timeout=120)
    if code != 0:
        return None, f"git fetch failed: {detail}"
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-list", "--left-right", "--count", "HEAD...@{u}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if proc.returncode != 0:
        return None, "no upstream branch to compare with"
    parts = proc.stdout.strip().split()
    if len(parts) != 2:
        return None, "could not compare with the upstream branch"
    return int(parts[0]), ""


def _version_key(text: str) -> tuple:
    """Order versions numerically per component ('0.10.0' > '0.9.0').

    A trailing terminator makes a plain release sort above its pre-releases
    ('1.0.0' > '1.0.0-rc1').
    """
    key: list[tuple[int, object]] = []
    for chunk in re.split(r"[.\-+_]", str(text).strip()):
        if chunk.isdigit():
            key.append((0, int(chunk)))
        elif chunk:
            key.append((1, chunk))
    key.append((2, ""))
    return tuple(key)


def check(home: Path, cfg: dict) -> dict:
    """Report whether an update is available, without changing anything."""
    mode = install_mode()
    result: dict = {
        "mode": mode,
        "installed": __version__,
        "latest": None,
        "behind": None,
        "update_available": None,
        "detail": "",
    }
    if mode == "checkout":
        behind, detail = _checkout_behind()
        result["behind"] = behind
        result["detail"] = detail
        if behind is not None:
            result["update_available"] = behind > 0
        return result
    latest, detail = _latest_from_pypi()
    result["latest"] = latest
    result["detail"] = detail
    if latest:
        result["update_available"] = _version_key(latest) > _version_key(__version__)
    return result


def update(home: Path, cfg: dict) -> dict:
    """Run the install-appropriate update command. Returns a result dict."""
    mode = install_mode()
    if mode == "pipx":
        command = ["pipx", "upgrade", DIST_NAME]
    elif mode == "checkout":
        command = ["git", "-C", str(REPO_ROOT), "pull", "--rebase", "--autostash"]
    else:
        raise PoppyError(
            "this install is not managed by pipx or a git checkout — update it with your "
            "package manager, for example: python3 -m pip install --upgrade "
            "git+https://github.com/dbmrq/poppy.git"
        )

    code, detail = _run(command)
    if code != 0:
        raise PoppyError(f"{' '.join(command)} failed: {detail}")
    refreshed, refresh_error = _refresh()
    return {
        "mode": mode,
        "command": " ".join(command),
        "detail": detail,
        "refreshed": refreshed,
        "refresh_error": refresh_error,
        "version": version(),
    }
