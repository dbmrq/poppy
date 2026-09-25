"""Environment and installation checks (`poppy doctor`)."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from . import library
from .agent import test_agent
from .candidates import list_candidates
from .config import config_path, load_config
from .schedule import scheduled_path
from .schedule import status as schedule_status
from .skills import load_manifest, skills_dir_paths
from .sources import load_sources
from .sync import status as sync_status
from .update import install_mode
from .util import PoppyError, tail


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str = ""


def run_checks(home: Path, with_agent: bool = False) -> list[Check]:
    checks: list[Check] = []
    checks.append(
        Check("python", "ok" if sys.version_info >= (3, 10) else "fail", sys.version.split()[0])
    )
    mode = install_mode()
    label = {
        "pipx": "pipx install",
        "brew": "Homebrew",
        "checkout": "source checkout",
        "pip": "pip install",
    }[mode]
    checks.append(Check("install", "ok", f"{label} · poppy {__version__}"))

    if not config_path(home).exists():
        checks.append(Check("config", "fail", f"missing {config_path(home)} — run `poppy init`"))
        return checks
    try:
        cfg = load_config(home)
    except PoppyError as exc:
        checks.append(Check("config", "fail", str(exc)))
        return checks
    checks.append(Check("config", "ok", str(config_path(home))))

    sources = []
    try:
        sources = load_sources(home)
    except PoppyError as exc:
        checks.append(Check("sources", "fail", str(exc)))
    if not sources:
        checks.append(Check("sources", "fail", "no sources configured — the miner has nothing to read"))
    for source in sources:
        try:
            recent = source.list_sessions(limit=1)
            detail = f"{source.type}"
            if recent:
                detail += f"; newest: {recent[0].id}"
            checks.append(Check(f"source:{source.name}", "ok", detail))
        except PoppyError as exc:
            checks.append(Check(f"source:{source.name}", "fail", str(exc)))

    dirs = cfg.get("skills_dirs") or []
    if not dirs:
        checks.append(Check("skills_dirs", "fail", "none configured — nothing could be installed"))
    for entry in skills_dir_paths(cfg):
        path = Path(entry).expanduser()
        label = f"skills_dir:{entry}"
        if path.is_dir():
            checks.append(Check(label, "ok", "exists"))
        elif path.parent.is_dir():
            checks.append(Check(label, "warn", "missing; created on first install"))
        else:
            checks.append(Check(label, "fail", "parent directory does not exist"))

    try:
        entries = library.list_entries(home)
        kinds: dict[str, int] = {}
        for entry in entries:
            kinds[entry.kind] = kinds.get(entry.kind, 0) + 1
        checks.append(
            Check(
                "library",
                "ok",
                ", ".join(f"{count} {kind}(s)" for kind, count in sorted(kinds.items())) or "empty",
            )
        )
        if "poppy-context" not in {entry.id for entry in entries}:
            checks.append(Check("builtin:poppy-context", "warn", "not in library — run `poppy init` to install it"))
        if library.BUILTIN_DIR.is_dir():
            builtin_names = [path.parent.name for path in sorted(library.BUILTIN_DIR.glob("*/SKILL.md"))]
            present = {entry.id for entry in entries}
            missing = [name for name in builtin_names if name not in present]
            if missing:
                checks.append(Check("builtins", "warn", f"missing: {', '.join(missing)} — run `poppy init`"))
    except PoppyError as exc:
        checks.append(Check("library", "fail", str(exc)))

    manifest = load_manifest(home)
    if manifest.get("skills"):
        missing_library = [
            name for name, entry in manifest["skills"].items() if not entry.get("library")
        ]
        if missing_library:
            checks.append(
                Check(
                    "mirrors",
                    "warn",
                    f"{len(missing_library)} skill(s) predate the library; run `poppy library adopt`",
                )
            )

    for role in ("miner", "writer"):
        cmd = ((cfg.get("agent") or {}).get(role) or {}).get("cmd")
        checks.append(
            Check(f"agent.{role}", "ok" if cmd else "fail", "configured" if cmd else "no command configured")
        )
    if with_agent:
        for role in ("miner", "writer"):
            ok, detail = test_agent(role, cfg, home)
            checks.append(Check(f"agent.{role}.test", "ok" if ok else "fail", detail))

    status = schedule_status(home, cfg)
    cadence = status.get("mine_cadence")
    detail = status.get("detail", "")
    if cadence and status.get("installed"):
        detail = f"{detail} · miner: {cadence}"
    checks.append(Check("schedule", "ok" if status.get("installed") else "warn", detail))

    from . import mailer

    ready, email_detail = mailer.email_ready(cfg)
    if not (cfg.get("email") or {}).get("enabled") and not mailer.email_config(cfg).get("enabled"):
        checks.append(Check("email", "warn", "off (optional) — `poppy email set --enable`"))
    else:
        checks.append(Check("email", "ok" if ready else "fail", "configured" if ready else email_detail))
    if status.get("installed") and status.get("kind") in ("launchd", "systemd"):
        stored = scheduled_path(cfg)
        if not stored:
            checks.append(
                Check(
                    "schedule:env",
                    "warn",
                    "timer carries no PATH — re-run `poppy schedule install` so scheduled runs find your tools",
                )
            )
        else:
            missing = []
            for role in ("miner", "writer"):
                cmd = ((cfg.get("agent") or {}).get(role) or {}).get("cmd")
                if cmd and shutil.which(str(cmd[0]), path=stored) is None:
                    missing.append(f"{role} ({cmd[0]})")
            if missing:
                checks.append(
                    Check(
                        "schedule:env",
                        "fail",
                        "not resolvable under the timer's PATH: "
                        + ", ".join(missing)
                        + " — use an absolute path or re-run `poppy schedule install`",
                    )
                )
            else:
                checks.append(
                    Check("schedule:env", "ok", "agent commands resolve under the timer's PATH")
                )

    sync_cfg = cfg.get("sync") or {}
    if sync_cfg.get("enabled"):
        sync = sync_status(home, cfg)
        if not sync["initialized"]:
            checks.append(Check("sync", "fail", "enabled but no repo — run `poppy sync init`"))
        elif sync["rebase_in_progress"]:
            checks.append(
                Check("sync", "fail", f"rebase in progress — resolve manually in {sync['repo']}")
            )
        else:
            last_status = sync.get("last_status")
            bad_run = last_status in ("offline", "error", "conflict")
            bits = [f"remote {sync['remote'] or '(local only)'}"]
            if sync["dirty"]:
                bits.append("uncommitted changes")
            if sync["ahead"]:
                bits.append(f"ahead {sync['ahead']}")
            if sync["behind"]:
                bits.append(f"behind {sync['behind']}")
            if bad_run:
                bits.append(f"last sync {last_status}")
                if sync.get("last_error"):
                    bits.append(tail(sync["last_error"], 160))
            problem = sync["dirty"] or sync["ahead"] or sync["behind"] or bad_run
            checks.append(Check("sync", "warn" if problem else "ok", " · ".join(bits)))
            if sync.get("env_file_error"):
                checks.append(
                    Check(
                        "sync:env-file",
                        "warn",
                        f"sync.env_file: {sync['env_file_error']}",
                    )
                )
            probe = sync.get("scheduled_probe") or {}
            if probe.get("ok") is False and (status.get("sync") or {}).get("installed"):
                checks.append(
                    Check(
                        "sync:probe",
                        "warn",
                        f"the timer could not reach the remote (checked {probe.get('at')}): "
                        f"{probe.get('detail') or 'unknown error'}"
                        " — give the timer credentials with `sync.env_file` or fix them",
                    )
                )
        mirrors = sync.get("mirrors") or {}
        drift = (
            len(mirrors.get("not_mirrored") or [])
            + len(mirrors.get("drifted") or [])
            + len(mirrors.get("orphaned") or [])
        )
        if drift:
            checks.append(
                Check("sync:mirrors", "warn", f"{drift} skill(s) out of sync — run `poppy sync run`")
            )
        if sync_cfg.get("schedule", True) and not (status.get("sync") or {}).get("installed"):
            checks.append(
                Check("schedule:sync", "warn", "sync enabled but no sync timer — run `poppy schedule install`")
            )
    else:
        checks.append(Check("sync", "warn", "not configured (optional) — `poppy sync init --remote <url>`"))

    counts: dict[str, int] = {}
    for candidate in list_candidates(home):
        status_name = str(candidate.get("status", "?"))
        counts[status_name] = counts.get(status_name, 0) + 1
    if counts:
        checks.append(Check("queue", "ok", ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))))
    return checks
