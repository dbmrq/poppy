"""Shared helpers. Standard library only."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"
REPO_ROOT = PACKAGE_DIR.parents[1]  # the checkout root when running from a source tree

# Convenience locations for user-installed tools, appended to PATH for
# scheduled runs and agent subprocesses. Not a support matrix: any directory
# already on PATH keeps its precedence, and missing entries are harmless.
COMMON_BIN_DIRS = (
    "~/.local/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/home/linuxbrew/.linuxbrew/bin",
)
FALLBACK_BIN_DIRS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


def _installed_console_script() -> str | None:
    """The `poppy` script on PATH, but only when it belongs to this install."""
    script = shutil.which("poppy")
    if not script:
        return None
    try:
        if Path(script).resolve().parent == Path(sys.executable).parent:
            return script
    except OSError:
        return None
    return None


def launch_command() -> list[str]:
    """argv prefix for scheduled runs (systemd, launchd, cron).

    A source checkout runs its ``bin/poppy``. An installed copy prefers the
    console script on PATH when it demonstrably belongs to this install: pipx
    and Homebrew keep that path stable across upgrades, while the interpreter
    path carries a version (``Cellar/poppy-ai/0.2.0/...``) that moves on every
    upgrade and would leave the scheduler pointing at a deleted interpreter.
    """
    bin_path = REPO_ROOT / "bin" / "poppy"
    if bin_path.is_file():
        return [sys.executable, str(bin_path)]
    script = _installed_console_script()
    if script:
        return [script]
    return [sys.executable, "-m", "poppy"]


def launch_command_str() -> str:
    return " ".join(shlex.quote(part) for part in launch_command())


def _path_entries(value: str | None) -> list[str]:
    return [part for part in str(value or "").split(os.pathsep) if part]


def extended_path(current: str | None = None, extra: str | None = None) -> str:
    """A PATH that keeps its precedence but also carries user tool directories.

    Scheduled runs (launchd, systemd, cron) start with a much smaller
    environment than the shell the installer verified the agent command in, so
    a command that works interactively can be missing from the timer. This
    appends the stored timer PATH (``extra``) and common user bin directories
    to ``current`` (``os.environ`` by default), preserving the order and
    precedence of entries that already exist.
    """
    base = _path_entries(os.environ.get("PATH") if current is None else current)
    candidates: list[str] = [*_path_entries(extra)]
    candidates.extend(str(Path(raw).expanduser()) for raw in COMMON_BIN_DIRS)
    candidates.extend(FALLBACK_BIN_DIRS)
    entries: list[str] = []
    for entry in [*base, *candidates]:
        if entry and entry not in entries:
            entries.append(entry)
    return os.pathsep.join(entries)


def redact_userinfo(text: str | None) -> str:
    """Strip credentials embedded in URLs before text is logged or stored."""
    return re.sub(r"(://)[^/\s@]*@", r"\1***@", text or "")


class PoppyError(Exception):
    """Expected, user-facing error."""


def poppy_home() -> Path:
    env = os.environ.get("POPPY_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".poppy"


HOME_SUBDIRS = ("inbox", "candidates", "drafts", "rejected", "logs", "locks")


def ensure_home_layout(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    for sub in HOME_SUBDIRS:
        (home / sub).mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        raise PoppyError(f"invalid JSON in {path}: {exc}") from exc


def save_json(path: Path, data, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_duration(value: str) -> float:
    """'14d', '36h', '90m', '3600' -> seconds."""
    text = str(value).strip().lower()
    if text.isdigit():
        return float(text)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhdw])", text)
    if not m:
        raise PoppyError(f"cannot parse duration: {value!r} (use e.g. 14d, 36h, 90m)")
    amount = float(m.group(1))
    unit = m.group(2)
    factor = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    return amount * factor


def norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def sha(text: str, n: int = 12) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:n]


def tail(text: str | None, limit: int = 2000) -> str:
    text = text or ""
    return text if len(text) <= limit else "…" + text[-limit:]


SECRET_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("high", "private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("high", "OpenAI-style API key", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}\b")),
    ("high", "GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs)_[A-Za-z0-9]{20,}\b")),
    ("high", "GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("high", "AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("high", "Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("high", "JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    (
        "low",
        "secret-like assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*['\"]?[A-Za-z0-9_+/.-]{16,}"
        ),
    ),
]


def scan_secrets(*texts: str) -> list[tuple[str, str, str]]:
    """Return (severity, label, matched_snippet) findings across the given texts."""
    findings: list[tuple[str, str, str]] = []
    for text in texts:
        text = text or ""
        for severity, label, pattern in SECRET_PATTERNS:
            m = pattern.search(text)
            if m:
                snippet = m.group(0)
                findings.append((severity, label, snippet[:80]))
    return findings


def acquire_lock(home: Path, name: str, stale_after: float = 6 * 3600) -> Path:
    """Create an exclusive lock file. Stale locks (crashed runs) are reclaimed."""
    lock_dir = home / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / f"{name}.lock"
    if path.exists():
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            age = 0
        if age > stale_after:
            path.unlink(missing_ok=True)
        else:
            raise PoppyError(f"another poppy run holds the lock ({path}); remove it if that run is gone")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return path


def release_lock(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def temp_text_file(prefix: str, text: str) -> Path:
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return Path(name)


def human_time(epoch: float | None) -> str:
    if not epoch:
        return "-"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def iso_to_epoch(value: str | None) -> float | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def human_size(chars: int) -> str:
    if chars < 1024:
        return f"{chars}B"
    if chars < 1024 * 1024:
        return f"{chars / 1024:.0f}K"
    return f"{chars / (1024 * 1024):.1f}M"
