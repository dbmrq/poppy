"""Poppy's canonical library: approved skills, memories, and rules.

Everything Poppy manages lives under ``~/.poppy/library``. Harness copies are
derived mirrors. The library is the thing worth backing up or syncing; it is
never mixed into user skill directories or user context files.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .frontmatter import dump_frontmatter, parse_frontmatter
from .util import (
    REPO_ROOT,
    PoppyError,
    atomic_write_text,
    load_json,
    now_iso,
    save_json,
    sha,
)

KINDS = ("skill", "memory", "rule")
SCOPES = ("user", "machine", "project", "task")
SCOPE_RANK = {"task": 0, "project": 1, "machine": 2, "user": 3}

META_ORDER = [
    "id",
    "kind",
    "title",
    "scope",
    "project",
    "machine",
    "status",
    "pinned",
    "created",
    "last_verified",
    "archived_at",
    "source_candidate",
    "trigger",
]

BUILTIN_DIR = REPO_ROOT / "builtin"


@dataclass
class Entry:
    kind: str
    id: str
    path: Path
    meta: dict
    body: str
    archived: bool = False

    @property
    def title(self) -> str:
        return str(self.meta.get("title") or self.meta.get("name") or self.id)

    @property
    def scope(self) -> str:
        return str(self.meta.get("scope") or "user")

    @property
    def status(self) -> str:
        return str(self.meta.get("status") or "active")

    @property
    def pinned(self) -> bool:
        return str(self.meta.get("pinned", "false")).lower() in ("true", "1", "yes")

    def to_dict(self, usage: dict | None = None) -> dict:
        record = (usage or {}).get(self.id, {})
        return {
            "kind": self.kind,
            "id": self.id,
            "title": self.title,
            "scope": self.scope,
            "project": self.meta.get("project"),
            "machine": self.meta.get("machine"),
            "status": self.status,
            "pinned": self.pinned,
            "created": self.meta.get("created"),
            "last_verified": self.meta.get("last_verified"),
            "archived_at": self.meta.get("archived_at"),
            "source_candidate": self.meta.get("source_candidate"),
            "last_used": record.get("last_used"),
            "uses": int(record.get("uses", 0)),
            "archived": self.archived,
            "path": str(self.path),
            "body": self.body,
        }


# --------------------------------------------------------------------------- paths


def library_dir(home: Path) -> Path:
    return home / "library"


def skills_dir(home: Path) -> Path:
    return library_dir(home) / "skills"


def memory_dir(home: Path) -> Path:
    return library_dir(home) / "memory"


def rules_dir(home: Path) -> Path:
    return library_dir(home) / "rules"


def archive_dir(home: Path) -> Path:
    return library_dir(home) / "archive"


def state_dir(home: Path) -> Path:
    return home / "state"


def ensure_library(home: Path) -> None:
    for directory in (
        skills_dir(home),
        memory_dir(home),
        rules_dir(home),
        archive_dir(home),
        state_dir(home),
    ):
        directory.mkdir(parents=True, exist_ok=True)


def host_name() -> str:
    return socket.gethostname().split(".")[0]


# --------------------------------------------------------------------------- entries


def read_entry(path: Path, kind: str, archived: bool = False) -> Entry:
    text = path.read_text(encoding="utf-8")
    meta, body, errors = parse_frontmatter(text)
    if errors:
        raise PoppyError(f"cannot parse {path}: {'; '.join(errors)}")
    entry_id = str(meta.get("id") or (path.parent.name if kind == "skill" else path.stem))
    return Entry(kind=kind, id=entry_id, path=path, meta=meta, body=body, archived=archived)


def write_entry(entry: Entry) -> None:
    atomic_write_text(entry.path, dump_frontmatter(entry.meta, entry.body, META_ORDER))


def list_entries(home: Path, kinds=None, include_archived: bool = False) -> list[Entry]:
    wanted = tuple(kinds) if kinds else KINDS
    entries: list[Entry] = []
    if "skill" in wanted:
        for skill_md in sorted(skills_dir(home).glob("*/SKILL.md")):
            entries.append(read_entry(skill_md, "skill"))
    for kind, root in (("memory", memory_dir(home)), ("rule", rules_dir(home))):
        if kind in wanted:
            for path in sorted(root.glob("*/*.md")):
                entries.append(read_entry(path, kind))
    if include_archived:
        if "skill" in wanted:
            for skill_md in sorted((archive_dir(home) / "skills").glob("*/SKILL.md")):
                entries.append(read_entry(skill_md, "skill", archived=True))
        for kind, sub in (("memory", "memory"), ("rule", "rules")):
            if kind in wanted:
                for path in sorted((archive_dir(home) / sub).glob("*.md")):
                    entries.append(read_entry(path, kind, archived=True))
    return entries


def find_entry(home: Path, entry_id: str, include_archived: bool = True) -> Entry:
    """Find an entry by full id, id prefix, or unique hash-prefix (>= 4 chars)."""
    entries = list_entries(home, include_archived=include_archived)
    for entry in entries:
        if entry.id == entry_id:
            return entry
    if len(entry_id) >= 4:
        matches = [
            entry
            for entry in entries
            if entry.id.startswith(entry_id) or entry.id.split("-")[-1].startswith(entry_id)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = ", ".join(entry.id for entry in matches)
            raise PoppyError(f"ambiguous entry id {entry_id!r}: matches {names}")
    raise PoppyError(f"unknown entry: {entry_id}")


def update_entry(entry: Entry, **changes) -> Entry:
    for key, value in changes.items():
        if value is not None:
            entry.meta[key] = value
    if not entry.archived:
        write_entry(entry)
    return entry


def verify_entry(entry: Entry) -> Entry:
    return update_entry(entry, last_verified=now_iso())


def set_pinned(entry: Entry, pinned: bool) -> Entry:
    return update_entry(entry, pinned="true" if pinned else "false")


def create_fact_entry(home: Path, candidate: dict, scope: str | None = None, project: str | None = None) -> Entry:
    kind = str(candidate.get("kind") or "memory")
    if kind not in ("memory", "rule"):
        raise PoppyError(f"not a fact kind: {kind}")
    scope = str(scope or candidate.get("scope") or "user").strip()
    if scope not in SCOPES:
        raise PoppyError(f"invalid scope {scope!r} (expected one of {', '.join(SCOPES)})")
    entry_id = ("mem-" if kind == "memory" else "rule-") + sha(f"{kind}:{candidate['id']}", 10)
    meta = {
        "id": entry_id,
        "kind": kind,
        "title": str(candidate.get("title", "")).strip(),
        "scope": scope,
        "status": "active",
        "pinned": "false",
        "created": now_iso(),
        "last_verified": now_iso(),
        "source_candidate": candidate["id"],
    }
    trigger = str(candidate.get("trigger", "")).strip()
    if trigger:
        meta["trigger"] = trigger
    if scope == "machine":
        meta["machine"] = host_name()
    if scope == "project":
        project = str(project or candidate.get("project") or "").strip()
        if not project:
            raise PoppyError("project scope requires a project path")
        meta["project"] = project
    entry = Entry(kind=kind, id=entry_id, path=_fact_path(home, kind, scope, entry_id), meta=meta, body=fact_body(candidate))
    entry.path.parent.mkdir(parents=True, exist_ok=True)
    write_entry(entry)
    return entry


def _fact_path(home: Path, kind: str, scope: str, entry_id: str) -> Path:
    root = memory_dir(home) if kind == "memory" else rules_dir(home)
    return root / scope / f"{entry_id}.md"


def fact_body(candidate: dict) -> str:
    parts = [str(candidate.get("summary", "")).strip()]
    trigger = str(candidate.get("trigger", "")).strip()
    if trigger:
        parts.append(f"Applies when: {trigger}")
    evidence = candidate.get("evidence") or []
    if evidence:
        lines = ["## Evidence"]
        for item in evidence:
            lines.append(f"- {item.get('source')}:{item.get('session')} — “{item.get('quote')}”")
        parts.append("\n".join(lines))
    return "\n\n".join(part for part in parts if part)


def archive_entry(home: Path, entry: Entry) -> Entry:
    if entry.archived:
        return entry
    if entry.kind == "skill":
        destination = archive_dir(home) / "skills" / entry.id
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(entry.path.parent), str(destination))
        entry.path = destination / "SKILL.md"
    else:
        entry.meta.update({"status": "archived", "archived_at": now_iso()})
        sub = "memory" if entry.kind == "memory" else "rules"
        destination = archive_dir(home) / sub / f"{entry.id}.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(destination, dump_frontmatter(entry.meta, entry.body, META_ORDER))
        entry.path.unlink(missing_ok=True)
        entry.path = destination
    entry.archived = True
    return entry


def restore_entry(home: Path, entry: Entry) -> Entry:
    if not entry.archived:
        return entry
    if entry.kind == "skill":
        raise PoppyError("restore skills through skills.restore_skill")
    entry.meta.pop("archived_at", None)
    entry.meta["status"] = "active"
    destination = _fact_path(home, entry.kind, entry.scope, entry.id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(destination, dump_frontmatter(entry.meta, entry.body, META_ORDER))
    entry.path.unlink(missing_ok=True)
    entry.path = destination
    entry.archived = False
    return entry


# --------------------------------------------------------------------------- usage


def usage_path(home: Path) -> Path:
    return state_dir(home) / "usage.json"


def load_usage(home: Path) -> dict:
    return load_json(usage_path(home), {}) or {}


def touch_usage(home: Path, entry_ids: list[str]) -> None:
    if not entry_ids:
        return
    usage = load_usage(home)
    stamp = now_iso()
    for entry_id in entry_ids:
        record = usage.setdefault(entry_id, {"uses": 0, "last_used": None})
        record["uses"] = int(record.get("uses", 0)) + 1
        record["last_used"] = stamp
    if len(usage) > 2000:
        recent = sorted(usage.items(), key=lambda item: item[1].get("last_used") or "", reverse=True)[:1000]
        usage = dict(recent)
    save_json(usage_path(home), usage)


# --------------------------------------------------------------------------- context


def _git(args: list[str], cwd: Path) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def project_keys(cwd: Path) -> dict:
    cwd = cwd.resolve()
    keys = {"path": str(cwd), "remote": ""}
    root = _git(["rev-parse", "--show-toplevel"], cwd)
    if root:
        keys["path"] = root
    remote = _git(["remote", "get-url", "origin"], cwd)
    if remote:
        keys["remote"] = remote
    return keys


def entry_matches(entry: Entry, host: str, cwd: Path, project: dict) -> bool:
    scope = entry.scope
    if scope == "user":
        return True
    if scope == "machine":
        return str(entry.meta.get("machine") or "") == host
    if scope == "project":
        raw = str(entry.meta.get("project") or "")
        if raw.startswith("remote:"):
            return bool(project.get("remote")) and raw[7:] == project["remote"]
        if raw.startswith("path:"):
            raw = raw[5:]
        if not raw:
            return False
        root = Path(raw).expanduser()
        try:
            return cwd == root or cwd.is_relative_to(root) or str(root) == project.get("path")
        except OSError:
            return False
    if scope == "task":
        return True
    return False


def build_context(home: Path, cfg: dict, cwd: Path | None = None, mark_used: bool = True) -> dict:
    cwd = Path(cwd or Path.cwd()).resolve()
    host = host_name()
    project = project_keys(cwd)
    entries = [e for e in list_entries(home, kinds=("memory", "rule")) if e.status == "active"]
    rules = [e for e in entries if e.kind == "rule" and entry_matches(e, host, cwd, project)]
    memories = [e for e in entries if e.kind == "memory" and entry_matches(e, host, cwd, project)]
    rules.sort(key=lambda e: (SCOPE_RANK.get(e.scope, 9), e.title.lower()))
    memories.sort(key=lambda e: (SCOPE_RANK.get(e.scope, 9), e.title.lower()))
    skills = list_entries(home, kinds=("skill",))
    if mark_used:
        touch_usage(home, [e.id for e in rules + memories])
    usage = load_usage(home)
    return {
        "host": host,
        "cwd": str(cwd),
        "project": project,
        "rules": [e.to_dict(usage) for e in rules],
        "memories": [e.to_dict(usage) for e in memories],
        "skills": [e.to_dict(usage) for e in skills],
    }


# --------------------------------------------------------------------------- builtins


def ensure_builtin_skills(home: Path) -> list[str]:
    """Copy builtin skills shipped with the repo into the library (idempotent)."""
    created: list[str] = []
    if not BUILTIN_DIR.is_dir():
        return created
    ensure_library(home)
    for source in sorted(BUILTIN_DIR.glob("*/SKILL.md")):
        name = source.parent.name
        destination = skills_dir(home) / name
        if destination.exists():
            continue
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination / "SKILL.md")
        created.append(name)
    return created
