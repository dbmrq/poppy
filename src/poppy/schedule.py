"""Scheduling: systemd user timers, launchd agents, or cron lines to install manually."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .util import PoppyError, atomic_write_text

MINE_LABEL = "poppy-mine"
SYNC_LABEL = "poppy-sync"
BIN_PATH = Path(__file__).resolve().parents[2] / "bin" / "poppy"

MINE_SERVICE = """[Unit]
Description=Poppy: mine recent agent sessions for reusable skills

[Service]
Type=oneshot
ExecStart={python} {bin} mine --quiet
Environment=POPPY_HOME={home}
Nice=10
"""

MINE_TIMER = """[Unit]
Description=Weekly Poppy mining run

[Timer]
OnCalendar=Mon 09:00
RandomizedDelaySec=1800
Persistent=true

[Install]
WantedBy=timers.target
"""

SYNC_SERVICE = """[Unit]
Description=Poppy: sync the library with its private git remote

[Service]
Type=oneshot
ExecStart={python} {bin} sync run --quiet
Environment=POPPY_HOME={home}
SuccessExitStatus=1
Nice=10
"""

SYNC_TIMER = """[Unit]
Description=Frequent Poppy sync (every {interval} min)

[Timer]
OnBootSec=5min
OnUnitActiveSec={interval}min
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
"""

MINE_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
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

SYNC_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.poppy.sync</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>{bin}</string>
    <string>sync</string>
    <string>run</string>
    <string>--quiet</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>POPPY_HOME</key><string>{home}</string></dict>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>{interval_sec}</integer>
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


def sync_interval_minutes(cfg: dict) -> int:
    raw = (cfg.get("sync") or {}).get("interval_min", 30)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 30
    return max(5, value)


def systemd_paths(label: str) -> tuple[Path, Path]:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    return unit_dir / f"{label}.service", unit_dir / f"{label}.timer"


def launchd_path(name: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"com.poppy.{name}.plist"


def cron_line(home: Path) -> str:
    return f"0 9 * * 1 {sys.executable} {BIN_PATH} mine --quiet  # poppy: weekly skills mining"


def sync_cron_line(home: Path, cfg: dict) -> str:
    interval = sync_interval_minutes(cfg)
    return f"*/{interval} * * * * {sys.executable} {BIN_PATH} sync run --quiet  # poppy: library sync"


def install(home: Path, cfg: dict, dry_run: bool = False) -> dict:
    kind = platform_kind()
    sync_on = bool((cfg.get("sync") or {}).get("enabled"))
    interval = sync_interval_minutes(cfg)
    if kind == "systemd":
        service_path, timer_path = systemd_paths(MINE_LABEL)
        service_text = MINE_SERVICE.format(python=sys.executable, bin=BIN_PATH, home=home)
        sync_service_path, sync_timer_path = systemd_paths(SYNC_LABEL)
        sync_service_text = SYNC_SERVICE.format(python=sys.executable, bin=BIN_PATH, home=home)
        sync_timer_text = SYNC_TIMER.format(interval=interval)
        files = {str(service_path): service_text, str(timer_path): MINE_TIMER}
        if sync_on:
            files[str(sync_service_path)] = sync_service_text
            files[str(sync_timer_path)] = sync_timer_text
        if dry_run:
            return {"kind": kind, "enabled": False, "sync_enabled": sync_on, "files": files}
        for path_text, content in files.items():
            atomic_write_text(Path(path_text), content)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", f"{MINE_LABEL}.timer"], check=True)
        if sync_on:
            subprocess.run(
                ["systemctl", "--user", "enable", "--now", f"{SYNC_LABEL}.timer"], check=True
            )
        return {
            "kind": kind,
            "enabled": True,
            "sync_enabled": sync_on,
            "files": {path: "written" for path in files},
        }
    if kind == "launchd":
        path = launchd_path("mine")
        content = MINE_PLIST.format(
            python=sys.executable, bin=BIN_PATH, home=home, log=home / "logs" / "scheduled.log"
        )
        files = {str(path): content}
        if sync_on:
            sync_path = launchd_path("sync")
            files[str(sync_path)] = SYNC_PLIST.format(
                python=sys.executable,
                bin=BIN_PATH,
                home=home,
                interval_sec=interval * 60,
                log=home / "logs" / "sync.log",
            )
        if dry_run:
            return {"kind": kind, "enabled": False, "sync_enabled": sync_on, "files": files}
        for path_text, plist in files.items():
            target = Path(path_text)
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(target, plist)
            subprocess.run(["launchctl", "load", "-w", str(target)], check=False)
        return {"kind": kind, "enabled": True, "sync_enabled": sync_on, "files": {p: "written" for p in files}}
    lines = [cron_line(home)]
    if sync_on:
        lines.append(sync_cron_line(home, cfg))
    if dry_run:
        return {"kind": "cron", "enabled": False, "sync_enabled": sync_on, "files": {}, "cron_lines": lines}
    raise PoppyError(
        "no supported scheduler found; add these lines with `crontab -e`:\n  " + "\n  ".join(lines)
    )


def _systemd_state(label: str) -> dict:
    service_path, timer_path = systemd_paths(label)
    if not timer_path.exists():
        return {"installed": False, "detail": f"no timer at {timer_path}"}
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-active", f"{label}.timer"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        active = proc.stdout.strip() == "active"
        return {"installed": True, "detail": f"timer {'active' if active else 'inactive'} ({timer_path})"}
    except (OSError, subprocess.TimeoutExpired):
        return {"installed": True, "detail": str(timer_path)}


def status(home: Path, cfg: dict) -> dict:
    kind = platform_kind()
    sync_on = bool((cfg.get("sync") or {}).get("enabled"))
    if kind == "systemd":
        out = {"kind": kind, **_systemd_state(MINE_LABEL)}
        if sync_on:
            out["sync"] = _systemd_state(SYNC_LABEL)
        else:
            out["sync"] = {"installed": False, "detail": "sync not enabled"}
        return out
    if kind == "launchd":
        path = launchd_path("mine")
        out = {
            "kind": kind,
            "installed": path.exists(),
            "detail": str(path) if path.exists() else f"no plist at {path}",
        }
        sync_path = launchd_path("sync")
        if sync_on:
            out["sync"] = {
                "installed": sync_path.exists(),
                "detail": str(sync_path) if sync_path.exists() else f"no plist at {sync_path}",
            }
        else:
            out["sync"] = {"installed": False, "detail": "sync not enabled"}
        return out
    out = {"kind": "cron", "installed": False, "detail": f"add manually: {cron_line(home)}"}
    out["sync"] = (
        {"installed": False, "detail": f"add manually: {sync_cron_line(home, cfg)}"}
        if sync_on
        else {"installed": False, "detail": "sync not enabled"}
    )
    return out


def uninstall(home: Path) -> dict:
    kind = platform_kind()
    if kind == "systemd":
        removed = []
        for label in (MINE_LABEL, SYNC_LABEL):
            subprocess.run(["systemctl", "--user", "disable", "--now", f"{label}.timer"], check=False)
            for path in systemd_paths(label):
                if path.exists():
                    path.unlink()
                    removed.append(str(path))
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        return {"kind": kind, "removed": removed}
    if kind == "launchd":
        removed = []
        for name in ("mine", "sync"):
            path = launchd_path(name)
            if path.exists():
                subprocess.run(["launchctl", "unload", "-w", str(path)], check=False)
                path.unlink()
                removed.append(str(path))
        return {"kind": kind, "removed": removed}
    return {
        "kind": "cron",
        "removed": [],
        "detail": f"remove manually: {cron_line(home)}",
    }
