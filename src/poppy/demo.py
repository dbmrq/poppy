"""Mock data behind `poppy ui --demo`: nothing is read from or written to disk.

The demo exists so the review UI can be exercised — clicked through,
screenshotted, polished — without a real Poppy home, a configured writer agent,
or any risk to the library. It covers every card and row state the real UI can
render, and actions mutate this in-memory state only.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from .util import PoppyError

DEMO_HOME = "~/.poppy  (demo — nothing is read or written)"
DEMO_SKILLS_DIRS = ["~/.config/opencode/skills", "~/.claude/skills"]
QUEUE_STATUSES = {"pending", "writing", "draft", "draft_invalid", "draft_failed", "writer_rejected"}
WRITER_DELAY = 2.5  # seconds an accepted skill spends "writing"


def _ts(days: float = 0, hours: float = 0) -> str:
    moment = datetime.now(timezone.utc) - timedelta(days=days, hours=hours)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slug(text: str, limit: int = 42) -> str:
    out = "".join(char if char.isalnum() else "-" for char in text.lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")[:limit] or "entry"


def _evidence(source: str, session: str, quote: str) -> dict:
    return {"source": source, "session": session, "quote": quote}


DRAFT_TAP_CHECK = """\
---
name: check-homebrew-tap
description: >-
  Check and refresh a Homebrew tap before debugging an install. Use when
  `brew install <formula>` says the formula does not exist, or when a formula
  that exists upstream is missing locally.
---

# Check the Homebrew tap before installing

A tap is a git clone, and `brew install` uses whatever was fetched last — so a
formula added upstream can stay invisible locally for days.

## When to use

- `brew install` reports "no available formula" for a formula you know exists.
- `brew info` suggests a formula that was renamed or removed.
- The tap was edited by hand or by tooling and brew has not noticed yet.

## Steps

1. Confirm the tap is present and current:

   ```bash
   brew tap | grep <owner>/<tap>
   brew update --quiet
   ```

2. Ask brew about the formula directly:

   ```bash
   brew info <owner>/<tap>/<formula>
   ```

3. If it is still missing, inspect the tap checkout itself:

   ```bash
   ls "$(brew --repository <owner>/<tap>)/Formula"
   ```

   A formula that is not in that directory cannot be installed, no matter
   what the remote repository shows.

## Verify

`brew info` prints the version, dependencies, and `From:` path. Only once that
resolves should you move on to debugging the formula itself.
"""


def _draft_for(candidate: dict) -> str:
    name = _slug(candidate.get("title", "draft"), 32)
    trigger = candidate.get("trigger") or "when the situation matches"
    return f"""\
---
name: {name}
description: >-
  {trigger.rstrip('.')}.
---

# {candidate.get("title", "Draft")}

{candidate.get("summary", "")}

## When to use

{trigger.rstrip('.')}.

## Steps

1. Confirm the situation matches before acting.
2. Do the smallest thing that resolves it.
3. Check the result.

## Verify

