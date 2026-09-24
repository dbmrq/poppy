"""Candidate schema, validation, dedupe, and storage."""

from __future__ import annotations

import difflib
from pathlib import Path

from .sessions import verify_quote
from .util import PoppyError, load_json, norm_ws, now_iso, save_json, scan_secrets, sha

KIND_VALUES = ("skill", "memory", "rule")
SCOPE_VALUES = ("user", "machine", "project", "task")

TITLE_MAX = 100
SUMMARY_MAX = 2000
TRIGGER_MAX = 500
QUOTE_MIN = 16
QUOTE_MAX = 2000

PENDING_STATUSES = ("pending", "writing", "draft_invalid", "draft_failed", "writer_rejected", "draft")


def candidates_dir(home: Path) -> Path:
    return home / "candidates"


def drafts_dir(home: Path) -> Path:
    return home / "drafts"


def drafts_candidate_dir(home: Path, cid: str) -> Path:
    return drafts_dir(home) / cid


def rejected_dir(home: Path) -> Path:
    return home / "rejected"


def title_key(title: str) -> str:
    return sha(norm_ws(title).lower(), 16)


def candidate_id(title: str, summary: str) -> str:
    return sha(norm_ws(title).lower() + "\n" + norm_ws(summary).lower())


def load_candidate(home: Path, cid: str) -> dict:
    data = load_json(candidates_dir(home) / f"{cid}.json")
    if data is None:
        raise PoppyError(f"unknown candidate: {cid}")
    if isinstance(data, dict):
        data.setdefault("kind", "skill")
    return data


def save_candidate(home: Path, cand: dict) -> None:
    save_json(candidates_dir(home) / f"{cand['id']}.json", cand)


def list_candidates(home: Path, statuses: tuple[str, ...] | None = None) -> list[dict]:
    out = []
    for path in sorted(candidates_dir(home).glob("*.json")):
        data = load_json(path)
        if not isinstance(data, dict):
            continue
        data.setdefault("kind", "skill")
        if statuses and data.get("status") not in statuses:
            continue
        out.append(data)
    out.sort(key=lambda c: c.get("created_at", ""), reverse=True)
    return out


def rejected_index_path(home: Path) -> Path:
    return rejected_dir(home) / "index.json"


def load_rejection_index(home: Path) -> dict:
    return load_json(rejected_index_path(home), {}) or {}


def rejection_reason(home: Path, title: str) -> str | None:
    entry = load_rejection_index(home).get(title_key(title))
    return (entry or {}).get("reason")


def mark_rejected(home: Path, cand: dict, reason: str, stage: str = "user") -> None:
    at = now_iso()
    index = load_rejection_index(home)
    index[title_key(cand["title"])] = {
        "id": cand["id"],
        "title": cand["title"],
        "reason": reason,
        "at": at,
        "stage": stage,
    }
    save_json(rejected_index_path(home), index)
    rejected = dict(cand)
    rejected["status"] = "rejected"
    rejected["rejection"] = {"reason": reason, "at": at, "stage": stage}
    save_json(rejected_dir(home) / f"{cand['id']}.json", rejected)
    path = candidates_dir(home) / f"{cand['id']}.json"
    if path.exists():
        path.unlink()


def mark_invalid(home: Path, raw, errors: list[str], stage: str = "inbox") -> Path:
    payload = {"stage": stage, "at": now_iso(), "errors": errors, "raw": raw}
    path = rejected_dir(home) / f"invalid-{sha(str(raw))}.json"
    save_json(path, payload)
    return path


def similar_candidate(home: Path, title: str, threshold: float = 0.87) -> dict | None:
    target = norm_ws(title).lower()
    for cand in list_candidates(home):
        other = norm_ws(cand.get("title", "")).lower()
        if other and difflib.SequenceMatcher(None, target, other).ratio() >= threshold:
            return cand
    return None


def similar_installed(home: Path, title: str, threshold: float = 0.8) -> str | None:
    from .skills import load_manifest

    target = norm_ws(title).lower()
    for name in (load_manifest(home).get("skills") or {}):
        other = name.replace("-", " ")
        if difflib.SequenceMatcher(None, target, other).ratio() >= threshold:
            return name
    return None


def similar_library(home: Path, title: str, kind: str, threshold: float = 0.87) -> str | None:
    if kind not in ("memory", "rule"):
        return None
    from .library import list_entries

    target = norm_ws(title).lower()
    for entry in list_entries(home, kinds=(kind,)):
        other = norm_ws(entry.title).lower()
        if other and difflib.SequenceMatcher(None, target, other).ratio() >= threshold:
            return entry.title
    return None


