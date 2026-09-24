"""Environment and installation checks (`poppy doctor`)."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from .agent import test_agent
from .candidates import list_candidates
from .config import config_path, load_config
from .schedule import status as schedule_status
from .sources import load_sources
from .util import PoppyError


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
    for entry in dirs:
        path = Path(entry).expanduser()
        if path.is_dir():
            checks.append(Check(f"skills_dir:{entry}", "ok", "exists"))
        elif path.parent.is_dir():
            checks.append(Check(f"skills_dir:{entry}", "warn", "missing; created on first install"))
        else:
            checks.append(Check(f"skills_dir:{entry}", "fail", "parent directory does not exist"))

    for role in ("miner", "writer"):
        cmd = ((cfg.get("agent") or {}).get(role) or {}).get("cmd")
        checks.append(
            Check(f"agent.{role}", "ok" if cmd else "fail", "configured" if cmd else "no command configured")
        )
    if with_agent:
        for role in ("miner", "writer"):
            ok, detail = test_agent(role, cfg, home)
            checks.append(Check(f"agent.{role}.test", "ok" if ok else "fail", detail))

    status = schedule_status(home)
    checks.append(
        Check("schedule", "ok" if status.get("installed") else "warn", status.get("detail", ""))
    )

    counts: dict[str, int] = {}
    for candidate in list_candidates(home):
        status_name = str(candidate.get("status", "?"))
        counts[status_name] = counts.get(status_name, 0) + 1
    if counts:
        checks.append(Check("queue", "ok", ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))))
    return checks
