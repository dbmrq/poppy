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
import html
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import decay, digest, library, mailer, settings
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
PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Poppy — {title}</title>
<style>
  :root {{ color-scheme: light dark; --bg:#faf9f7; --fg:#1d1c1a; --muted:#6f6b64; --card:#ffffff;
    --border:#e7e4df; --accent:#b4530a; --accent-fg:#ffffff; --danger:#a03030; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg:#141413; --fg:#ecebe8; --muted:#9b978f;
    --card:#1d1d1c; --border:#2f2e2c; --accent:#e08a3c; --accent-fg:#1d1c1a; --danger:#e08a8a; }} }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
    font:15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
  main {{ max-width:560px; margin:0 auto; padding:32px 20px 60px; }}
  h1 {{ font-size:19px; margin:6px 0 4px; }}
  .meta {{ color:var(--muted); font-size:12.5px; text-transform:uppercase; letter-spacing:.06em; margin:0; }}
  .trigger {{ margin:6px 0 18px; }}
  form {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; }}
  input[type=text] {{ flex:1 1 100%; font:inherit; padding:8px 11px; border:1px solid var(--border);
    border-radius:8px; background:var(--bg); color:var(--fg); }}
  button, .button {{ font:inherit; font-size:14px; padding:8px 14px; border-radius:8px;
    border:1px solid var(--border); background:transparent; color:var(--fg); text-decoration:none; cursor:pointer; }}
  button.primary {{ background:var(--accent); border-color:var(--accent); color:var(--accent-fg); font-weight:600; }}
  button.danger {{ color:var(--danger); }}
  a {{ color:var(--accent); }}
  .note {{ color:var(--muted); font-size:12.5px; margin-top:14px; }}
</style>
</head>
<body><main>{body}</main></body>
</html>
"""
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
        mine(home, config=cfg, quiet=True, notify=False)
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

    def _send_html(self, code: int, body: str) -> None:
        self._send(code, body.encode("utf-8"), "text/html; charset=utf-8")

    def _page(self, title: str, body: str) -> str:
        return PAGE_TEMPLATE.format(title=html.escape(title), body=body)

    def _ui_link(self) -> str:
        url = mailer.base_url(self.cfg)
        return f'<p class="note">Review everything in the UI: <a href="{html.escape(url)}">{html.escape(url)}</a></p>'

    def _decide_page(self, token: str) -> None:
        found = mailer.verify_token(self.home, token)
        if not found or mailer.token_used(self.home, token):
            self._send_html(410, self._page("Link no longer valid", f"<p>This link is invalid, expired, or already used.</p>{self._ui_link()}"))
            return
        candidate_id, action = found
        try:
            candidate = load_candidate(self.home, candidate_id)
        except PoppyError:
            self._send_html(410, self._page("Already handled", f"<p>That candidate is no longer in the queue.</p>{self._ui_link()}"))
            return
        status = str(candidate.get("status") or "pending")
        verb = "Accept" if action == "accept" else "Reject"
        reason = (
            '<input type="text" name="reason" placeholder="reason (optional)">'
            if action == "reject"
            else ""
        )
        body = (
            f'<p class="meta">{html.escape(str(candidate.get("kind") or "skill"))} · {html.escape(status)}</p>'
            f'<h1>{html.escape(str(candidate.get("title") or candidate_id))}</h1>'
            f'<p class="trigger">{html.escape(str(candidate.get("trigger") or ""))}</p>'
            '<form method="post" action="/decide">'
            f'<input type="hidden" name="token" value="{html.escape(token)}">'
            f"{reason}"
            f'<button class="primary{"" if action == "accept" else " danger"}" type="submit">{verb}</button>'
            f'<a class="button" href="{html.escape(mailer.base_url(self.cfg))}">Open the UI</a>'
            "</form>"
            '<p class="note">This link works once and expires in 14 days.</p>'
        )
        self._send_html(200, self._page(f"{verb} candidate", body))

    def _decide_apply(self, token: str, reason: str) -> None:
        found = mailer.verify_token(self.home, token)
        if not found or mailer.token_used(self.home, token):
            self._send_html(410, self._page("Link no longer valid", f"<p>This link is invalid, expired, or already used.</p>{self._ui_link()}"))
            return
        candidate_id, action = found
        try:
            if action == "reject":
                handle_action(
                    self.home,
                    self.cfg,
                    "reject",
                    {"id": candidate_id, "reason": reason or "rejected from the notification email"},
                )
                message = "Rejected. It will not be proposed again."
            else:
                result = handle_action(self.home, self.cfg, "accept", {"id": candidate_id})
                if result.get("status") == "writing":
                    message = "Accepted. The writer agent is drafting the skill — open the UI to review the draft."
                elif result.get("status") == "active":
                    message = "Accepted into the library."
                else:
                    message = f"Accepted ({html.escape(str(result.get('status') or 'ok'))})."
        except PoppyError as exc:
            self._send_html(400, self._page("Could not apply", f"<p>{html.escape(str(exc))}</p>{self._ui_link()}"))
            return
        mailer.consume_token(self.home, token)
        try:  # keep the always-on digest fresh; it is a cache, never fail the action
            digest.export(self.home, self.cfg)
        except Exception:
            pass
        self._send_html(200, self._page("Done", f"<p>{message}</p>{self._ui_link()}"))

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
        if urlparse(self.path).path == "/decide":
            query = parse_qs(urlparse(self.path).query)
            self._decide_page((query.get("token") or [""])[0])
            return
        if not self._authorized():
            return
        if self.path in ("/", "/index.html"):
            self._send(200, self.index_html, "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._json(self.backend.state() if self.backend is not None else _state(self.home, self.cfg))
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path == "/decide":
            # email decision links: the signed token is the credential, and the
            # decision is bound to one candidate + action (form-encoded so the
            # confirmation page can post it)
            length = int(self.headers.get("Content-Length") or 0)
            form = parse_qs(self.rfile.read(length).decode("utf-8", "replace")) if length else {}
            self._decide_apply((form.get("token") or [""])[0], (form.get("reason") or [""])[0])
            return
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
