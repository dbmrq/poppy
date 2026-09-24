"""Deterministic decay: propose stale library entries for archive.

Nothing here removes anything. The scan writes proposals into the same review
queue; a human resolves each one (archive / keep / pin).
"""

from __future__ import annotations

import time
from pathlib import Path

from .candidates import candidates_dir, list_candidates, save_candidate
from .library import archive_entry, find_entry, list_entries, load_usage, set_pinned, verify_entry
from .skills import archive_skill, load_manifest
from .util import PoppyError, iso_to_epoch, load_json, now_iso, sha

DECAY_KIND = "decay"


def _last_activity(entry, usage: dict) -> float | None:
    record = usage.get(entry.id, {})
    stamps = [
        record.get("last_used"),
        entry.meta.get("last_verified"),
        entry.meta.get("created"),
    ]
    epochs = [iso_to_epoch(value) for value in stamps if value]
    epochs = [value for value in epochs if value is not None]
    return max(epochs) if epochs else None


def scan(home: Path, cfg: dict) -> list[dict]:
    usage = load_usage(home)
    limit_days = int(cfg.get("decay_after_days", 90) or 90)
    cutoff = time.time() - limit_days * 86400
    pending_targets = {
        candidate.get("target")
        for candidate in list_candidates(home)
        if candidate.get("kind") == DECAY_KIND
    }
    builtin = {
        name
        for name, entry in (load_manifest(home).get("skills") or {}).items()
        if entry.get("builtin")
    }
    proposals: list[dict] = []
    for entry in list_entries(home):
        if entry.pinned or entry.archived:
            continue
        if entry.kind == "skill" and entry.id in builtin:
            continue
        last = _last_activity(entry, usage)
        if last is None or last >= cutoff:
            continue
        if entry.id in pending_targets:
            continue
        idle_days = int((time.time() - last) / 86400)
        proposal = {
            "id": "decay-" + sha(f"{entry.kind}:{entry.id}", 10),
            "kind": DECAY_KIND,
            "status": "pending",
            "created_at": now_iso(),
            "title": f"Archive stale {entry.kind}: {entry.title}",
            "summary": f"Not used or verified in {idle_days} days (limit {limit_days}).",
            "trigger": f"Decay review for {entry.kind} {entry.id}",
            "target": entry.id,
            "entry_type": entry.kind,
            "entry_title": entry.title,
            "days_idle": idle_days,
        }
        save_candidate(home, proposal)
        proposals.append(proposal)
    return proposals


def resolve(home: Path, cfg: dict, proposal_id: str, resolution: str) -> dict:
    proposal_path = candidates_dir(home) / f"{proposal_id}.json"
    proposal = load_json(proposal_path)
    if not isinstance(proposal, dict) or proposal.get("kind") != DECAY_KIND:
        raise PoppyError(f"not a decay proposal: {proposal_id}")
    entry_id = str(proposal.get("target") or "")
    if resolution == "keep":
        verify_entry(find_entry(home, entry_id, include_archived=False))
        result = {"resolved": "keep", "entry": entry_id}
    elif resolution == "pin":
        set_pinned(find_entry(home, entry_id, include_archived=False), True)
        result = {"resolved": "pin", "entry": entry_id}
    elif resolution == "archive":
        entry = find_entry(home, entry_id, include_archived=True)
        if entry.kind == "skill":
            archive_skill(home, entry_id)
        else:
            archive_entry(home, entry)
        result = {"resolved": "archive", "entry": entry_id}
    else:
        raise PoppyError(f"unknown resolution {resolution!r} (expected archive, keep, or pin)")
    proposal_path.unlink(missing_ok=True)
    return result
