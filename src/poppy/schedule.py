"""Scheduling: systemd user timers, launchd agents, or cron lines to install manually."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from .util import PoppyError, atomic_write_text, launch_command, launch_command_str

MINE_LABEL = "poppy-mine"
SYNC_LABEL = "poppy-sync"

MINE_SERVICE = """[Unit]
Description=Poppy: mine recent agent sessions for reusable skills

[Service]
Type=oneshot
ExecStart={cmd} mine --quiet
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
ExecStart={cmd} sync run --quiet
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

PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
{program_arguments}  </array>
  <key>EnvironmentVariables</key>
  <dict><key>POPPY_HOME</key><string>{home}</string><key>HOME</key><string>{user_home}</string></dict>
{schedule}  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""

MINE_CALENDAR = """  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key><integer>1</integer>
    <key>Hour</key><integer>9</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
"""

SYNC_INTERVAL = """  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>{interval_sec}</integer>
"""


def _plist(label: str, args: list[str], home: Path, log: Path, schedule_xml: str) -> str:
    program_arguments = "".join(f"    <string>{xml_escape(part)}</string>\n" for part in args)
    return PLIST_TEMPLATE.format(
        label=label,
        program_arguments=program_arguments,
        home=xml_escape(str(home)),
        user_home=xml_escape(str(Path.home())),
        schedule=schedule_xml,
        log=xml_escape(str(log)),
    )


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


def _systemctl(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(
            ["systemctl", "--user", *args], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PoppyError(f"systemctl --user {' '.join(args)} failed: {exc}")
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise PoppyError(f"systemctl --user {' '.join(args)} failed: {detail}")
    return proc


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
    return f"0 9 * * 1 {launch_command_str()} mine --quiet  # poppy: weekly skills mining"


def sync_cron_line(home: Path, cfg: dict) -> str:
    interval = sync_interval_minutes(cfg)
    return f"*/{interval} * * * * {launch_command_str()} sync run --quiet  # poppy: library sync"


def install(
    home: Path,
    cfg: dict,
    dry_run: bool = False,
    include_mine: bool = True,
    include_sync: bool | None = None,
) -> dict:
    """Install/refresh the timers. ``include_sync`` defaults to sync.enabled + sync.schedule."""
    kind = platform_kind()
    if include_sync is None:
        sync_cfg = cfg.get("sync") or {}
        include_sync = bool(sync_cfg.get("enabled")) and bool(sync_cfg.get("schedule", True))
    include_sync = bool(include_sync)
    interval = sync_interval_minutes(cfg)
    if kind == "systemd":
        files: dict[str, str] = {}
        if include_mine:
            service_path, timer_path = systemd_paths(MINE_LABEL)
            files[str(service_path)] = MINE_SERVICE.format(cmd=launch_command_str(), home=home)
            files[str(timer_path)] = MINE_TIMER
        if include_sync:
            sync_service_path, sync_timer_path = systemd_paths(SYNC_LABEL)
            files[str(sync_service_path)] = SYNC_SERVICE.format(cmd=launch_command_str(), home=home)
            files[str(sync_timer_path)] = SYNC_TIMER.format(interval=interval)
        if not files:
            raise PoppyError("nothing to install: mining and sync schedules are both disabled")
        if dry_run:
            return {"kind": kind, "enabled": False, "sync_enabled": include_sync, "files": files}
        for path_text, content in files.items():
            atomic_write_text(Path(path_text), content)
        _systemctl(["daemon-reload"])
        if include_mine:
            _systemctl(["enable", "--now", f"{MINE_LABEL}.timer"])
        if include_sync:
            _systemctl(["enable", "--now", f"{SYNC_LABEL}.timer"])
        return {
            "kind": kind,
            "enabled": include_mine,
            "sync_enabled": include_sync,
            "files": {path: "written" for path in files},
        }
    if kind == "launchd":
        files = {}
        if include_mine:
            files[str(launchd_path("mine"))] = _plist(
                "com.poppy.mine",
                [*launch_command(), "mine", "--quiet"],
                home,
                home / "logs" / "scheduled.log",
                MINE_CALENDAR,
            )
        if include_sync:
            files[str(launchd_path("sync"))] = _plist(
                "com.poppy.sync",
                [*launch_command(), "sync", "run", "--quiet"],
                home,
                home / "logs" / "sync.log",
                SYNC_INTERVAL.format(interval_sec=interval * 60),
            )
        if not files:
            raise PoppyError("nothing to install: mining and sync schedules are both disabled")
        if dry_run:
            return {"kind": kind, "enabled": False, "sync_enabled": include_sync, "files": files}
        for path_text, plist in files.items():
            target = Path(path_text)
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(target, plist)
            subprocess.run(
                ["launchctl", "load", "-w", str(target)], check=False, capture_output=True
            )
        return {
            "kind": kind,
            "enabled": include_mine,
            "sync_enabled": include_sync,
            "files": {path: "written" for path in files},
        }
    lines = []
    if include_mine:
        lines.append(cron_line(home))
    if include_sync:
        lines.append(sync_cron_line(home, cfg))
    if not lines:
        raise PoppyError("nothing to install: mining and sync schedules are both disabled")
    if dry_run:
        return {"kind": "cron", "enabled": False, "sync_enabled": include_sync, "files": {}, "cron_lines": lines}
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
    sync_cfg = cfg.get("sync") or {}
    sync_on = bool(sync_cfg.get("enabled")) and bool(sync_cfg.get("schedule", True))
    if kind == "systemd":
        out = {"kind": kind, **_systemd_state(MINE_LABEL)}
        if sync_on:
            out["sync"] = _systemd_state(SYNC_LABEL)
        else:
            out["sync"] = {"installed": False, "detail": "sync scheduling not enabled"}
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
            out["sync"] = {"installed": False, "detail": "sync scheduling not enabled"}
        return out
    out = {"kind": "cron", "installed": False, "detail": f"add manually: {cron_line(home)}"}
    out["sync"] = (
        {"installed": False, "detail": f"add manually: {sync_cron_line(home, cfg)}"}
        if sync_on
        else {"installed": False, "detail": "sync scheduling not enabled"}
    )
    return out


def uninstall(home: Path) -> dict:
    kind = platform_kind()
    if kind == "systemd":
        removed = []
        for label in (MINE_LABEL, SYNC_LABEL):
            _systemctl(["disable", "--now", f"{label}.timer"], check=False)
            for path in systemd_paths(label):
                if path.exists():
                    path.unlink()
                    removed.append(str(path))
        _systemctl(["daemon-reload"], check=False)
        return {"kind": kind, "removed": removed}
    if kind == "launchd":
        removed = []
        for name in ("mine", "sync"):
            path = launchd_path(name)
            if path.exists():
                subprocess.run(["launchctl", "unload", "-w", str(path)], check=False, capture_output=True)
                path.unlink()
                removed.append(str(path))
        return {"kind": kind, "removed": removed}
    return {
        "kind": "cron",
        "removed": [],
        "detail": f"remove manually: {cron_line(home)}",
    }
