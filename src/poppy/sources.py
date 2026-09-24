"""Transcript sources.

Three source types, in order of preference:

- ``command``: delegate to the harness's own listing/export commands.
- ``files``:   a directory (or file) of JSONL/text transcripts.
- ``sqlite``:  a read-only SQLite database with ``list_query`` / ``read_query``.

The installer writes ``~/.poppy/sources.json`` and verifies entries with
``poppy sources test``. Nothing here knows about any specific harness.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .util import PoppyError, load_json, norm_ws, tail


@dataclass
class Session:
    source: str
    id: str
    time: float | None = None
    title: str = ""
    cwd: str = ""
    size: int = 0
    path: str = ""

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "session": self.id,
            "time": self.time,
            "title": self.title,
            "cwd": self.cwd,
            "size": self.size,
            "path": self.path,
        }


@dataclass
class Hit:
    source: str
    session: str
    line: int
    snippet: str


def _norm_epoch(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1e11:  # milliseconds
        number /= 1000.0
    return number


def _first(mapping: dict, keys: tuple[str, ...], default=None):
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return default


def _search_over_read(source, read, pattern, since, limit, session_ids):
    """Shared search for sources whose sessions must be read to be searched."""
    rx = re.compile(pattern, re.IGNORECASE)
    hits: list[Hit] = []
    for session in source.list_sessions(since=since):
        if session_ids and session.id not in session_ids:
            continue
        try:
            text = read(session.id, 400_000)
        except PoppyError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(Hit(source.name, session.id, lineno, norm_ws(line)[:400]))
                if len(hits) >= limit:
                    return hits
    return hits


class FilesSource:
    type = "files"

    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.cfg = cfg
        self.root = Path(os.path.expanduser(cfg["path"]))
        if not self.root.exists():
            raise PoppyError(f"source {name}: path not found: {self.root}")
        self.pattern = cfg.get("glob", "**/*")
        self.extensions = cfg.get("extensions") or []
        self.max_session_bytes = int(cfg.get("max_session_bytes", 5_000_000))
        self._is_file = self.root.is_file()

    def _iter_files(self):
        if self._is_file:
            yield self.root
            return
        for path in sorted(self.root.glob(self.pattern)):
            if not path.is_file():
                continue
            if self.extensions and path.suffix not in self.extensions:
                continue
            yield path

    def _session_id(self, path: Path) -> str:
        return path.name if self._is_file else str(path.relative_to(self.root))

    def _path_for(self, session_id: str) -> Path:
        path = self.root if self._is_file else (self.root / session_id)
        root = self.root.resolve()
        resolved = path.resolve()
        if not str(resolved).startswith(str(root)):
            raise PoppyError(f"source {self.name}: session id escapes the source root")
        if not resolved.is_file():
            raise PoppyError(f"source {self.name}: unknown session: {session_id}")
        return resolved

    def list_sessions(self, since: float | None = None, limit: int | None = None) -> list[Session]:
        sessions = []
        for path in self._iter_files():
            try:
                stat = path.stat()
            except OSError:
                continue
            if since is not None and stat.st_mtime < since:
                continue
            sessions.append(
                Session(
                    source=self.name,
                    id=self._session_id(path),
                    time=stat.st_mtime,
                    title=path.stem,
                    size=stat.st_size,
                    path=str(path),
                )
            )
        sessions.sort(key=lambda s: s.time or 0, reverse=True)
        return sessions[:limit] if limit else sessions

    def read_session(self, session_id: str, max_chars: int | None = None) -> str:
        path = self._path_for(session_id)
        with open(path, "rb") as fh:
            raw = fh.read(self.max_session_bytes)
        text = raw.decode("utf-8", "replace")
        if max_chars and len(text) > max_chars:
            text = text[:max_chars] + "\n…[truncated by poppy]"
        return text

    def search(self, pattern, since=None, limit=100, session_ids=None) -> list[Hit]:
        return _search_over_read(self, self.read_session, pattern, since, limit, session_ids)


class SqliteSource:
    type = "sqlite"

    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.cfg = cfg
        self.path = Path(os.path.expanduser(cfg["path"]))
        if not self.path.exists():
            raise PoppyError(f"source {name}: database not found: {self.path}")
        if not cfg.get("list_query"):
            raise PoppyError(f"source {name}: sqlite sources need list_query")
        self.list_query = cfg["list_query"]
        self.read_query = cfg.get("read_query")
        self.read_row_limit = int(cfg.get("read_row_limit", 5000))

    def _conn(self) -> sqlite3.Connection:
        uri = self.path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _query(self, sql: str, params: dict):
        with self._conn() as conn:
            try:
                return conn.execute(sql, params).fetchall()
            except sqlite3.Error as exc:
                raise PoppyError(f"source {self.name}: query failed: {exc}") from exc

    def list_sessions(self, since: float | None = None, limit: int | None = None) -> list[Session]:
        params = {"limit": int(limit or 500), "since": int((since or 0) * 1000)}
        rows = self._query(self.list_query, params)
        sessions = []
        for row in rows:
            data = dict(row)
            session_id = _first(data, ("id", "session_id", "sessionId", "sid"))
            if not session_id:
                continue
            sessions.append(
                Session(
                    source=self.name,
                    id=str(session_id),
                    time=_norm_epoch(_first(data, ("time", "time_updated", "updated", "time_created", "created"))),
                    title=str(_first(data, ("title", "name"), "") or ""),
                    cwd=str(_first(data, ("cwd", "directory", "project"), "") or ""),
                )
            )
        if since:
            sessions = [s for s in sessions if s.time is None or s.time >= since]
        sessions.sort(key=lambda s: s.time or 0, reverse=True)
        return sessions[:limit] if limit else sessions

    def read_session(self, session_id: str, max_chars: int | None = None) -> str:
        if not self.read_query:
            raise PoppyError(f"source {self.name}: no read_query configured")
        rows = self._query(self.read_query, {"id": session_id, "limit": self.read_row_limit})
        parts = []
        for row in rows:
            text = _first(dict(row), ("text", "content", "message", "data"))
            if text:
                parts.append(str(text))
        out = "\n\n".join(parts)
        if max_chars and len(out) > max_chars:
            out = out[:max_chars] + "\n…[truncated by poppy]"
        return out

    def search(self, pattern, since=None, limit=100, session_ids=None) -> list[Hit]:
        return _search_over_read(self, self.read_session, pattern, since, limit, session_ids)


class CommandSource:
    type = "command"

    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.cfg = cfg
        if not cfg.get("list_cmd"):
            raise PoppyError(f"source {name}: command sources need list_cmd")
        if not cfg.get("read_cmd"):
            raise PoppyError(f"source {name}: command sources need read_cmd")
        self.list_cmd = list(cfg["list_cmd"])
        self.read_cmd = list(cfg["read_cmd"])
        self.timeout = int(cfg.get("timeout_sec", 60))

    def _run(self, argv: list[str]) -> str:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout)
        except FileNotFoundError as exc:
            raise PoppyError(f"source {self.name}: command not found: {argv[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise PoppyError(f"source {self.name}: command timed out after {self.timeout}s") from exc
        if proc.returncode != 0:
            raise PoppyError(
                f"source {self.name}: command failed ({proc.returncode}): {tail(proc.stderr, 300)}"
            )
        return proc.stdout

    def list_sessions(self, since: float | None = None, limit: int | None = None) -> list[Session]:
        raw = self._run(self.list_cmd)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PoppyError(f"source {self.name}: list_cmd did not print JSON: {exc}") from exc
        if isinstance(data, dict):
            data = data.get("sessions") or data.get("data") or []
        sessions = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            session_id = _first(item, ("id", "session_id", "sessionId", "sid"))
            if not session_id:
                continue
            sessions.append(
                Session(
                    source=self.name,
                    id=str(session_id),
                    time=_norm_epoch(_first(item, ("time", "time_updated", "updated", "time_created", "created"))),
                    title=str(_first(item, ("title", "name", "summary"), "") or ""),
                    cwd=str(_first(item, ("cwd", "directory", "project"), "") or ""),
                )
            )
        if since:
            sessions = [s for s in sessions if s.time is None or s.time >= since]
        sessions.sort(key=lambda s: s.time or 0, reverse=True)
        return sessions[:limit] if limit else sessions

    def read_session(self, session_id: str, max_chars: int | None = None) -> str:
        argv = [part.replace("{id}", session_id) for part in self.read_cmd]
        out = self._run(argv)
        if max_chars and len(out) > max_chars:
            out = out[:max_chars] + "\n…[truncated by poppy]"
        return out

    def search(self, pattern, since=None, limit=100, session_ids=None) -> list[Hit]:
        return _search_over_read(self, self.read_session, pattern, since, limit, session_ids)


SOURCE_TYPES = {
    "files": FilesSource,
    "sqlite": SqliteSource,
    "command": CommandSource,
}


def build_source(entry: dict):
    name = entry.get("name") or "unnamed"
    kind = entry.get("type")
    if kind not in SOURCE_TYPES:
        raise PoppyError(f"source {name}: unknown type {kind!r} (expected one of {', '.join(SOURCE_TYPES)})")
    return SOURCE_TYPES[kind](name, entry)


def sources_path(home: Path) -> Path:
    return home / "sources.json"


def load_sources(home: Path) -> list:
    data = load_json(sources_path(home), {"sources": []}) or {}
    entries = data.get("sources", [])
    out = []
    for entry in entries:
        try:
            out.append(build_source(entry))
        except PoppyError:
            raise
        except Exception as exc:  # defensive: a bad entry should not crash the whole CLI
            raise PoppyError(f"source {entry.get('name', '?')}: {exc}") from exc
    return out


def save_sources(home: Path, entries: list[dict]) -> None:
    from .util import save_json

    save_json(sources_path(home), {"sources": entries}, mode=0o600)
