"""The local review UI: candidates in, library entries out. Localhost only."""

from __future__ import annotations

import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import decay, digest, library
from .candidates import (
    drafts_candidate_dir,
    list_candidates,
    load_candidate,
    mark_rejected,
    save_candidate,
)
from .pipeline import accept, discard_draft
from .skills import archive_skill, install_draft, restore_skill
from .util import DATA_DIR, PoppyError, REPO_ROOT, tail

INDEX_HTML = DATA_DIR / "ui" / "index.html"
QUEUE_STATUSES = {"pending", "writing", "draft", "draft_invalid", "draft_failed", "writer_rejected"}


def _state(home: Path, cfg: dict) -> dict:
    candidates = [
        candidate
        for candidate in list_candidates(home)
        if candidate.get("status") in QUEUE_STATUSES or candidate.get("kind") == "decay"
    ]
    for candidate in candidates:
        draft = drafts_candidate_dir(home, candidate["id"]) / "SKILL.md"
        if draft.is_file():
            candidate["draft_preview"] = draft.read_text(encoding="utf-8", errors="replace")[:20000]
    usage = library.load_usage(home)
    grouped: dict[str, list] = {"skill": [], "memory": [], "rule": []}
    for entry in library.list_entries(home):
        grouped[entry.kind].append(entry.to_dict(usage))
    archived = [e.to_dict(usage) for e in library.list_entries(home, include_archived=True) if e.archived]
    return {
        "candidates": candidates,
        "library": grouped,
        "archived": archived,
        "skills_dirs": cfg.get("skills_dirs", []),
        "home": str(home),
    }


def _accept_worker(home: Path, cfg: dict, candidate_id: str) -> None:
    try:
        accept(home, candidate_id, cfg)
    except Exception:  # surface failures on the candidate instead of losing them
        try:
            candidate = load_candidate(home, candidate_id)
            candidate["status"] = "draft_failed"
            candidate["error"] = tail(traceback.format_exc(), 800)
            save_candidate(home, candidate)
        except Exception:
            pass


def handle_action(home: Path, cfg: dict, action: str, payload: dict) -> dict:
    if action == "accept":
        candidate_id = str(payload.get("id", ""))
        candidate = load_candidate(home, candidate_id)
        kind = str(candidate.get("kind") or "skill")
        if kind in ("memory", "rule"):
            result = accept(
                home,
                candidate_id,
                cfg,
                scope=payload.get("scope"),
                project=payload.get("project"),
            )
            return {"ok": True, **result}
        if candidate.get("status") == "writing":
            raise PoppyError("a writer run is already in progress for this candidate")
        thread = threading.Thread(target=_accept_worker, args=(home, cfg, candidate_id), daemon=True)
        thread.start()
        return {"ok": True, "status": "writing"}

    if action == "reject":
        candidate_id = str(payload.get("id", ""))
        reason = str(payload.get("reason") or "rejected in review").strip()
        mark_rejected(home, load_candidate(home, candidate_id), reason)
        return {"ok": True}

    if action == "discard_draft":
        return {"ok": True, **discard_draft(home, str(payload.get("id", "")))}

    if action == "install":
        candidate_id = str(payload.get("id", ""))
        candidate = load_candidate(home, candidate_id)
        if candidate.get("status") != "draft":
            raise PoppyError("candidate has no validated draft yet")
        name, dirs = install_draft(home, cfg, candidate)
        candidate["status"] = "installed"
        save_candidate(home, candidate)
        return {"ok": True, "name": name, "dirs": dirs}

    if action == "uninstall":
        return {"ok": True, "removed": archive_skill(home, str(payload.get("name", "")))}

    if action == "resolve_decay":
        return {
            "ok": True,
            **decay.resolve(
                home,
                cfg,
                str(payload.get("id", "")),
                str(payload.get("resolution", "")),
            ),
        }

    if action == "entry_pin":
        entry = library.find_entry(home, str(payload.get("id", "")))
        library.set_pinned(entry, bool(payload.get("pinned", True)))
        return {"ok": True}

    if action == "entry_verify":
        library.verify_entry(library.find_entry(home, str(payload.get("id", ""))))
        return {"ok": True}

    if action == "entry_archive":
        entry = library.find_entry(home, str(payload.get("id", "")))
        if entry.kind == "skill":
            archive_skill(home, entry.id)
        else:
            library.archive_entry(home, entry)
        return {"ok": True}

    if action == "entry_restore":
        entry = library.find_entry(home, str(payload.get("id", "")))
        if entry.kind == "skill":
            dirs = restore_skill(home, cfg, entry.id)
            return {"ok": True, "dirs": dirs}
        library.restore_entry(home, entry)
        return {"ok": True}

    raise PoppyError(f"unknown action: {action}")


class Handler(BaseHTTPRequestHandler):
    home: Path
    cfg: dict

    def log_message(self, *args):  # keep the terminal quiet
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def do_GET(self):  # noqa: N802 (http.server API)
        if self.path in ("/", "/index.html"):
            if not INDEX_HTML.is_file():
                self._json({"error": "ui/index.html missing"}, 500)
                return
            self._send(200, INDEX_HTML.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._json(_state(self.home, self.cfg))
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "invalid JSON"}, 400)
            return
        try:
            result = handle_action(self.home, self.cfg, str(payload.get("action", "")), payload)
        except PoppyError as exc:
            self._json({"error": str(exc)}, 400)
            return
        except Exception:
            self._json({"error": "internal error", "detail": tail(traceback.format_exc(), 800)}, 500)
            return
        try:  # keep the always-on digest fresh; it is a cache, never fail the action
            digest.export(self.home, self.cfg)
        except Exception:
            pass
        self._json(result)


def serve(home: Path, cfg: dict) -> int:
    ui_cfg = cfg.get("ui") or {}
    host = str(ui_cfg.get("host", "127.0.0.1"))
    port = int(ui_cfg.get("port", 8788))
    handler = type("PoppyHandler", (Handler,), {"home": home, "cfg": cfg})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"poppy ui: http://{host}:{port}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0
