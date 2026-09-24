"""The local review UI: candidates in, skills out. Localhost only, no credentials."""

from __future__ import annotations

import json
import shutil
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .candidates import (
    drafts_candidate_dir,
    list_candidates,
    load_candidate,
    mark_rejected,
    save_candidate,
)
from .pipeline import accept
from .skills import install_draft, load_manifest, uninstall_skill
from .util import PoppyError, REPO_ROOT, tail

INDEX_HTML = REPO_ROOT / "ui" / "index.html"


def _state(home: Path, cfg: dict) -> dict:
    candidates = [c for c in list_candidates(home) if c.get("status") != "installed"]
    for candidate in candidates:
        draft = drafts_candidate_dir(home, candidate["id"]) / "SKILL.md"
        if draft.is_file():
            candidate["draft_preview"] = draft.read_text(encoding="utf-8", errors="replace")[:20000]
    return {
        "candidates": candidates,
        "installed": load_manifest(home).get("skills", {}),
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
        if candidate.get("status") in ("writing",):
            raise PoppyError("a writer run is already in progress for this candidate")
        thread = threading.Thread(
            target=_accept_worker, args=(home, cfg, candidate_id), daemon=True
        )
        thread.start()
        return {"ok": True, "status": "writing"}

    if action == "reject":
        candidate_id = str(payload.get("id", ""))
        reason = str(payload.get("reason") or "rejected in review").strip()
        candidate = load_candidate(home, candidate_id)
        mark_rejected(home, candidate, reason)
        return {"ok": True}

    if action == "discard_draft":
        candidate_id = str(payload.get("id", ""))
        candidate = load_candidate(home, candidate_id)
        draft_dir = drafts_candidate_dir(home, candidate_id)
        if draft_dir.exists():
            shutil.rmtree(draft_dir)
        candidate["status"] = "pending"
        for key in ("draft_errors", "draft_warnings", "draft_name", "error", "writer_rejection"):
            candidate.pop(key, None)
        save_candidate(home, candidate)
        return {"ok": True}

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
        name = str(payload.get("name", ""))
        removed = uninstall_skill(home, name)
        return {"ok": True, "removed": removed}

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
            self._json(handle_action(self.home, self.cfg, str(payload.get("action", "")), payload))
        except PoppyError as exc:
            self._json({"error": str(exc)}, 400)
        except Exception:
            self._json({"error": "internal error", "detail": tail(traceback.format_exc(), 800)}, 500)


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
