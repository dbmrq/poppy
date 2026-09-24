"""The two agent-driven pipelines: mine (find candidates) and accept (write a skill)."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import decay, library
from . import __version__
from .agent import run_agent
from .candidates import (
    drafts_candidate_dir,
    load_candidate,
    mark_invalid,
    save_candidate,
    validate_raw,
)
from .config import load_config
from .prompts import render
from .sessions import collect_sessions
from .skills import validate_skill
from .sources import load_sources, sources_path
from .util import (
    PoppyError,
    acquire_lock,
    atomic_write_text,
    ensure_home_layout,
    load_json,
    now_iso,
    release_lock,
    tail,
)


def poppy_cmd() -> str:
    import shutil as _shutil

    exe = _shutil.which("poppy")
    if exe:
        return exe
    return str(Path(__file__).resolve().parents[2] / "bin" / "poppy")


def library_index(home: Path) -> str:
    entries = library.list_entries(home)
    if not entries:
        return "(none yet)"
    lines: list[str] = []
    for kind in ("skill", "memory", "rule"):
        subset = [e for e in entries if e.kind == kind]
        if not subset:
            continue
        lines.append(f"### {kind}s")
        for entry in subset:
            scope = f" [{entry.scope}]" if entry.kind != "skill" else ""
            lines.append(f"- {entry.title}{scope}")
    return "\n".join(lines)


def sources_summary(home: Path, sources: list) -> str:
    raw = load_json(sources_path(home), {"sources": []}) or {}
    descriptions = {e.get("name"): e.get("description", "") for e in raw.get("sources", [])}
    lines = []
    for source in sources:
        desc = descriptions.get(source.name) or ""
        lines.append(f"- {source.name} (type: {source.type})" + (f" — {desc}" if desc else ""))
    return "\n".join(lines)


def _lookback_label(window_seconds: float) -> str:
    if window_seconds >= 86400 and abs(window_seconds % 86400) < 1:
        return f"{int(window_seconds / 86400)}d"
    return f"{max(1, int(round(window_seconds / 3600)))}h"


def _evidence_block(candidate: dict) -> str:
    chunks = []
    for item in candidate.get("evidence", []):
        chunks.append(
            f"### {item.get('source')}:{item.get('session')}\n\n"
            f"> {item.get('quote')}\n\n"
            f"Excerpt:\n\n{item.get('excerpt', '')}"
        )
    return "\n\n".join(chunks) if chunks else "(none)"


def mine(
    home: Path,
    since_seconds: float | None = None,
    dry_run: bool = False,
    quiet: bool = False,
    config: dict | None = None,
) -> dict:
    ensure_home_layout(home)
    cfg = config or load_config(home)
    sources = load_sources(home)
    if not sources:
        raise PoppyError("no sources configured — run the installer prompt or `poppy sources add`")

    lookback_days = cfg.get("lookback_days", 14)
    window = since_seconds if since_seconds is not None else float(lookback_days) * 86400
    since = time.time() - window
    max_sessions = cfg.get("max_sessions_per_run")
    sessions = collect_sessions(sources, since=since, limit=int(max_sessions) if max_sessions else None)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    inbox = home / "inbox" / f"run-{stamp}"
    inbox.mkdir(parents=True, exist_ok=True)
    prompt_path = home / "logs" / f"mine-{stamp}.prompt.md"
    log_path = home / "logs" / f"mine-{stamp}.log"

    prompt = render(
        "miner",
        {
            "VERSION": __version__,
            "LOOKBACK_DAYS": _lookback_label(window),
            "SESSIONS_TOTAL": len(sessions),
            "MAX_CANDIDATES": cfg.get("max_candidates_per_run", 5),
            "MIN_EVIDENCE": cfg.get("min_evidence", 1),
            "INBOX_DIR": str(inbox),
            "POPPY_CMD": poppy_cmd(),
            "LIBRARY_INDEX": library_index(home),
            "SOURCES_SUMMARY": sources_summary(home, sources),
        },
    )
    atomic_write_text(prompt_path, prompt)

    summary = {
        "sessions": len(sessions),
        "prompt": str(prompt_path),
        "log": str(log_path),
        "inbox": str(inbox),
        "accepted": [],
        "invalid": [],
        "duration_sec": 0.0,
        "dry_run": dry_run,
    }
    if dry_run:
        summary["agent_stdout_tail"] = "(dry run: agent not invoked)"
        atomic_write_text(log_path, f"DRY RUN — prompt written to {prompt_path}\n")
        return summary

    started = time.time()
    result = run_agent(
        "miner",
        prompt,
        cfg,
        home,
        env_extra={"POPPY_INBOX": str(inbox)},
        log_path=log_path,
    )
    summary["duration_sec"] = time.time() - started
    summary["agent_exit"] = result.returncode
    summary["agent_stdout_tail"] = tail(result.stdout, 1200)

    accepted: list[dict] = []
    invalid: list[dict] = []
    for path in sorted(inbox.glob("*.json")):
        raw_text = path.read_text(encoding="utf-8", errors="replace")
        try:
            raw = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            mark_invalid(home, raw_text[:4000], [f"invalid JSON: {exc}"])
            invalid.append({"file": path.name, "errors": [f"invalid JSON: {exc}"]})
            continue
        candidate, errors, _warnings = validate_raw(raw, home, cfg, sources)
        if candidate:
            save_candidate(home, candidate)
            accepted.append({"id": candidate["id"], "title": candidate["title"]})
        else:
            mark_invalid(home, raw, errors)
            invalid.append({"file": path.name, "errors": errors})

    summary["accepted"] = accepted
    summary["invalid"] = invalid

    if cfg.get("decay_scan", True):
        try:
            proposals = decay.scan(home, cfg)
            summary["decay"] = len(proposals)
        except Exception as exc:  # decay must never break a mining run
            summary["decay_error"] = str(exc)

    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(
            f"\n\n--- poppy run summary ---\nsessions: {len(sessions)}\n"
            f"accepted: {json.dumps(accepted)}\ninvalid: {json.dumps(invalid)}\n"
            f"decay proposals: {summary.get('decay', 0)}\n"
        )
    return summary


def accept(
    home: Path,
    candidate_id: str,
    config: dict | None = None,
    scope: str | None = None,
    project: str | None = None,
) -> dict:
    ensure_home_layout(home)
    cfg = config or load_config(home)
    candidate = load_candidate(home, candidate_id)
    kind = str(candidate.get("kind") or "skill")

    if kind == "decay":
        raise PoppyError("decay proposals are resolved in the review UI (`poppy ui`)")
    if candidate.get("status") in ("installed", "active"):
        raise PoppyError(f"{candidate_id} is already accepted")

    if kind in ("memory", "rule"):
        entry = library.create_fact_entry(home, candidate, scope=scope, project=project)
        candidate["status"] = "active"
        candidate["entry"] = entry.id
        save_candidate(home, candidate)
        return {"status": "active", "entry": entry.to_dict(library.load_usage(home))}

    lock = acquire_lock(home, f"accept-{candidate_id}")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = home / "logs" / f"accept-{candidate_id}-{stamp}.log"
    try:
        draft_dir = drafts_candidate_dir(home, candidate_id)
        if draft_dir.exists():
            shutil.rmtree(draft_dir)
        draft_dir.mkdir(parents=True, exist_ok=True)

        candidate["status"] = "writing"
        candidate["writing_started_at"] = now_iso()
        save_candidate(home, candidate)

        prompt = render(
            "writer",
            {
                "VERSION": __version__,
                "CANDIDATE_JSON": json.dumps(candidate, indent=2),
                "EVIDENCE_BLOCK": _evidence_block(candidate),
                "DRAFT_DIR": str(draft_dir),
            },
        )
        result = run_agent(
            "writer",
            prompt,
            cfg,
            home,
            env_extra={"POPPY_DRAFT_DIR": str(draft_dir), "POPPY_CANDIDATE_ID": candidate_id},
            log_path=log_path,
        )

        reject_file = draft_dir / "REJECT.md"
        skill_file = draft_dir / "SKILL.md"
        if reject_file.is_file():
            reason = reject_file.read_text(encoding="utf-8").strip()[:500]
            candidate["status"] = "writer_rejected"
            candidate["writer_rejection"] = reason
            save_candidate(home, candidate)
            return {"status": "writer_rejected", "reason": reason, "log": str(log_path)}

        if result.returncode != 0 or not skill_file.is_file():
            candidate["status"] = "draft_failed"
            candidate["error"] = (
                f"agent exit {result.returncode}; SKILL.md {'written' if skill_file.is_file() else 'missing'}; "
                f"stderr: {tail(result.stderr, 300)}"
            )
            save_candidate(home, candidate)
            return {"status": "draft_failed", "error": candidate["error"], "log": str(log_path)}

        text = skill_file.read_text(encoding="utf-8")
        meta, _body, errors, warnings = validate_skill(text)
        candidate["draft_name"] = meta.get("name", "")
        candidate["draft_warnings"] = warnings
        if errors:
            candidate["status"] = "draft_invalid"
            candidate["draft_errors"] = errors
            save_candidate(home, candidate)
            return {"status": "draft_invalid", "errors": errors, "log": str(log_path)}

        candidate["status"] = "draft"
        candidate.pop("draft_errors", None)
        save_candidate(home, candidate)
        return {"status": "draft", "name": candidate["draft_name"], "warnings": warnings, "log": str(log_path)}
    finally:
        release_lock(lock)
