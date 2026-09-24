"""Update an installed Poppy, whichever way it was installed.

`poppy update` is for agents and humans alike: it detects pipx, a plain pip
install, or a source checkout, runs the right command, then refreshes the
builtin skills with the new code (so the agent-facing skills track the CLI).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .util import PACKAGE_DIR, PoppyError, REPO_ROOT, launch_command, tail

DIST_NAME = "poppy-agent"
UPDATE_TIMEOUT = 300


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
        from . import __version__

        return __version__
    return (proc.stdout or proc.stderr).strip() or "unknown"


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
