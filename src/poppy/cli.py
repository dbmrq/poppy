"""Poppy command line interface."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from . import decay as decay_mod
from . import digest
from . import library
from . import publish as publish_mod
from . import sync as sync_mod
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
from .skills import (
    adopt_installed,
    archive_skill,
    install_builtin_skill,
    install_draft,
    load_manifest,
    restore_skill,
    uninstall_skill,
)
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

    p = sub.add_parser("context", help="show or materialize the memory index")
    csub = p.add_subparsers(dest="context_command")
    cs = csub.add_parser("show", help="print memories and rules that apply here")
    cs.add_argument("--cwd", help="directory to resolve project scope for (default: cwd)")
    cs.add_argument("--brief", action="store_true", help="compact output (capped; empty when nothing applies)")
    cs.add_argument("--json", action="store_true")
    csub.add_parser("export", help="regenerate the always-on digest file")
    cw = csub.add_parser("wire", help="insert the digest as a managed block into a context file")
    cw.add_argument("--file", required=True)
    cu = csub.add_parser("unwire", help="remove a wired digest block")
    cu.add_argument("--file", required=True)
    csub.add_parser("status", help="check digest freshness and wiring")
    csub.add_parser("verify", help="prove the digest reaches a headless session")
    p.set_defaults(context_command="show", cwd=None, brief=False, json=False)

    p = sub.add_parser("library", help="inspect and manage the canonical library")
    lsub = p.add_subparsers(dest="library_command", required=True)
    ll = lsub.add_parser("list")
    ll.add_argument("--kind", choices=("skill", "memory", "rule"))
    ll.add_argument("--archived", action="store_true")
    ll.add_argument("--json", action="store_true")
    ls = lsub.add_parser("show")
    ls.add_argument("id")
    ls.add_argument("--json", action="store_true")
    for name in ("verify", "pin", "unpin"):
        lp = lsub.add_parser(name)
        lp.add_argument("id")
    la = lsub.add_parser("archive")
    la.add_argument("id")
    lr = lsub.add_parser("restore")
    lr.add_argument("id")
    lsub.add_parser("adopt", help="import V1-installed skills into the library")

    p = sub.add_parser("decay", help="scan for stale entries and propose archives")
    p.add_argument("--json", action="store_true")
    p.add_argument("--resolve", help="resolve a decay proposal by id")
    p.add_argument("--resolution", choices=("archive", "keep", "pin"))

    p = sub.add_parser("ui", help="serve the review UI (localhost only)")
    p.add_argument("--port", type=int)

    p = sub.add_parser("doctor", help="check the installation")
    p.add_argument("--agent", action="store_true", help="also test the configured agent commands")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("schedule", help="manage the mining and sync schedules")
    ssub = p.add_subparsers(dest="schedule_command", required=True)
    si = ssub.add_parser("install")
    si.add_argument("--dry-run", action="store_true")
    ssub.add_parser("status")
    ssub.add_parser("uninstall")

    p = sub.add_parser("sync", help="sync the library with a private git remote")
    ssub = p.add_subparsers(dest="sync_command", required=True)
    si = ssub.add_parser("init", help="initialize the repo and optionally set the remote")
    si.add_argument("--remote", help="git remote URL (recommended: an empty private repo)")
    si.add_argument("--branch", help="branch name (default: main)")
    si.add_argument("--machine", help="machine name for commits and machine-scoped entries")
    sr = ssub.add_parser("run", help="commit, pull, push, then materialize")
    sr.add_argument("--quiet", action="store_true")
    ss = ssub.add_parser("status", help="show repo, remote, and materialization state")
    ss.add_argument("--json", action="store_true")

    p = sub.add_parser("publish", help="copy a reviewed skill into a public skills repo checkout")
    p.add_argument("name")
    p.add_argument("--to", help="path to a checkout of the public repo (default: publish.target)")
    p.add_argument("--subdir", help="subdirectory inside the target (default: publish.subdir)")
    p.add_argument("--commit", action="store_true", help="commit the change in the target repo")
    p.add_argument("--push", action="store_true", help="commit and push")
    p.add_argument("--force", action="store_true", help="overwrite a destination that differs")

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
            f"{session.source:<12} {session.id:<36} {human_time(session.time):<16} "
            f"{size:>7}  {title}"
        )


# --------------------------------------------------------------------------- commands


def cmd_init(args, home: Path) -> int:
    ensure_home_layout(home)
    cfg, created = init_config(home, force=args.force)
    if not sources_path(home).exists():
        save_sources(home, [])
    library.ensure_library(home)
    created_builtins = library.ensure_builtin_skills(home)
    mirrored = []
    for name in created_builtins:
        if cfg.get("skills_dirs"):
            try:
                install_builtin_skill(home, cfg, name)
                mirrored.append(name)
            except PoppyError as exc:
                print(f"note: could not mirror builtin {name}: {exc}")
    print(f"poppy home: {home}")
    print(f"config:     {'written' if created else 'kept'} ({home / 'config.json'})")
    print(f"sources:    {home / 'sources.json'}")
    print(f"library:    {home / 'library'}")
    if created_builtins:
        detail = f" (mirrored: {', '.join(mirrored)})" if mirrored else ""
        print(f"builtins:   {', '.join(created_builtins)}{detail}")
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
                print(f"{hit.source:<12} {hit.session:<36} :{hit.line:<5} {tail(hit.snippet, 90)}")
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
        if summary.get("decay"):
            print(f"  decay: {summary['decay']} proposal(s) to review")
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
    if status == "active":
        entry = result.get("entry") or {}
        print(f"accepted {entry.get('kind', 'entry')}: {entry.get('title') or entry.get('id')} (scope: {entry.get('scope')})")
        return 0
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


def _first_sentence(text: str, limit: int = 240) -> str:
    paragraph = (text or "").strip().split("\n\n")[0].replace("\n", " ")
    return tail(paragraph, limit)


def _brief_text(context: dict) -> str:
    lines = [f"Poppy context ({context['host']} · {context['cwd']})"]
    if context["rules"]:
        lines.append("Rules (always apply):")
        for entry in context["rules"]:
            lines.append(f"- [{entry['scope']}] {entry['title']}: {_first_sentence(entry.get('body', ''))}")
    if context["memories"]:
        lines.append("Memories:")
        for entry in context["memories"]:
            lines.append(f"- [{entry['scope']}] {entry['title']}: {_first_sentence(entry.get('body', ''))}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines) + "\n"


def cmd_context(args, home: Path) -> int:
    cfg = load_config(home)
    command = getattr(args, "context_command", None) or "show"

    if command == "export":
        result = digest.export(home, cfg)
        print(
            f"digest: {result['path']}\n"
            f"  {result['chars']} chars · {result['binding_rules']} binding rule(s) · "
            f"{result['memories']} memory headline(s) · {result['scoped_rules']} scoped rule(s)"
            + (f" · {result['dropped']} dropped" if result.get("dropped") else "")
        )
        return 0

    if command == "wire":
        result = digest.wire(home, cfg, Path(args.file))
        print(f"wired digest into {result['file']} (managed block)")
        print("prove it reaches sessions with `poppy context verify`")
        return 0

    if command == "unwire":
        result = digest.unwire(home, cfg, Path(args.file))
        print(f"removed digest block from {result['file']}")
        return 0

    if command == "status":
        status = digest.status(home, cfg)
        print(f"digest:    {status['digest']}")
        print(f"exists:    {'yes' if status['exists'] else 'no'}")
        print(f"generated: {status['generated_at'] or '-'}")
        print(f"fresh:     {'yes' if status['fresh'] else 'no — run `poppy context export`'}")
        if status["stats"]:
            print(f"content:   {status['stats']}")
        if status["targets"]:
            for target in status["targets"]:
                mark = "✓" if target["exists"] and target["block"] else "✗"
                print(f" {mark} wired: {target['file']}")
        else:
            print("wired:     nowhere yet — `poppy context wire --file <path>` or a native include")
        return 0

    if command == "verify":
        ok, detail = digest.verify(home, cfg)
        print(f"{'✓' if ok else '✗'} {detail}")
        return 0 if ok else 1

    cwd = Path(args.cwd).expanduser() if args.cwd else Path.cwd()
    context = library.build_context(home, cfg, cwd=cwd)
    if args.brief:
        context["memories"] = context["memories"][:12]
    if args.json:
        print(json.dumps(context, indent=2))
        return 0
    if args.brief:
        text = _brief_text(context)
        if text:
            print(text, end="")
        return 0
    print(f"# Poppy context — {context['host']} — {context['cwd']}")
    sections = (
        ("Rules", context["rules"], True),
        ("Memories", context["memories"], True),
        ("Poppy skills", context["skills"], False),
    )
    for title, entries, with_summary in sections:
        if not entries:
            continue
        print(f"\n## {title}")
        for entry in entries:
            scope = f"[{entry['scope']}] " if with_summary else ""
            print(f"- {scope}{entry['title']}")
            if with_summary:
                summary = (entry.get("body") or "").split("\n\n")[0]
                if summary:
                    print(f"  {summary}")
    if not any(entries for _title, entries, _s in sections):
        print("\n(no entries yet)")
    return 0


def cmd_library(args, home: Path) -> int:
    cfg = load_config(home)
    usage = library.load_usage(home)

    if args.library_command == "list":
        kinds = (args.kind,) if args.kind else None
        entries = library.list_entries(home, kinds=kinds, include_archived=args.archived)
        if args.json:
            print(json.dumps([entry.to_dict(usage) for entry in entries], indent=2))
            return 0
        for entry in sorted(entries, key=lambda e: (e.kind, e.scope, e.title.lower())):
            flags = []
            if entry.kind != "skill":
                flags.append(entry.scope)
            if entry.archived:
                flags.append("archived")
            if entry.pinned:
                flags.append("pinned")
            suffix = f"  ({', '.join(flags)})" if flags else ""
            print(f"{entry.kind:<7} {entry.id:<18} {entry.title}{suffix}")
        if not entries:
            print("(library empty)")
        return 0

    if args.library_command == "show":
        entry = library.find_entry(home, args.id)
        data = entry.to_dict(usage)
        if args.json:
            print(json.dumps(data, indent=2))
            return 0
        print(f"id:            {data['id']}")
        print(f"kind:          {data['kind']}")
        print(f"title:         {data['title']}")
        print(f"scope:         {data['scope']}" + (f" · {data['project']}" if data.get("project") else ""))
        print(f"status:        {data['status']}{' (archived)' if data['archived'] else ''}")
        print(f"pinned:        {data['pinned']}")
        print(f"created:       {data['created']}")
        print(f"last verified: {data['last_verified']}")
        print(f"last used:     {data['last_used'] or '-'} ({data['uses']}×)")
        print(f"path:          {data['path']}")
        print()
        print(entry.body)
        return 0

    if args.library_command == "verify":
        library.verify_entry(library.find_entry(home, args.id))
        print(f"verified {args.id}")
        return 0

    if args.library_command in ("pin", "unpin"):
        library.set_pinned(library.find_entry(home, args.id), args.library_command == "pin")
        print(f"{args.library_command} {args.id}")
        return 0

    if args.library_command == "archive":
        entry = library.find_entry(home, args.id)
        if entry.kind == "skill":
            removed = archive_skill(home, entry.id)
            print(f"archived skill {entry.id} (removed mirrors: {', '.join(removed) or 'none'})")
        else:
            library.archive_entry(home, entry)
            print(f"archived {entry.id}")
        return 0

    if args.library_command == "restore":
        entry = library.find_entry(home, args.id)
        if entry.kind == "skill":
            dirs = restore_skill(home, cfg, entry.id)
            print(f"restored skill {entry.id} (mirrors: {', '.join(dirs) or 'none'})")
        else:
            library.restore_entry(home, entry)
            print(f"restored {entry.id}")
        return 0

    if args.library_command == "adopt":
        adopted = adopt_installed(home, cfg)
        print(f"adopted into library: {', '.join(adopted) if adopted else '(nothing to do)'}")
        return 0

    return 1


def cmd_decay(args, home: Path) -> int:
    cfg = load_config(home)
    if args.resolve:
        if not args.resolution:
            raise PoppyError("--resolve needs --resolution archive|keep|pin")
        print(json.dumps(decay_mod.resolve(home, cfg, args.resolve, args.resolution)))
        return 0
    proposals = decay_mod.scan(home, cfg)
    if args.json:
        print(json.dumps(proposals, indent=2))
        return 0
    for proposal in proposals:
        print(f"{proposal['id']}  {proposal['title']} — {proposal['summary']}")
    print(
        f"{len(proposals)} proposal(s); resolve with "
        "`poppy decay --resolve <id> --resolution archive|keep|pin` or in `poppy ui`"
    )
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
    cfg = load_config(home)
    if args.schedule_command == "install":
        result = schedule_install(home, cfg, dry_run=args.dry_run)
        if args.dry_run:
            print(f"scheduler: {result['kind']}")
            for path, content in result.get("files", {}).items():
                print(f"\n--- {path} ---\n{content}")
            for line in result.get("cron_lines", []):
                print(f"\nadd to crontab:\n  {line}")
            return 0
        print(f"scheduler: {result['kind']}")
        for path in result.get("files", {}):
            print(f"  {path}")
        if result.get("enabled"):
            print("mining:  enabled (weekly, Mon 09:00, jittered)")
        if result.get("sync_enabled"):
            print("sync:    enabled (frequent; `poppy sync status` for details)")
        elif not (cfg.get("sync") or {}).get("enabled"):
            print("sync:    not enabled (optional — `poppy sync init --remote <url>`)")
        return 0
    if args.schedule_command == "status":
        status = schedule_status(home, cfg)
        print(f"mining:  {'installed' if status.get('installed') else 'not installed'} ({status.get('kind')}): {status.get('detail')}")
        sync = status.get("sync") or {}
        print(f"sync:    {'installed' if sync.get('installed') else 'not installed'}: {sync.get('detail')}")
        return 0
    result = schedule_uninstall(home)
    for path in result.get("removed", []):
        print(f"removed {path}")
    print(f"scheduler: {result['kind']}")
    return 0


def _print_sync_run(result: dict) -> None:
    if result.get("skipped"):
        print(f"skipped: {result['skipped']}")
        return
    actions = []
    if result.get("committed"):
        actions.append("committed local changes")
    if result.get("pulled"):
        actions.append("pulled remote changes")
    if result.get("pushed"):
        actions.append("pushed")
    if result.get("offline"):
        actions.append("offline — will retry next run")
    if result.get("conflict"):
        actions.append("CONFLICT — rebase aborted; resolve manually")
    mirrors = (result.get("materialized") or {}).get("mirrors") or {}
    for label in ("installed", "updated", "removed"):
        names = mirrors.get(label) or []
        if names:
            actions.append(f"{label} {len(names)} mirror(s)")
    for error in mirrors.get("errors") or []:
        actions.append(f"mirror error: {error}")
    print("sync: " + ("; ".join(actions) if actions else "nothing to do"))
    if result.get("remote_error"):
        print(f"remote error: {result['remote_error']}")


def _print_sync_status(status: dict) -> None:
    if not status["initialized"]:
        print(f"repo:    not initialized ({status['repo']}) — run `poppy sync init`")
        print("         if another machine already syncs this library, point this one at the same repo:")
        print("         poppy sync init --remote <url>")
        return
    print(f"repo:    {status['repo']} (branch {status['branch']})")
    print(f"remote:  {status['remote'] or '(none — local only)'}")
    bits = []
    if status["dirty"]:
        bits.append("uncommitted changes")
    if status["ahead"]:
        bits.append(f"ahead {status['ahead']}")
    if status["behind"]:
        bits.append(f"behind {status['behind']}")
    if status["rebase_in_progress"]:
        bits.append("rebase in progress — resolve manually")
    print("state:   " + (" · ".join(bits) if bits else "clean · up to date"))
    if status.get("last_run"):
        print(f"last:    {status['last_run']} ({status.get('last_status') or '?'})")
    mirrors = status.get("mirrors") or {}
    if mirrors:
        print(
            f"mirrors: {mirrors['library']} skill(s) in library · "
            f"{len(mirrors['not_mirrored'])} not mirrored · {len(mirrors['drifted'])} drifted · "
            f"{len(mirrors['orphaned'])} orphaned"
        )
    print(f"machine: {status['machine']}")


def cmd_sync(args, home: Path) -> int:
    cfg = load_config(home)
    if args.sync_command == "init":
        result = sync_mod.init(home, cfg, remote=args.remote, branch=args.branch, machine=args.machine)
        print(f"repo:    {result['repo']} (branch {result['branch']})")
        print(f"remote:  {result['remote'] or '(none — local only; add one with `poppy sync init --remote <url>`)'}")
        _print_sync_run(result)
        print("next:    `poppy schedule install` enables automatic sync")
        return 2 if int(result.get("code", 0)) == 2 else 0
    if args.sync_command == "run":
        try:
            result = sync_mod.run(home, cfg)
        except PoppyError as exc:
            print(f"poppy sync: {exc}", file=sys.stderr)
            return 2
        if args.quiet:
            if result.get("code") == 2:
                print(f"poppy sync: {result.get('remote_error') or 'conflict'}", file=sys.stderr)
        else:
            _print_sync_run(result)
        return int(result.get("code", 0))
    status = sync_mod.status(home, cfg)
    if args.json:
        print(json.dumps(status, indent=2))
        return 0
    _print_sync_status(status)
    return 0


def cmd_publish(args, home: Path) -> int:
    cfg = load_config(home)
    result = publish_mod.publish(
        home,
        cfg,
        args.name,
        target=args.to,
        subdir=args.subdir,
        commit=args.commit,
        push=args.push,
        force=args.force,
    )
    if result["action"] == "unchanged":
        print(f"{result['name']}: already published at {result['path']} (nothing to do)")
        return 0
    print(f"{result['action']} {result['name']}: {result['path']}")
    for warning in result["warnings"]:
        print(f"warning: {warning}")
    if result["committed"]:
        print("committed" + (" and pushed" if result["pushed"] else ""))
    else:
        print("next: review the change and commit, or re-run with --commit [--push]")
    return 0


def cmd_status(args, home: Path) -> int:
    cfg = load_config(home)
    candidates = list_candidates(home)
    counts: dict[str, int] = {}
    for candidate in candidates:
        key = str(candidate.get("status", "?"))
        counts[key] = counts.get(key, 0) + 1
    sources = load_sources(home)
    schedule = schedule_status(home, cfg)
    installed = load_manifest(home).get("skills", {})
    library_entries = library.list_entries(home)
    archived = [e for e in library.list_entries(home, include_archived=True) if e.archived]
    kind_counts = {"skill": 0, "memory": 0, "rule": 0}
    for entry in library_entries:
        kind_counts[entry.kind] = kind_counts.get(entry.kind, 0) + 1
    decay_pending = [c for c in candidates if c.get("kind") == "decay"]
    print(f"home:      {home}")
    print(f"sources:   {', '.join(s.name for s in sources) or '(none)'}")
    print(f"queue:     " + (", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "(empty)"))
    print(
        f"library:   {kind_counts['skill']} skill(s), {kind_counts['memory']} memory entry(ies), "
        f"{kind_counts['rule']} rule(s), {len(archived)} archived"
    )
    if decay_pending:
        print(f"decay:     {len(decay_pending)} proposal(s) awaiting review")
    print(f"installed: {len(installed)} skill(s) mirrored")
    print(f"schedule:  {'installed' if schedule.get('installed') else 'not installed'} ({schedule.get('detail')})")
    sync_state = sync_mod.status(home, cfg)
    if sync_state.get("initialized"):
        bits = []
        if sync_state["dirty"]:
            bits.append("uncommitted")
        if sync_state["ahead"]:
            bits.append(f"ahead {sync_state['ahead']}")
        if sync_state["behind"]:
            bits.append(f"behind {sync_state['behind']}")
        detail = " · ".join(bits) or "up to date"
        print(
            f"sync:      {sync_state['remote'] or 'local only'} ({detail}; "
            f"last {sync_state.get('last_run') or 'never'})"
        )
    else:
        print("sync:      not initialized (optional — `poppy sync init`)")
    print(f"skills:    {', '.join(str(d) for d in (cfg.get('skills_dirs') or [])) or '(none configured)'}")
    return 0


def cmd_selftest(args, home: Path) -> int:
    import unittest

    loader = unittest.TestLoader()
    suite = loader.discover(str(REPO_ROOT / "tests"), pattern="test_*.py", top_level_dir=str(REPO_ROOT))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# --------------------------------------------------------------------------- dispatch


REFRESH_COMMANDS = {"accept", "install", "uninstall", "library", "decay", "mine", "init", "sync"}


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
        "context": cmd_context,
        "library": cmd_library,
        "decay": cmd_decay,
        "ui": cmd_ui,
        "doctor": cmd_doctor,
        "schedule": cmd_schedule,
        "sync": cmd_sync,
        "publish": cmd_publish,
        "status": cmd_status,
        "selftest": cmd_selftest,
    }
    code = handlers[args.command](args, home)
    if args.command in REFRESH_COMMANDS:
        try:  # the digest is a cache; never let a refresh failure break a command
            digest.export(home, load_config(home))
        except Exception:
            pass
    return code


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
