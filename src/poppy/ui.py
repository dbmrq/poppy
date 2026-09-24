"""The local review UI: candidates in, library entries out.

Binds localhost by default. A non-loopback bind requires a token (HTTP Basic;
any username, the token as the password) unless `--insecure` explicitly
acknowledges the risk; POSTs must be `application/json`, so a cross-site form
cannot act on the library.
"""

from __future__ import annotations

import base64
import errno
import hmac
import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import decay, digest, library, settings
from .candidates import (
    drafts_candidate_dir,
    list_candidates,
    load_candidate,
    mark_rejected,
    save_candidate,
)
from .config import config_path, save_config
from .demo import DemoBackend
from .doctor import run_checks
from .pipeline import accept, discard_draft, mine, mine_state, save_mine_state
from .skills import archive_skill, install_draft, restore_skill
from .util import DATA_DIR, PoppyError, REPO_ROOT, load_json, now_iso, save_json, tail

INDEX_HTML = DATA_DIR / "ui" / "index.html"
QUEUE_STATUSES = {"pending", "writing", "draft", "draft_invalid", "draft_failed", "writer_rejected"}
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _basic_password(header: str) -> str:
    if not header.startswith("Basic "):
        return ""
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8", "replace")
    except ValueError:
        return ""
    _user, _sep, password = decoded.partition(":")
    return password


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
        "config": {
            "path": str(config_path(home)),
            "fields": settings.fields(cfg),
            "ui_address": f"{cfg.get('ui', {}).get('host', '127.0.0.1')}:{cfg.get('ui', {}).get('port', 8788)}",
        },
        "mining": mine_state(home),
    }


def _accept_worker(home: Path, cfg: dict, candidate_id: str, instructions: str | None = None) -> None:
    try:
        accept(home, candidate_id, cfg, instructions=instructions)
    except Exception:  # surface failures on the candidate instead of losing them
        try:
            candidate = load_candidate(home, candidate_id)
            candidate["status"] = "draft_failed"
            candidate["error"] = tail(traceback.format_exc(), 800)
            save_candidate(home, candidate)
        except Exception:
            pass


def _mine_worker(home: Path, cfg: dict) -> None:
    """Run a mining pass; ``pipeline.mine`` records progress and outcome itself."""
    try:
        mine(home, config=cfg, quiet=True)
    except Exception as exc:  # mine() records its own failures; cover pre-run errors too
        state = mine_state(home)
        if state.get("running") or not state.get("error"):
            save_mine_state(home, running=False, finished_at=now_iso(), error=str(exc))


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
        instructions = payload.get("instructions")
        thread = threading.Thread(
            target=_accept_worker, args=(home, cfg, candidate_id, instructions), daemon=True
        )
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

    if action == "config_set":
        normalized = settings.validate(payload.get("values"))
        settings.apply(cfg, normalized)  # cfg is shared, so the change is live
        save_config(home, cfg)
        return {"ok": True, "fields": settings.fields(cfg)}

    if action == "doctor":
        checks = run_checks(home, with_agent=bool(payload.get("agent")))
        return {
            "ok": True,
            "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in checks],
        }

    if action == "mine":
        if mine_state(home).get("running"):
            raise PoppyError("a mining run is already in progress")
        thread = threading.Thread(target=_mine_worker, args=(home, cfg), daemon=True)
        thread.start()
        return {"ok": True, "running": True}

    raise PoppyError(f"unknown action: {action}")


class Handler(BaseHTTPRequestHandler):
    home: Path
    cfg: dict
    token: str = ""
    backend: DemoBackend | None = None  # set only in --demo mode

    def log_message(self, *args):  # keep the terminal quiet
        pass

    def _send(self, code: int, body: bytes, content_type: str, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def _authorized(self) -> bool:
        if not self.token:
            return True
        password = _basic_password(self.headers.get("Authorization") or "")
        if password and hmac.compare_digest(password, self.token):
            return True
        self._send(
            401,
            b"authentication required\n",
            "text/plain; charset=utf-8",
            {"WWW-Authenticate": 'Basic realm="poppy"'},
        )
        return False

    def do_GET(self):  # noqa: N802 (http.server API)
        if not self._authorized():
            return
        if self.path in ("/", "/index.html"):
            self._send(200, self.index_html, "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._json(self.backend.state() if self.backend is not None else _state(self.home, self.cfg))
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        if not self._authorized():
            return
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            self._json({"error": "Content-Type must be application/json"}, 415)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "invalid JSON"}, 400)
            return
        try:
            if self.backend is not None:
                result = self.backend.action(str(payload.get("action", "")), payload)
            else:
                result = handle_action(self.home, self.cfg, str(payload.get("action", "")), payload)
        except PoppyError as exc:
            self._json({"error": str(exc)}, 400)
            return
        except Exception:
            self._json({"error": "internal error", "detail": tail(traceback.format_exc(), 800)}, 500)
            return
        if self.backend is None:
            try:  # keep the always-on digest fresh; it is a cache, never fail the action
                digest.export(self.home, self.cfg)
            except Exception:
                pass
        self._json(result)


def build_server(
    home: Path,
    cfg: dict,
    host: str | None = None,
    port: int | None = None,
    token: str | None = None,
    insecure: bool = False,
    demo: bool = False,
) -> ThreadingHTTPServer:
    ui_cfg = cfg.get("ui") or {}
    bind_host = str(host or ui_cfg.get("host", "127.0.0.1"))
    bind_port = int(port if port is not None else ui_cfg.get("port", 8788))
    token_value = str(token if token is not None else (ui_cfg.get("token") or ""))
    if bind_host not in LOOPBACK_HOSTS and not token_value and not insecure:
        raise PoppyError(
            f"refusing to bind {bind_host} without a token — anyone who can reach the port "
            "could change the library; pass --token <secret> (or --insecure to acknowledge the risk)"
        )
    if not INDEX_HTML.is_file():
        raise PoppyError(f"the UI page is missing from this install ({INDEX_HTML}) — reinstall Poppy")
    # Read the page once: a running server must survive its install directory
    # being replaced (for example a package-manager upgrade).
    attrs = {"home": home, "cfg": cfg, "token": token_value, "index_html": INDEX_HTML.read_bytes()}
    if demo:
        attrs["backend"] = DemoBackend()
    handler = type("PoppyHandler", (Handler,), attrs)
    try:
        server = ThreadingHTTPServer((bind_host, bind_port), handler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise PoppyError(
                f"port {bind_port} is already in use — another `poppy ui` may be running; "
                "stop it or pass `poppy ui --port <other>`"
            ) from exc
        raise PoppyError(f"cannot bind {bind_host}:{bind_port}: {exc}") from exc
    server.token = token_value  # type: ignore[attr-defined]
    return server


def serve(
    home: Path,
    cfg: dict,
    host: str | None = None,
    port: int | None = None,
    token: str | None = None,
    insecure: bool = False,
    demo: bool = False,
) -> int:
    server = build_server(home, cfg, host=host, port=port, token=token, insecure=insecure, demo=demo)
    bind_host, bind_port = server.server_address[0], server.server_address[1]
    print(f"poppy ui: http://{bind_host}:{bind_port}  (Ctrl-C to stop)")
    if demo:
        print("mode:     demo — mock data, nothing is read from or written to disk")
    if server.token:
        print("auth:     HTTP Basic — any username, the token as the password")
    elif bind_host not in LOOPBACK_HOSTS:
        print("warning:  no token set — anyone who can reach this port can change the library")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0
