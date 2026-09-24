"""The miner-facing transcript toolbox: list, search, read, verify.

Everything a miner needs to find evidence, behind one deterministic interface.
"""

from __future__ import annotations

from .sources import Hit, Session
from .util import PoppyError, norm_ws

DEFAULT_READ_CHARS = 60_000
MAX_QUOTE_CHARS = 2_000


def collect_sessions(
    sources: list,
    since: float | None = None,
    limit: int | None = None,
    names: list[str] | None = None,
) -> list[Session]:
    out: list[Session] = []
    for source in sources:
        if names and source.name not in names:
            continue
        out.extend(source.list_sessions(since=since, limit=limit))
    out.sort(key=lambda s: s.time or 0, reverse=True)
    return out[:limit] if limit else out


def search_sessions(
    sources: list,
    pattern: str,
    since: float | None = None,
    limit: int = 100,
    names: list[str] | None = None,
    session_ids: list[str] | None = None,
) -> list[Hit]:
    hits: list[Hit] = []
    per_source = max(10, limit // max(1, len([s for s in sources if not names or s.name in names])))
    for source in sources:
        if names and source.name not in names:
            continue
        remaining = limit - len(hits)
        if remaining <= 0:
            break
        hits.extend(source.search(pattern, since=since, limit=min(per_source, remaining), session_ids=session_ids))
    hits.sort(key=lambda h: (h.session, h.line))
    return hits[:limit]


def get_session(
    sources: list,
    session_id: str,
    source_name: str | None = None,
    max_chars: int | None = None,
) -> tuple[object, str]:
    """Return (source, text). Ambiguous ids require source_name."""
    candidates = [s for s in sources if not source_name or s.name == source_name]
    matches = []
    for source in candidates:
        try:
            text = source.read_session(session_id, max_chars=max_chars or DEFAULT_READ_CHARS)
        except PoppyError:
            continue
        matches.append((source, text))
    if not matches:
        raise PoppyError(f"session not found in any source: {session_id}")
    if len(matches) > 1:
        names = ", ".join(source.name for source, _ in matches)
        raise PoppyError(f"session id {session_id} exists in multiple sources ({names}); use --source")
    return matches[0]


def verify_quote(source, session_id: str, quote: str, max_chars: int = 400_000) -> tuple[bool, str]:
    """Check that *quote* appears in the session, ignoring whitespace.

    Returns (ok, excerpt) where excerpt is context around the match, normalized.
    """
    quote = norm_ws(quote)
    if not quote:
        return False, ""
    if len(quote) > MAX_QUOTE_CHARS:
        return False, ""
    text = source.read_session(session_id, max_chars=max_chars)
    haystack = norm_ws(text)
    index = haystack.find(quote)
    if index < 0:
        return False, ""
    start = max(0, index - 300)
    end = min(len(haystack), index + len(quote) + 300)
    excerpt = haystack[start:end]
    if start > 0:
        excerpt = "…" + excerpt
    if end < len(haystack):
        excerpt = excerpt + "…"
    return True, excerpt
