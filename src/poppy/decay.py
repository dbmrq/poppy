"""Deterministic decay: stale entries are archived automatically.

The scan archives entries nobody used or verified for `decay_after_days`
(pinned entries and builtin skills are exempt) and leaves one queue card per
archived entry. The card is the undo path: restore (bring it back and refresh
its clock) or archive (let it stay archived). Nothing is ever deleted, and a
card whose entry has already been restored elsewhere is dropped as moot.
"""

from __future__ import annotations

import time
from pathlib import Path

from .candidates import candidates_dir, list_candidates, save_candidate
from .library import (
    archive_entry,
    find_entry,
    list_entries,
    load_usage,
    restore_entry,
    verify_entry,
)
from .skills import archive_skill, load_manifest, restore_skill
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


def _open_cards(home: Path) -> dict[str, dict]:
    return {
        str(candidate.get("target")): candidate
        for candidate in list_candidates(home)
        if candidate.get("kind") == DECAY_KIND
    }


def _remove_card(home: Path, card_id: str) -> None:
    (candidates_dir(home) / f"{card_id}.json").unlink(missing_ok=True)


def scan(home: Path, cfg: dict, dry_run: bool = False) -> list[dict]:
    """Archive stale entries (unless dry_run) and return the decay cards."""
    usage = load_usage(home)
    limit_days = int(cfg.get("decay_after_days", 90) or 90)
    cutoff = time.time() - limit_days * 86400
    builtin = {
        name
        for name, entry in (load_manifest(home).get("skills") or {}).items()
        if entry.get("builtin")
    }

    # a card whose entry is no longer archived (restored by hand) is moot
    archived_ids = {
        entry.id for entry in list_entries(home, include_archived=True) if entry.archived
    }
    cards: list[dict] = []
    for target, card in _open_cards(home).items():
        if target not in archived_ids:
            if not dry_run:
                _remove_card(home, card["id"])
            continue
        cards.append(card)

    existing = {str(card.get("target")) for card in cards}
    for entry in list_entries(home):
        if entry.pinned or entry.archived or entry.id in existing:
            continue
        if entry.kind == "skill" and entry.id in builtin:
            continue
        last = _last_activity(entry, usage)
        if last is None or last >= cutoff:
            continue
        idle_days = int((time.time() - last) / 86400)
        card = {
            "id": "decay-" + sha(f"{entry.kind}:{entry.id}", 10),
            "kind": DECAY_KIND,
            "status": "pending",
            "created_at": now_iso(),
            "title": f"Auto-archived {entry.kind}: {entry.title}",
            "summary": (
                f"Unused and unverified for {idle_days} days (limit {limit_days}) — "
                "archived automatically."
            ),
            "trigger": f"Decay review for {entry.kind} {entry.id}",
            "target": entry.id,
            "entry_type": entry.kind,
            "entry_title": entry.title,
            "days_idle": idle_days,
            "archived_at": now_iso(),
        }
        if dry_run:
            cards.append(card)
            continue
        if entry.kind == "skill":
            archive_skill(home, entry.id)
        else:
            archive_entry(home, entry)
        save_candidate(home, card)
        cards.append(card)
    return cards


def resolve(home: Path, cfg: dict, card_id: str, resolution: str) -> dict:
    """Resolve a decay card: restore the entry (and refresh it) or keep archived."""
    card_path = candidates_dir(home) / f"{card_id}.json"
    card = load_json(card_path)
    if not isinstance(card, dict) or card.get("kind") != DECAY_KIND:
        raise PoppyError(f"not a decay card: {card_id}")
    entry_id = str(card.get("target") or "")
    if resolution == "restore":
        entry = find_entry(home, entry_id, include_archived=True)
        dirs: list[str] = []
        if entry.archived:
            if entry.kind == "skill":
                dirs = restore_skill(home, cfg, entry.id)
            else:
                restore_entry(home, entry)
        verify_entry(find_entry(home, entry_id, include_archived=False))
        result = {"resolved": "restore", "entry": entry_id, "dirs": dirs}
    elif resolution == "archive":
        entry = find_entry(home, entry_id, include_archived=True)
        if not entry.archived:
            if entry.kind == "skill":
                archive_skill(home, entry.id)
            else:
                archive_entry(home, entry)
        result = {"resolved": "archive", "entry": entry_id}
    else:
        raise PoppyError(f"unknown resolution {resolution!r} (expected restore or archive)")
    card_path.unlink(missing_ok=True)
    return result