def validate_raw(raw, home: Path, cfg: dict, sources: list) -> tuple[dict | None, list[str], list[str]]:
    """Validate one raw candidate. Returns (candidate, errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(raw, dict):
        return None, ["candidate is not a JSON object"], warnings

    def field(name: str, max_len: int) -> str:
        value = raw.get(name)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"missing or empty field: {name}")
            return ""
        value = value.strip()
        if len(value) > max_len:
            errors.append(f"field {name} is longer than {max_len} chars")
        return value

    title = field("title", TITLE_MAX)
    summary = field("summary", SUMMARY_MAX)
    trigger = field("trigger", TRIGGER_MAX)

    kind = str(raw.get("kind") or "skill").strip().lower()
    if kind not in KIND_VALUES:
        errors.append(f"unknown kind {kind!r} (expected one of {', '.join(KIND_VALUES)})")
        kind = "skill"
    scope = str(raw.get("scope") or "user").strip().lower()
    project = str(raw.get("project") or "").strip()
    if kind in ("memory", "rule"):
        if scope not in SCOPE_VALUES:
            errors.append(f"invalid scope {scope!r} (expected one of {', '.join(SCOPE_VALUES)})")
        elif scope == "project" and not project:
            warnings.append("project scope without a project path; defaulting to user scope")
            scope = "user"

    evidence = raw.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append("missing evidence list")
        evidence = []
    min_evidence = int(cfg.get("min_evidence", 1) or 1)
    if isinstance(evidence, list) and len(evidence) < min_evidence:
        errors.append(f"at least {min_evidence} evidence item(s) required")

    source_map = {source.name: source for source in sources}
    verified = []
    for index, item in enumerate(evidence if isinstance(evidence, list) else []):
        label = f"evidence[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label}: not an object")
            continue
        source_name = str(item.get("source", "")).strip()
        session = str(item.get("session", "")).strip()
        quote = str(item.get("quote", "")).strip()
        if not source_name or source_name not in source_map:
            errors.append(f"{label}: unknown source {source_name!r}")
            continue
        if not session:
            errors.append(f"{label}: missing session")
            continue
        if len(quote) < QUOTE_MIN:
            errors.append(f"{label}: quote too short to verify (min {QUOTE_MIN} chars)")
            continue
        if len(quote) > QUOTE_MAX:
            errors.append(f"{label}: quote too long (max {QUOTE_MAX} chars)")
            continue
        try:
            ok, excerpt = verify_quote(source_map[source_name], session, quote)
        except PoppyError as exc:
            errors.append(f"{label}: {exc}")
            continue
        if not ok:
            errors.append(
                f"{label}: quote not found in {source_name}:{session} "
                "(unknown session, or the text no longer matches)"
            )
            continue
        verified.append({"source": source_name, "session": session, "quote": quote, "excerpt": excerpt})

    if errors:
        return None, errors, warnings

    for severity, label, _snippet in scan_secrets(title, summary, trigger, *[v["quote"] for v in verified]):
        if severity == "high":
            errors.append(f"secret detected: {label}")
        else:
            warnings.append(f"possible secret: {label}")
    if errors:
        return None, errors, warnings

    cid = candidate_id(title, summary)
    if (candidates_dir(home) / f"{cid}.json").exists():
        return None, ["duplicate of an existing candidate"], warnings
    reason = rejection_reason(home, title)
    if reason:
        return None, [f"previously rejected: {reason}"], warnings
    similar = similar_candidate(home, title)
    if similar:
        return None, [f"similar to existing candidate: {similar.get('title')!r}"], warnings
    installed = similar_installed(home, title)
    if installed:
        return None, [f"similar to installed skill: {installed}"], warnings
    library_similar = similar_library(home, title, kind)
    if library_similar:
        return None, [f"similar to existing library entry: {library_similar!r}"], warnings

    candidate = {
        "id": cid,
        "kind": kind,
        "status": "pending",
        "created_at": now_iso(),
        "title": title,
        "summary": summary,
        "trigger": trigger,
        "evidence": verified,
        "warnings": warnings,
    }
    if kind in ("memory", "rule"):
        candidate["scope"] = scope
        if project:
            candidate["project"] = project
    return candidate, [], warnings