The trigger no longer applies and nothing else changed as a side effect.
"""


def _candidates() -> list[dict]:
    return [
        {
            "id": "skill-tap-check",
            "kind": "skill",
            "status": "draft",
            "title": "Check the Homebrew tap before installing",
            "trigger": "when a `brew install` cannot find a formula that exists upstream",
            "summary": (
                "A stale tap checkout is the usual reason brew cannot find a formula; check "
                "and refresh the tap before debugging the formula itself."
            ),
            "evidence": [
                _evidence(
                    "opencode",
                    "ses-2f9ab41c",
                    "brew couldn't find it because the tap checkout was stale — `brew update` fixed it.",
                ),
                _evidence(
                    "claude",
                    "ses-88c10de2",
                    "right, check `brew info <owner>/<tap>/<formula>` before assuming the formula is broken",
                ),
            ],
            "created_at": _ts(hours=3),
            "draft_preview": DRAFT_TAP_CHECK,
        },
        {
            "id": "skill-lan-map",
            "kind": "skill",
            "status": "writing",
            "title": "Map LAN services before adding a host override",
            "trigger": "when a new hostname needs a DNS rewrite",
            "summary": (
                "Scan what is actually listening before adding a rewrite, so the override "
                "points at the right host and port."
            ),
            "evidence": [
                _evidence(
                    "opencode",
                    "ses-5b2c77aa",
                    "I keep adding rewrites for ports nothing listens on — scan first.",
                ),
            ],
            "created_at": _ts(days=1),
        },
        {
            "id": "skill-verify-backup",
            "kind": "skill",
            "status": "pending",
            "title": "Verify the last backup run before reporting success",
            "trigger": "when a scheduled backup job finishes",
            "summary": (
                "A job exiting 0 does not mean the backup landed; check the newest snapshot "
                "and its size before reporting success."
            ),
            "evidence": [
                _evidence(
                    "opencode",
                    "ses-1c9e3d77",
                    "the timer said success but the repo hadn't changed in nine days",
                ),
                _evidence(
                    "opencode",
                    "ses-1c9e3d77",
                    "add a check that the snapshot timestamp is today before the email goes out",
                ),
                _evidence(
                    "claude",
                    "ses-44a0b1f2",
                    "same thing happened to me — exit code was 0 because the wrapper swallowed it",
                ),
            ],
            "warnings": ["the same session provides most of the evidence for this candidate"],
            "created_at": _ts(hours=9),
        },
        {
            "id": "skill-ci-triage",
            "kind": "skill",
            "status": "draft_failed",
            "title": "Triage failing CI before pushing again",
            "trigger": "when a push turns CI red",
            "summary": (
                "Read the failing job's log to the first error instead of re-pushing and "
                "hoping the flake clears."
            ),
            "evidence": [
                _evidence(
                    "opencode",
                    "ses-77d0aa19",
                    "two pushes later I finally opened the log — it was a lint error the whole time",
                ),
                _evidence(
                    "cursor",
                    "ses-02fe8811",
                    "the failure was in the second job; the first one was just cancelled",
                ),
            ],
            "error": "writer agent exited 1: `opencode run` timed out after 600s",
            "created_at": _ts(days=1, hours=4),
        },
        {
            "id": "skill-import-check",
            "kind": "skill",
            "status": "draft_invalid",
            "title": "Check the import mode before running a media import",
            "trigger": "when importing manually downloaded media",
            "summary": (
                "Match the import mode to what you want to happen to the source files: "
                "move relocates, copy keeps the download folder intact."
            ),
            "evidence": [
                _evidence(
                    "opencode",
                    "ses-9a1bf203",
                    "I used the default and it emptied the download folder",
                ),
            ],
            "draft_errors": [
                "frontmatter: missing description",
                "empty body",
            ],
            "created_at": _ts(days=2),
        },
        {
            "id": "skill-writer-declined",
            "kind": "skill",
            "status": "writer_rejected",
            "title": "Keep the download folder when re-importing",
            "trigger": "when re-running an import over an existing library",
            "summary": "Re-imports should never empty the source folder.",
            "evidence": [
                _evidence(
                    "opencode",
                    "ses-9a1bf203",
                    "the folder was empty after the scan, which was not what I wanted",
                ),
            ],
            "writer_rejection": (
                "duplicates the installed skill `sonarr-import` once the import-mode fix is "
                "in place; no new draft needed"
            ),
            "created_at": _ts(days=2, hours=6),
        },
        {
            "id": "memory-lean-deps",
            "kind": "memory",
            "status": "pending",
            "title": "Prefers minimal dependencies",
            "trigger": "when choosing libraries or tools",
            "summary": (
                "Prefer stdlib or already-installed tooling; a new dependency needs a real "
                "reason and should be easy to remove."
            ),
            "scope": "user",
            "evidence": [
                _evidence("opencode", "ses-3e8d12c4", "I'd rather keep it dependency-free than pull in a library"),
                _evidence("claude", "ses-10bb7742", "no external deps, please — stdlib is fine"),
            ],
            "created_at": _ts(hours=6),
        },
        {
            "id": "memory-project-layout",
            "kind": "memory",
            "status": "pending",
            "title": "Melvil generates its Xcode project from project.yml",
            "trigger": "when changing project settings in Melvil",
            "summary": (
                "Edit project.yml and regenerate with XcodeGen; never hand-edit the "
                ".xcodeproj, it is generated."
            ),
            "scope": "project",
            "project": "/Users/daniel/Projects/Melvil",
            "evidence": [
                _evidence(
                    "opencode",
                    "ses-6f01cc88",
                    "the setting kept disappearing because it was regenerated from the spec",
                ),
            ],
            "created_at": _ts(hours=20),
        },
        {
            "id": "rule-no-force-push",
            "kind": "rule",
            "status": "pending",
            "title": "Never force-push a shared branch",
            "trigger": "when a push is rejected as non-fast-forward",
            "summary": (
                "Pull with rebase instead; force-pushing rewrites history other people "
                "already have."
            ),
            "evidence": [
                _evidence(
                    "opencode",
                    "ses-0d5be441",
                    "don't force-push that branch, my clone is based on it",
                ),
                _evidence(
                    "claude",
                    "ses-91c2aa30",
                    "that needed a rebase, not a force-push",
                ),
            ],
            "created_at": _ts(days=1, hours=2),
        },
        {
            "id": "decay-4f2a91c3d8",
            "kind": "decay",
            "status": "pending",
            "title": "Auto-archived memory: OpenRouter spend tuning",
            "summary": "Unused and unverified for 97 days (limit 90) — archived automatically.",
            "trigger": "Decay review for memory memory-openrouter-spend",
            "target": "memory-openrouter-spend",
            "entry_type": "memory",
            "entry_title": "OpenRouter spend tuning",
            "days_idle": 97,
            "archived_at": _ts(days=3),
            "created_at": _ts(days=3),
        },
    ]


def _entry(
    kind: str,
    entry_id: str,
    title: str,
    scope: str = "user",
    *,
    project: str | None = None,
    machine: str | None = None,
    pinned: bool = False,
    uses: int = 0,
    last_used: str | None = None,
    last_verified: str | None = None,
    created: str | None = None,
    archived_at: str | None = None,
    body: str = "",
) -> dict:
    return {
        "kind": kind,
        "id": entry_id,
        "title": title,
        "scope": scope,
        "project": project,
        "machine": machine,
        "status": "archived" if archived_at else "active",
        "pinned": pinned,
        "created": created or _ts(days=30),
        "last_verified": last_verified or _ts(days=1),
        "archived_at": archived_at,
        "source_candidate": None,
        "last_used": last_used,
        "uses": uses,
        "archived": bool(archived_at),
        "path": "(demo)",
        "body": body,
    }


def _library() -> list[dict]:
    return [
        _entry(
            "skill",
            "backup-restore-drill",
            "Rehearse a restore before trusting a backup",
            uses=12,
            body="A backup you have never restored is a hope, not a backup. Stage the "
            "newest snapshot into a scratch directory, check the required paths exist, "
            "and only then trust the schedule.",
            last_used=_ts(days=1),
            last_verified=_ts(days=1),
            created=_ts(days=40),
        ),
        _entry(
            "skill",
            "sonarr-import",
            "Import manual TV downloads with sonarr-import",
            "machine",
            machine="eq12",
            pinned=True,
            uses=7,
            last_used=_ts(days=3),
            last_verified=_ts(days=10),
            created=_ts(days=60),
        ),
        _entry(
            "skill",
            "xcodegen-bump",
            "Bump the XcodeGen spec before regenerating",
            "project",
            project="/Users/daniel/Projects/Melvil",
            uses=3,
            last_used=_ts(days=12),
            last_verified=_ts(days=20),
            created=_ts(days=25),
        ),
        _entry(
            "memory",
            "memory-vault-creds",
            "Keeps credentials in the vault, never in dotfiles",
            pinned=True,
            body="Service secrets live in the password manager; repositories and dotfiles "
            "carry only references like `os.environ/KEY`.",
            uses=9,
            last_used=_ts(days=2),
            last_verified=_ts(days=2),
            created=_ts(days=50),
        ),
        _entry(
            "memory",
            "memory-lan-cidr",
            "LAN is 10.0.0.0/22 with /24-looking host addresses",
            "machine",
            body="The LAN is 10.0.0.0/22 (mask 255.255.252.0), so valid host addresses "
            "span 10.0.0.* through 10.0.3.*.",
            machine="eq12",
            uses=5,
            last_used=_ts(days=6),
            last_verified=_ts(days=6),
            created=_ts(days=45),
        ),
        _entry(
            "memory",
            "memory-brew-prefix",
            "Homebrew lives at /opt/homebrew on the MacBook",
            "machine",
            body="Non-interactive shells do not pick up the login PATH; call "
            "/opt/homebrew/bin/brew (or zsh -lc) on the MacBook.",
            machine="macbook",
            uses=2,
            last_used=_ts(days=15),
            last_verified=_ts(days=15),
            created=_ts(days=20),
        ),
        _entry(
            "memory",
            "memory-melvil-spec",
            "Melvil generates its project from project.yml",
            "project",
            project="/Users/daniel/Projects/Melvil",
            uses=4,
            last_used=_ts(days=4),
            last_verified=_ts(days=4),
            created=_ts(days=24),
        ),
        _entry(
            "rule",
            "rule-no-force-push",
            "Never force-push shared branches",
            uses=6,
            body="Never force-push a branch someone else may have cloned; rebase and push "
            "normally instead.",
            last_used=_ts(days=8),
            last_verified=_ts(days=8),
            created=_ts(days=55),
        ),
        _entry(
            "rule",
            "rule-commit-and-push",
            "Commit and push finished work",
            pinned=True,
            body="Finish work by committing and pushing it; do not leave completed work "
            "uncommitted or local-only.",
            uses=21,
            last_used=_ts(hours=5),
            last_verified=_ts(hours=5),
            created=_ts(days=70),
        ),
    ]


def _archived() -> list[dict]:
    return [
        _entry(
            "skill",
            "keep-filebrowser-mounts",
            "Keep FileBrowser mounts in sync",
            archived_at=_ts(days=30),
            uses=0,
            last_used=_ts(days=120),
            last_verified=_ts(days=95),
            created=_ts(days=200),
        ),
        _entry(
            "memory",
            "memory-openrouter-spend",
            "OpenRouter spend tuning",
            "machine",
            machine="eq12",
            archived_at=_ts(days=3),
            uses=0,
            last_used=_ts(days=97),
            last_verified=_ts(days=97),
            created=_ts(days=140),
            body="Trim the OpenRouter fallback list to the models that actually get used.",
        ),
        _entry(
            "memory",
            "route-agents-through-litellm",
            "Route coding agents through the LiteLLM gateway",
            "machine",
            machine="eq12",
            archived_at=_ts(days=8),
            uses=0,
            last_used=_ts(days=64),
            last_verified=_ts(days=64),
            created=_ts(days=90),
        ),
    ]


class DemoBackend:
    """In-memory stand-in for the disk-backed review backend."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self.candidates = _candidates()
        self._initial_writing = {c["id"] for c in self.candidates if c.get("status") == "writing"}
        self.library = _library()
        self.archived = _archived()

    # -- state -----------------------------------------------------------------
    def _settle_writer(self) -> None:
        """Let the candidate that started out 'writing' turn into a draft."""
        if time.monotonic() - self._started < 6:
            return
        for candidate in self.candidates:
            if candidate["id"] in self._initial_writing and candidate.get("status") == "writing":
                self._draftify(candidate)

    def _draftify(self, candidate: dict) -> None:
        candidate["status"] = "draft"
        candidate["draft_preview"] = _draft_for(candidate)
        for key in ("error", "draft_errors", "writer_rejection"):
            candidate.pop(key, None)

    def state(self) -> dict:
        with self._lock:
            self._settle_writer()
            grouped: dict[str, list] = {"skill": [], "memory": [], "rule": []}
            for entry in self.library:
                grouped[entry["kind"]].append(dict(entry))
            return {
                "candidates": [dict(c) for c in self.candidates if c["status"] in QUEUE_STATUSES],
                "library": grouped,
                "archived": [dict(e) for e in self.archived],
                "skills_dirs": DEMO_SKILLS_DIRS,
                "home": DEMO_HOME,
                "demo": True,
            }

    # -- actions ---------------------------------------------------------------
    def action(self, action: str, payload: dict) -> dict:
        with self._lock:
            if action == "accept":
                return self._accept(payload)
            if action == "reject":
                self._candidate(str(payload.get("id", "")))["status"] = "rejected"
                return {"ok": True}
            if action == "discard_draft":
                candidate = self._candidate(str(payload.get("id", "")))
                candidate["status"] = "pending"
                for key in (
                    "draft_errors",
                    "draft_warnings",
                    "draft_name",
                    "error",
                    "writer_rejection",
                    "draft_preview",
                ):
                    candidate.pop(key, None)
                return {"ok": True, "id": candidate["id"], "status": "pending"}
            if action == "install":
                candidate = self._candidate(str(payload.get("id", "")))
                if candidate.get("status") != "draft":
                    raise PoppyError("candidate has no validated draft yet")
                candidate["status"] = "installed"
                entry = _entry("skill", _slug(candidate["title"]), candidate["title"], last_verified=_ts())
                self.library.append(entry)
                return {"ok": True, "name": entry["id"], "dirs": DEMO_SKILLS_DIRS}
            if action == "uninstall" or action == "entry_archive":
                entry = self._entry(str(payload.get("id", "")))
                if entry not in self.archived:
                    entry["archived"] = True
                    entry["status"] = "archived"
                    entry["archived_at"] = _ts()
                    self.library.remove(entry)
                    self.archived.append(entry)
                return {"ok": True}
            if action == "entry_restore":
                entry = self._entry(str(payload.get("id", "")))
                if entry in self.archived:
                    entry["archived"] = False
                    entry["status"] = "active"
                    entry["archived_at"] = None
                    entry["last_verified"] = _ts()  # restoring refreshes the decay clock
                    self.archived.remove(entry)
                    self.library.append(entry)
                return {"ok": True}
            if action == "entry_pin":
                self._entry(str(payload.get("id", "")))["pinned"] = bool(payload.get("pinned", True))
                return {"ok": True}
            if action == "entry_verify":
                self._entry(str(payload.get("id", "")))["last_verified"] = _ts()
                return {"ok": True}
            if action == "resolve_decay":
                candidate = self._candidate(str(payload.get("id", "")))
                candidate["status"] = "resolved"
                resolution = str(payload.get("resolution", ""))
                entry = next(
                    (e for e in self.library + self.archived if e["id"] == candidate.get("target")), None
                )
                if entry is not None:
                    if resolution == "restore":
                        if entry in self.archived:
                            entry["archived"] = False
                            entry["status"] = "active"
                            entry["archived_at"] = None
                            self.archived.remove(entry)
                            self.library.append(entry)
                        entry["last_verified"] = _ts()
                    elif resolution == "archive" and entry not in self.archived:
                        entry["archived"] = True
                        entry["status"] = "archived"
                        entry["archived_at"] = _ts()
                        self.library.remove(entry)
                        self.archived.append(entry)
                return {"ok": True, "resolved": resolution}
            raise PoppyError(f"unknown action: {action}")

    def _accept(self, payload: dict) -> dict:
        candidate = self._candidate(str(payload.get("id", "")))
        kind = str(candidate.get("kind") or "skill")
        if kind in ("memory", "rule"):
            scope = str(payload.get("scope") or candidate.get("scope") or "user")
            project = payload.get("project") if scope == "project" else None
            entry = _entry(
                kind,
                f"{kind}-{_slug(candidate['title'], 30)}",
                candidate["title"],
                scope,
                project=project,
                last_verified=_ts(),
                body=candidate.get("summary", ""),
            )
            self.library.append(entry)
            candidate["status"] = "active"
            return {"ok": True, "status": "active", "entry": entry}
        if candidate.get("status") == "writing":
            raise PoppyError("a writer run is already in progress for this candidate")
        if "instructions" in payload:  # reviewer steering; empty clears a stored note
            steering = str(payload.get("instructions") or "").strip()
            if steering:
                candidate["writer_instructions"] = steering[:1000]
            else:
                candidate.pop("writer_instructions", None)
        candidate["status"] = "writing"
        for key in ("error", "draft_errors", "writer_rejection"):
            candidate.pop(key, None)
        timer = threading.Timer(WRITER_DELAY, self._finish_draft, args=(candidate["id"],))
        timer.daemon = True
        timer.start()
        return {"ok": True, "status": "writing"}

    def _finish_draft(self, candidate_id: str) -> None:
        with self._lock:
            candidate = next((c for c in self.candidates if c["id"] == candidate_id), None)
            if candidate is not None and candidate.get("status") == "writing":
                self._draftify(candidate)

    def _candidate(self, candidate_id: str) -> dict:
        for candidate in self.candidates:
            if candidate["id"] == candidate_id:
                return candidate
        raise PoppyError(f"unknown candidate: {candidate_id}")

    def _entry(self, entry_id: str) -> dict:
        for entry in self.library + self.archived:
            if entry["id"] == entry_id:
                return entry
        raise PoppyError(f"unknown entry: {entry_id}")
