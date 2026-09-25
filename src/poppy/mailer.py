"""Optional email notifications for new candidates — stdlib SMTP only.

Nothing here is provider-specific on purpose: SMTP host, port, and TLS mode are
stable protocol facts, and which values a given provider uses is something the
installing agent can look up (and `poppy email test` can verify). Secrets live
in Poppy's 0600 config or in ``SMTP_*`` environment variables (environment
wins), never in the repository.

Decision links are signed, single-use, and expiring. They authorize exactly one
action on one candidate; the UI shows the candidate for now and only the
confirmation page mutates state (a bare GET never does).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import smtplib
import sys
import time
from email.message import EmailMessage
from pathlib import Path

from .candidates import load_candidate
from .library import host_name
from .util import PoppyError, load_json, save_json

SECURITY_MODES = ("starttls", "ssl", "none")
DEFAULT_PORTS = {"starttls": 587, "ssl": 465, "none": 25}
TOKEN_TTL_SEC = 14 * 86400
TOKEN_ACTIONS = ("accept", "reject")

_ENV_KEYS = {
    "host": "SMTP_HOST",
    "port": "SMTP_PORT",
    "security": "SMTP_SECURITY",
    "user": "SMTP_USER",
    "password": "SMTP_PASS",
    "from": "SMTP_FROM",
    "to": "SMTP_TO",
}


# --------------------------------------------------------------------------- config


def email_config(cfg: dict) -> dict:
    """The email block with ``SMTP_*`` environment overrides applied."""
    block = dict(cfg.get("email") or {})
    for key, env in _ENV_KEYS.items():
        value = os.environ.get(env)
        if value:
            block[key] = value
    return block


def email_ready(cfg: dict) -> tuple[bool, str]:
    block = email_config(cfg)
    if not block.get("enabled"):
        return False, "email is off (`poppy email set --enable`)"
    missing = [key for key in ("host", "from") if not str(block.get(key) or "").strip()]  # `to` defaults to `from`
    if missing:
        return False, f"email is missing {', '.join(missing)}"
    return True, ""


def base_url(cfg: dict) -> str:
    """Where decision links point: ``email.base_url``, else the UI bind address."""
    configured = str(email_config(cfg).get("base_url") or "").strip().rstrip("/")
    if configured:
        return configured
    ui = cfg.get("ui") or {}
    host = str(ui.get("host") or "127.0.0.1")
    return f"http://{host}:{int(ui.get('port') or 8788)}"


# --------------------------------------------------------------------------- tokens


def secret_path(home: Path) -> Path:
    return home / "state" / "notify-secret"


def notify_secret(home: Path) -> str:
    path = secret_path(home)
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    secret = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return secret


def _signature(home: Path, payload: str) -> str:
    return hmac.new(notify_secret(home).encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def mint_token(home: Path, candidate_id: str, action: str, ttl_sec: int = TOKEN_TTL_SEC) -> str:
    if action not in TOKEN_ACTIONS:
        raise PoppyError(f"unknown decision action: {action}")
    if "." in candidate_id:
        raise PoppyError(f"candidate id cannot contain dots: {candidate_id}")
    payload = f"{candidate_id}.{action}.{int(time.time()) + int(ttl_sec)}"
    return f"{payload}.{_signature(home, payload)}"


def verify_token(home: Path, token: str, now: float | None = None) -> tuple[str, str] | None:
    """Return (candidate_id, action) for a valid token, else None."""
    parts = str(token or "").split(".")
    if len(parts) != 4:
        return None
    candidate_id, action, expires, signature = parts
    if action not in TOKEN_ACTIONS:
        return None
    payload = f"{candidate_id}.{action}.{expires}"
    if not hmac.compare_digest(signature, _signature(home, payload)):
        return None
    try:
        if int(expires) < (time.time() if now is None else now):
            return None
    except ValueError:
        return None
    return candidate_id, action


def used_tokens_path(home: Path) -> Path:
    return home / "state" / "notify-used.json"


def token_used(home: Path, token: str) -> bool:
    parts = str(token or "").split(".")
    if len(parts) != 4:
        return True
    used = load_json(used_tokens_path(home), {}) or {}
    return parts[3] in used


def consume_token(home: Path, token: str) -> bool:
    """Record a verified token as used. Returns False when it was used already."""
    parts = str(token or "").split(".")
    if len(parts) != 4:
        return False
    used = load_json(used_tokens_path(home), {}) or {}
    if parts[3] in used:
        return False
    now = time.time()
    used[parts[3]] = parts[2]
    try:
        expiry = float(parts[2])
    except ValueError:
        expiry = now
    used = {sig: exp for sig, exp in used.items() if float(exp) > now}
    save_json(used_tokens_path(home), used)
    return True


# --------------------------------------------------------------------------- sending


def send(cfg: dict, subject: str, body: str) -> None:
    """Send one plain-text message. Raises PoppyError with the SMTP error verbatim."""
    ready, reason = email_ready(cfg)
    if not ready:
        raise PoppyError(f"cannot send mail: {reason}")
    block = email_config(cfg)
    security = str(block.get("security") or "starttls").strip().lower()
    if security not in SECURITY_MODES:
        raise PoppyError(f"unknown security mode {security!r} (expected {', '.join(SECURITY_MODES)})")
    try:
        port = int(block.get("port") or DEFAULT_PORTS[security])
    except (TypeError, ValueError):
        raise PoppyError(f"invalid SMTP port: {block.get('port')!r}") from None

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = str(block["from"])
    message["To"] = str(block.get("to") or block["from"])
    message.set_content(body)
    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(str(block["host"]), port, timeout=30)
        else:
            server = smtplib.SMTP(str(block["host"]), port, timeout=30)
        with server:
            if security == "starttls":
                server.starttls()
            if block.get("user"):
                server.login(str(block["user"]), str(block.get("password") or ""))
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise PoppyError(f"sending mail failed: {exc}") from None


def _quote_line(candidate: dict, limit: int = 180) -> str:
    for item in (candidate.get("evidence") or [])[:1]:
        quote = " ".join(str(item.get("quote") or "").split())
        if quote:
            return quote[:limit] + ("…" if len(quote) > limit else "")
    return ""


def candidate_digest(home: Path, cfg: dict, candidates: list[dict], source_label: str) -> tuple[str, str]:
    """(subject, body) for a notification about queued candidates."""
    base = base_url(cfg)
    machine = host_name()
    count = len(candidates)
    plural = "" if count == 1 else "s"
    subject = f"Poppy: {count} new candidate{plural} on {machine}"
    lines = [f"Poppy queued {count} new candidate{plural} on {machine} ({source_label}).", ""]
    for index, candidate in enumerate(candidates, start=1):
        kind = str(candidate.get("kind") or "skill")
        lines.append(f"{index}. [{kind}] {candidate.get('title') or candidate.get('id')}")
        trigger = str(candidate.get("trigger") or "").strip()
        if trigger:
            lines.append(f"   {trigger}")
        quote = _quote_line(candidate)
        if quote:
            lines.append(f'   "{quote}"')
        candidate_id = str(candidate.get("id") or "")
        if candidate_id:
            accept = f"{base}/decide?token={mint_token(home, candidate_id, 'accept')}"
            reject = f"{base}/decide?token={mint_token(home, candidate_id, 'reject')}"
            lines.append(f"   Accept: {accept}")
            lines.append(f"   Reject: {reject}")
        lines.append("")
    lines.append(f"Review everything in the UI: {base}")
    lines.append("Links expire in 14 days, work once, and need the Poppy UI running on that address.")
    return subject, "\n".join(lines)


def notify_candidates(home: Path, cfg: dict, candidate_ids: list[str], source_label: str) -> str:
    """Best-effort notification. Returns 'sent', 'off', or 'failed: ...'; never raises."""
    ready, _reason = email_ready(cfg)
    if not ready or not candidate_ids:
        return "off"
    loaded = []
    for candidate_id in candidate_ids:
        try:
            loaded.append(load_candidate(home, candidate_id))
        except PoppyError:
            continue
    if not loaded:
        return "off"
    try:
        subject, body = candidate_digest(home, cfg, loaded, source_label)
        send(cfg, subject, body)
        return "sent"
    except PoppyError as exc:
        sys.stderr.write(f"poppy: email notification failed: {exc}\n")
        return f"failed: {exc}"
