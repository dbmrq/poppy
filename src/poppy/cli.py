"""Poppy command line interface."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .candidates import list_candidates, load_candidate
from .config import (
    get_dotted,
    init_config,
    load_config,
    parse_value,
    save_config,
    set_dotted,
)
from .doctor import run_checks
from .pipeline import accept, mine
from .sessions import collect_sessions, get_session, search_sessions
from .skills import install_draft, load_manifest, uninstall_skill
from .sources import build_source, load_sources, save_sources, sources_path
from .schedule import install as schedule_install
from .schedule import status as schedule_status
from .schedule import uninstall as schedule_uninstall
from .ui import serve
from .util import (
    PoppyError,
    REPO_ROOT,
    ensure_home_layout,
    human_size,
    human_time,
    load_json,
    parse_duration,
    poppy_home,
    tail,
)

SYMBOL = {"ok": "✓", "warn": "!", "fail": "✗"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="poppy", description="Turn coding-agent session history into reusable skills."
    )
    parser.add_argument("--version", action="version", version=f"poppy {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create ~/.poppy (config + empty sources)")
    p.add_argument("--force", action="store_true", help="overwrite an existing config")

    p = sub.add_parser("config", help="show or edit configuration")
    csub = p.add_subparsers(dest="config_command", required=True)
    csub.add_parser("show", help="print the merged configuration")
    g = csub.add_parser("get", help="print one config value")
    g.add_argument("key")
    s = csub.add_parser("set", help="set one config value (JSON value or plain string)")
    s.add_argument("key")
    s.add_argument("value")

    p = sub.add_parser("sources", help="manage transcript sources")
    ssub = p.add_subparsers(dest="sources_command", required=True)
    ssub.add_parser("list", help="list configured sources")
    t = ssub.add_parser("test", help="verify sources against real sessions")
    t.add_argument("name", nargs="?")
    a = ssub.add_parser("add", help="add a source from JSON")
    a.add_argument("--json", help="source definition as JSON")
    a.add_argument("--file", help="path to a JSON file with the source definition")
    r = ssub.add_parser("remove", help="remove a source by name")
    r.add_argument("name")

    p = sub.add_parser("sessions", help="transcript toolbox (used by miners and humans)")
    ssub = p.add_subparsers(dest="sessions_command", required=True)
    l = ssub.add_parser("list", help="list sessions")
    l.add_argument("--since", help="lookback window, e.g. 14d")
    l.add_argument("--source", help="comma-separated source names")
    l.add_argument("--limit", type=int, default=50)
    l.add_argument("--json", action="store_true")
    q = ssub.add_parser("search", help="regex search across sessions")
    q.add_argument("pattern")
    q.add_argument("--since", help="lookback window, e.g. 14d")
    q.add_argument("--source", help="comma-separated source names")
    q.add_argument("--limit", type=int, default=100)
    q.add_argument("--json", action="store_true")
    rd = ssub.add_parser("read", help="print one session")
    rd.add_argument("session")
    rd.add_argument("--source")
    rd.add_argument("--max-chars", type=int, default=60000)
    rd.add_argument("--json", action="store_true")

    p = sub.add_parser("mine", help="mine recent sessions for skill candidates")
    p.add_argument("--since", help="lookback window override, e.g. 7d")
    p.add_argument("--dry-run", action="store_true", help="render the prompt without invoking the agent")
    p.add_argument("--quiet", action="store_true")

    p = sub.add_parser("candidates", help="inspect the candidate queue")
    csub = p.add_subparsers(dest="candidates_command", required=True)
    cl = csub.add_parser("list")
    cl.add_argument("--json", action="store_true")
    cs = csub.add_parser("show")
    cs.add_argument("id")
    cs.add_argument("--json", action="store_true")

    p = sub.add_parser("accept", help="run the writer agent for a candidate")
    p.add_argument("id")

    p = sub.add_parser("install", help="install a validated draft")
    p.add_argument("id")

    p = sub.add_parser("uninstall", help="remove a poppy-managed skill")
    p.add_argument("name")

    p = sub.add_parser("installed", help="list poppy-managed skills")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("ui", help="serve the review UI (localhost only)")
    p.add_argument("--port", type=int)

    p = sub.add_parser("doctor", help="check the installation")
    p.add_argument("--agent", action="store_true", help="also test the configured agent commands")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("schedule", help="manage the mining schedule")
    ssub = p.add_subparsers(dest="schedule_command", required=True)
    si = ssub.add_parser("install")
    si.add_argument("--dry-run", action="store_true")
    ssub.add_parser("status")
    ssub.add_parser("uninstall")

    sub.add_parser("status", help="summary of home, queue, and schedule")
    sub.add_parser("selftest", help="run the bundled test suite")
    return parser


# --------------------------------------------------------------------------- helpers


def _load_sources(home: Path, names: str | None = None) -> list:
    sources = load_sources(home)
    if names:
        wanted = {part.strip() for part in names.split(",") if part.strip()}
        sources = [s for s in sources if s.name in wanted]
        if not sources:
            raise PoppyError(f"no matching sources: {names}")
    return sources


def _since_cutoff(value: str | None) -> float | None:
    if not value:
        return None
    return time.time() - parse_duration(value)


def _print_sessions(sessions: list, as_json: bool) -> None:
    if as_json:
        print(json.dumps([s.to_dict() for s in sessions], indent=2))
        return
    if not sessions:
        print("(no sessions)")
        return
    for session in sessions:
        title = tail(session.title or session.cwd or "", 60)
        size = human_size(session.size) if session.size else "-"
        print(
            f"{session.source:<12} {session.id[:36]:<36} {human_time(session.time):<16} "
            f"{size:>7}  {title}"
        )


# --------------------------------------------------------------------------- commands


def cmd_init(args, home: Path) -> int:
    ensure_home_layout(home)
    cfg, created = init_config(home, force=args.force)
    if not sources_path(home).exists():
        save_sources(home, [])
    print(f"poppy home: {home}")
    print(f"config:     {'written' if created else 'kept'} ({home / 'config.json'})")
    print(f"sources:    {home / 'sources.json'}")
    if created:
        print("\nnext: configure sources and agent commands, then run `poppy doctor --agent`.")
    return 0


def cmd_config(args, home: Path) -> int:
    cfg = load_config(home)
    if args.config_command == "show":
        print(json.dumps(cfg, indent=2))
        return 0
    if args.config_command == "get":
        value = get_dotted(cfg, args.key)
        print(json.dumps(value, indent=2) if not isinstance(value, str) else value)
        return 0
    set_dotted(cfg, args.key, parse_value(args.value))
    save_config(home, cfg)
    print(f"{args.key} = {json.dumps(get_dotted(cfg, args.key))}")
    return 0


def cmd_sources(args, home: Path) -> int:
    if args.sources_command == "list":
        data = load_json(sources_path(home), {"sources": []}) or {}
        for entry in data.get("sources", []):
            print(f"{entry.get('name'):<14} {entry.get('type'):<8} {entry.get('description', '')}")
        if not data.get("sources"):
            print("(no sources configured)")
        return 0

    if args.sources_command == "add":
        if args.file:
            entry = json.loads(Path(args.file).expanduser().read_text(encoding="utf-8"))
        elif args.json:
            entry = json.loads(args.json)
        else:
            raise PoppyError("sources add needs --json or --file")
        build_source(entry)  # fail fast if unusable
        data = load_json(sources_path(home), {"sources": []}) or {}
        entries = [e for e in data.get("sources", []) if e.get("name") != entry.get("name")]
        entries.append(entry)
        save_sources(home, entries)
        print(f"added source: {entry.get('name')}")
        return 0

    if args.sources_command == "remove":
        data = load_json(sources_path(home), {"sources": []}) or {}
        entries = [e for e in data.get("sources", []) if e.get("name") != args.name]
        if len(entries) == len(data.get("sources", [])):
            raise PoppyError(f"no such source: {args.name}")
        save_sources(home, entries)
        print(f"removed source: {args.name}")
        return 0

    # test
    sources = _load_sources(home, args.name)
    failures = 0
    for source in sources:
        try:
            sessions = source.list_sessions(limit=3)
            detail = f"{source.type}; {len(sessions)} session(s)"
            if sessions:
                text = source.read_session(sessions[0].id, max_chars=2000)
                detail += f"; read {len(text)} chars from {sessions[0].id}"
            print(f"✓ {source.name}: {detail}")
        except PoppyError as exc:
            failures += 1
            print(f"✗ {source.name}: {exc}")
    return 1 if failures else 0


def cmd_sessions(args, home: Path) -> int:
    sources = _load_sources(home, getattr(args, "source", None))
    cutoff = _since_cutoff(getattr(args, "since", None))

    if args.sessions_command == "list":
        sessions = collect_sessions(sources, since=cutoff, limit=args.limit)
        _print_sessions(sessions, args.json)
        return 0

    if args.sessions_command == "search":
        hits = search_sessions(sources, args.pattern, since=cutoff, limit=args.limit)
        if args.json:
            print(json.dumps([h.__dict__ for h in hits], indent=2))
        else:
            for hit in hits:
                print(f"{hit.source:<12} {hit.session[:28]:<28} :{hit.line:<5} {tail(hit.snippet, 90)}")
            if not hits:
                print("(no matches)")
        return 0

    source, text = get_session(sources, args.session, source_name=args.source, max_chars=args.max_chars)
    if args.json:
        print(json.dumps({"source": source.name, "session": args.session, "text": text}, indent=2))
    else:
        print(text)
    return 0


def cmd_mine(args, home: Path) -> int:
    since = parse_duration(args.since) if args.since else None
    summary = mine(home, since_seconds=since, dry_run=args.dry_run, quiet=args.quiet)
    if summary.get("dry_run"):
        print(f"dry run: {summary['sessions']} session(s) in the lookback window")
        print(f"prompt written to: {summary['prompt']}")
        return 0
    if not args.quiet:
        print(
            f"mined {summary['sessions']} session(s) in {summary['duration_sec']:.0f}s "
            f"(agent exit {summary.get('agent_exit')})"
        )
        for item in summary["accepted"]:
            print(f"  ✓ candidate {item['id']}  {item['title']}")
        for item in summary["invalid"]:
            print(f"  ✗ {item['file']}: {'; '.join(item['errors'])}")
        print(f"log: {summary.get('log', summary['prompt'])}")
        if summary["accepted"]:
            print("review: poppy ui")
    return 0 if summary.get("agent_exit", 0) == 0 else 1


def cmd_candidates(args, home: Path) -> int:
    if args.candidates_command == "list":
        candidates = list_candidates(home)
        if args.json:
            print(json.dumps(candidates, indent=2))
        else:
            for candidate in candidates:
                print(
                    f"{candidate.get('status', '?'):<14} {candidate.get('id')}  "
                    f"{tail(candidate.get('title', ''), 60)}"
                )
            if not candidates:
                print("(queue empty)")
        return 0
    candidate = load_candidate(home, args.id)
    if args.json:
        print(json.dumps(candidate, indent=2))
    else:
        print(f"id:      {candidate['id']}")
        print(f"status:  {candidate.get('status')}")
        print(f"title:   {candidate.get('title')}")
        print(f"trigger: {candidate.get('trigger')}")
        print(f"summary: {candidate.get('summary')}")
        for item in candidate.get("evidence", []):
            print(f"\n--- {item.get('source')}:{item.get('session')} ---\n{item.get('quote')}")
        for warning in candidate.get("warnings", []):
            print(f"warning: {warning}")
    return 0


def cmd_accept(args, home: Path) -> int:
    result = accept(home, args.id)
    status = result.get("status")
    if status == "draft":
        print(f"draft ready: {result.get('name')} (review with `poppy ui` or `poppy install {args.id}`)")
        return 0
    if status == "writer_rejected":
        print(f"writer rejected the candidate: {result.get('reason')}")
        return 0
    print(f"{status}: {result.get('error') or result.get('errors')}")
    return 1


def cmd_install(args, home: Path) -> int:
    cfg = load_config(home)
    candidate = load_candidate(home, args.id)
    if candidate.get("status") != "draft":
        raise PoppyError(f"candidate {args.id} has no validated draft (status: {candidate.get('status')})")
    name, dirs = install_draft(home, cfg, candidate)
    candidate["status"] = "installed"
    from .candidates import save_candidate

    save_candidate(home, candidate)
    print(f"installed {name}:")
    for path in dirs:
        print(f"  {path}")
    return 0


def cmd_uninstall(args, home: Path) -> int:
    removed = uninstall_skill(home, args.name)
    print(f"uninstalled {args.name}:")
    for path in removed:
        print(f"  {path}")
    return 0


def cmd_installed(args, home: Path) -> int:
    skills = load_manifest(home).get("skills", {})
    if args.json:
        print(json.dumps(skills, indent=2))
        return 0
    if not skills:
        print("(no poppy-managed skills)")
        return 0
    for name, entry in sorted(skills.items()):
        print(f"{name:<40} installed {entry.get('installed_at')}  candidate {entry.get('candidate')}")
    return 0


def cmd_ui(args, home: Path) -> int:
    cfg = load_config(home)
    if args.port:
        cfg.setdefault("ui", {})["port"] = args.port
    return serve(home, cfg)


def cmd_doctor(args, home: Path) -> int:
    checks = run_checks(home, with_agent=args.agent)
    if args.json:
        print(json.dumps([check.__dict__ for check in checks], indent=2))
    else:
        for check in checks:
            print(f" {SYMBOL.get(check.status, '?')} {check.name:<28} {check.detail}")
        failed = [check for check in checks if check.status == "fail"]
        warnings = [check for check in checks if check.status == "warn"]
        print(f"\n{len(checks)} checks, {len(failed)} failed, {len(warnings)} warning(s)")
    return 1 if any(check.status == "fail" for check in checks) else 0


def cmd_schedule(args, home: Path) -> int:
    if args.schedule_command == "install":
        result = schedule_install(home, dry_run=args.dry_run)
        if args.dry_run:
            print(f"scheduler: {result['kind']}")
            for path, content in result.get("files", {}).items():
                print(f"\n--- {path} ---\n{content}")
            if result.get("cron_line"):
                print(f"\nadd to crontab:\n  {result['cron_line']}")
            return 0
        print(f"scheduler: {result['kind']}")
        for path in result.get("files", {}):
            print(f"  {path}")
        print("enabled: weekly (Mon 09:00, jittered)" if result.get("enabled") else "not enabled")
        return 0
    if args.schedule_command == "status":
        status = schedule_status(home)
        print(f"{'installed' if status.get('installed') else 'not installed'} ({status.get('kind')}): {status.get('detail')}")
        return 0
    result = schedule_uninstall(home)
    for path in result.get("removed", []):
        print(f"removed {path}")
    print(f"scheduler: {result['kind']}")
    return 0


def cmd_status(args, home: Path) -> int:
    cfg = load_config(home)
    candidates = list_candidates(home)
    counts: dict[str, int] = {}
    for candidate in candidates:
        key = str(candidate.get("status", "?"))
        counts[key] = counts.get(key, 0) + 1
    sources = load_sources(home)
    schedule = schedule_status(home)
    installed = load_manifest(home).get("skills", {})
    print(f"home:      {home}")
    print(f"sources:   {', '.join(s.name for s in sources) or '(none)'}")
    print(f"queue:     " + (", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "(empty)"))
    print(f"installed: {len(installed)} skill(s)")
    print(f"schedule:  {'installed' if schedule.get('installed') else 'not installed'} ({schedule.get('detail')})")
    print(f"skills:    {', '.join(cfg.get('skills_dirs') or []) or '(none configured)'}")
    return 0


def cmd_selftest(args, home: Path) -> int:
    import unittest

    loader = unittest.TestLoader()
    suite = loader.discover(str(REPO_ROOT / "tests"), pattern="test_*.py", top_level_dir=str(REPO_ROOT))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# --------------------------------------------------------------------------- dispatch


def dispatch(args, home: Path) -> int:
    handlers = {
        "init": cmd_init,
        "config": cmd_config,
        "sources": cmd_sources,
        "sessions": cmd_sessions,
        "mine": cmd_mine,
        "candidates": cmd_candidates,
        "accept": cmd_accept,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "installed": cmd_installed,
        "ui": cmd_ui,
        "doctor": cmd_doctor,
        "schedule": cmd_schedule,
        "status": cmd_status,
        "selftest": cmd_selftest,
    }
    return handlers[args.command](args, home)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    home = poppy_home()
    try:
        return dispatch(args, home)
    except PoppyError as exc:
        print(f"poppy: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
