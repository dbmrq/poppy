"""Scheduling: systemd user timer, launchd agent, or a cron line to install manually."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .util import PoppyError, atomic_write_text

LABEL = "poppy-mine"
BIN_PATH = Path(__file__).resolve().parents[2] / "bin" / "poppy"

SERVICE = """[Unit]
Description=Poppy: mine recent agent sessions for reusable skills

[Service]
Type=oneshot
ExecStart={python} {bin} mine --quiet
Environment=POPPY_HOME={home}
Nice=10
"""

TIMER = """[Unit]
Description=Weekly Poppy mining run

[Timer]
OnCalendar=Mon 09:00
RandomizedDelaySec=1800
Persistent=true

[Install]
WantedBy=timers.target
"""

PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.poppy.mine</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>{bin}</string>
    <string>mine</string>
    <string>--quiet</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>POPPY_HOME</key><string>{home}</string></dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key><integer>1</integer>
    <key>Hour</key><integer>9</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def _systemd_user_available() -> bool:
    if not shutil.which("systemctl"):
        return False
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "show-environment"], capture_output=True, timeout=10
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def platform_kind() -> str:
    if sys.platform == "darwin":
        return "launchd"
    if _systemd_user_available():
        return "systemd"
    return "cron"


def systemd_paths() -> tuple[Path, Path]:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    return unit_dir / f"{LABEL}.service", unit_dir / f"{LABEL}.timer"


def launchd_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.poppy.mine.plist"


def cron_line(home: Path) -> str:
    return f"0 9 * * 1 {sys.executable} {BIN_PATH} mine --quiet  # poppy: weekly skills mining"


def install(home: Path, dry_run: bool = False) -> dict:
    kind = platform_kind()
    if kind == "systemd":
        service_path, timer_path = systemd_paths()
        service_text = SERVICE.format(python=sys.executable, bin=BIN_PATH, home=home)
        if dry_run:
            return {
                "kind": kind,
                "enabled": False,
                "files": {str(service_path): service_text, str(timer_path): TIMER},
            }
        service_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(service_path, service_text)
        atomic_write_text(timer_path, TIMER)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", f"{LABEL}.timer"], check=True)
        return {"kind": kind, "enabled": True, "files": {str(service_path): "written", str(timer_path): "written"}}
    if kind == "launchd":
        path = launchd_path()
        content = PLIST.format(
            python=sys.executable, bin=BIN_PATH, home=home, log=home / "logs" / "scheduled.log"
        )
        if dry_run:
            return {"kind": kind, "enabled": False, "files": {str(path): content}}
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, content)
        subprocess.run(["launchctl", "load", "-w", str(path)], check=False)
        return {"kind": kind, "enabled": True, "files": {str(path): "written"}}
    line = cron_line(home)
    if dry_run:
        return {"kind": "cron", "enabled": False, "cron_line": line}
    raise PoppyError(f"no supported scheduler found; add this line with `crontab -e`:\n  {line}")


def status(home: Path) -> dict:
    kind = platform_kind()
    if kind == "systemd":
        service_path, timer_path = systemd_paths()
        if not timer_path.exists():
            return {"installed": False, "kind": kind, "detail": f"no timer at {timer_path}"}
        try:
            proc = subprocess.run(
                ["systemctl", "--user", "is-active", f"{LABEL}.timer"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            active = proc.stdout.strip() == "active"
            return {
                "installed": True,
                "kind": kind,
                "detail": f"timer {'active' if active else 'inactive'} ({timer_path})",
            }
        except (OSError, subprocess.TimeoutExpired):
            return {"installed": True, "kind": kind, "detail": str(timer_path)}
    if kind == "launchd":
        path = launchd_path()
        if path.exists():
            return {"installed": True, "kind": kind, "detail": str(path)}
        return {"installed": False, "kind": kind, "detail": f"no plist at {path}"}
    return {"installed": False, "kind": "cron", "detail": f"add manually: {cron_line(home)}"}


def uninstall(home: Path) -> dict:
    kind = platform_kind()
    if kind == "systemd":
        service_path, timer_path = systemd_paths()
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{LABEL}.timer"], check=False)
        removed = []
        for path in (service_path, timer_path):
            if path.exists():
                path.unlink()
                removed.append(str(path))
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        return {"kind": kind, "removed": removed}
    if kind == "launchd":
        path = launchd_path()
        if path.exists():
            subprocess.run(["launchctl", "unload", "-w", str(path)], check=False)
            path.unlink()
            return {"kind": kind, "removed": [str(path)]}
        return {"kind": kind, "removed": []}
    return {"kind": "cron", "removed": [], "detail": f"remove manually: {cron_line(home)}"}
