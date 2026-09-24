"""Mid-session proposals: queue a candidate the moment it is learned.

The interactive agent supplies the judgment (what is worth keeping) and the
verbatim quotes; Poppy locates each quote in the recent sessions, runs the same
validation as the miner (schema, evidence, secrets, dedupe), and drops the
candidate into the same review queue. Nothing is promoted here: the user still
decides.
"""

from __future__ import annotations

import time
from pathlib import Path

from .candidates import save_candidate, validate_raw
from .sessions import collect_sessions, verify_quote
from .sources import load_sources
from .util import PoppyError

LOCATE_LIMIT = 100


def locate_quote(sources: list, cfg: dict, quote: str) -> tuple[str, str, str] | None:
    """Find the newest recent session containing *quote*.

    Returns (source name, session id, excerpt), or None when the quote is not
    found in the bounded search window.
    """
    window = float(cfg.get("lookback_days", 14) or 14) * 86400
    sessions = collect_sessions(sources, since=time.time() - window, limit=LOCATE_LIMIT)
    by_name = {source.name: source for source in sources}
    for session in sessions:
        source = by_name.get(session.source)
        if source is None:
            continue
        try:
            ok, excerpt = verify_quote(source, session.id, quote)
        except PoppyError:
            continue
        if ok:
            return session.source, session.id, excerpt
    return None


def propose(home: Path, cfg: dict, payload: dict) -> dict:
    """Validate and queue one proposed candidate. Returns a result dict."""
    sources = load_sources(home)
    if not sources:
        raise PoppyError("no sources configured — run the installer prompt or `poppy sources add`")
    if not isinstance(payload, dict):
        raise PoppyError("candidate must be a JSON object")
    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise PoppyError("candidate needs an evidence list with verbatim quotes")

    located: list[dict] = []
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise PoppyError(f"evidence[{index}] is not an object")
        quote = str(item.get("quote") or "").strip()
        if not quote:
            raise PoppyError(f"evidence[{index}] needs a quote")
        if item.get("source") and item.get("session"):
            located.append({"source": item["source"], "session": item["session"], "quote": quote})
            continue
        found = locate_quote(sources, cfg, quote)
        if not found:
            raise PoppyError(
                f"evidence[{index}]: quote not found in the last {LOCATE_LIMIT} sessions "
                "(or pass explicit source/session)"
            )
        source_name, session_id, _excerpt = found
        located.append({"source": source_name, "session": session_id, "quote": quote})

    raw = {**payload, "evidence": located}
    candidate, errors, warnings = validate_raw(raw, home, cfg, sources)
    if not candidate:
        raise PoppyError("; ".join(errors))
    save_candidate(home, candidate)
    return {
        "queued": True,
        "id": candidate["id"],
        "kind": candidate["kind"],
        "title": candidate["title"],
        "scope": candidate.get("scope"),
        "warnings": warnings,
        "review": "poppy ui",
    }
