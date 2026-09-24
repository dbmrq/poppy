"""Multi-machine sync: one private git repo for everything worth sharing.

The repo root is ``~/.poppy`` itself. Tracked content: the canonical library,
the candidate queue, and clean rejections (one file each, so merges are
trivial). Machine-local state — config, sources, the mirrors manifest, usage,
logs, locks, drafts, raw invalid dumps, and derived indexes — is gitignored.

Sync is deterministic: commit, fetch, rebase, push, then materialize locally
(reconcile skill mirrors against the library, regenerate the memory digest).
Conflicts abort the rebase and are surfaced for a human; offline runs
soft-fail and retry on the next run. No agent, no per-harness code.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .config import save_config
from .schedule import install as schedule_install
from .schedule import platform_kind
from .schedule import scheduled_path
from .schedule import status as schedule_status
from .schedule import sync_interval_minutes
from .skills import load_manifest, mirrors_current, reconcile_mirrors, skill_tree_hash
from .util import (
    PoppyError,
    acquire_lock,
    atomic_write_text,
    ensure_home_layout,
    extended_path,
    load_json,
    now_iso,
    redact_userinfo,
    release_lock,
    save_json,
    tail,
)

TRACKED_PATHS = ("library", "candidates", "rejected")
GIT_TIMEOUT = 180
LOCAL_TIMEOUT = 60

GITIGNORE = """# Machine-local state — never synced (managed by `poppy sync init`)
config.json
sources.json
installed.json
inbox/
drafts/
logs/
locks/
state/
context/
rejected/index.json
rejected/invalid-*.json
"""


# --------------------------------------------------------------------------- git


def git(cwd: Path, args: list[str], check: bool = False, timeout: int = LOCAL_TIMEOUT):
    if not cwd.is_dir():
        raise PoppyError(f"directory does not exist: {cwd}")
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError:
        raise PoppyError("git is not installed")
    except subprocess.TimeoutExpired:
        raise PoppyError(f"git {' '.join(args)} timed out after {timeout}s")
    if check and proc.returncode != 0:
        detail = tail((proc.stderr or proc.stdout).strip(), 300)
        raise PoppyError(f"git {' '.join(args)} failed: {detail}")
    return proc


def repo_exists(home: Path) -> bool:
    """True when *home* itself is the root of a git repo (not inside one)."""
    if not home.is_dir():
        return False
    proc = git(home, ["rev-parse", "--show-toplevel"])
    if proc.returncode != 0:
        return False
    try:
        return Path(proc.stdout.strip()).resolve() == home.resolve()
    except OSError:
        return False


def machine_name(cfg: dict) -> str:
    from .library import host_name

    return str((cfg.get("sync") or {}).get("machine") or "").strip() or host_name()


def tracked_existing(home: Path) -> list[str]:
    """Paths `poppy sync run` stages: shared content plus the generated .gitignore."""
    paths = [name for name in TRACKED_PATHS if (home / name).exists()]
    if (home / ".gitignore").is_file():
        paths.append(".gitignore")
    return paths


def ensure_gitignore(home: Path) -> None:
    path = home / ".gitignore"
    required = [line for line in GITIGNORE.splitlines() if line and not line.startswith("#")]
    if not path.is_file():
        atomic_write_text(path, GITIGNORE, mode=0o644)
        return
    existing = path.read_text(encoding="utf-8", errors="replace").splitlines()
    missing = [line for line in required if line not in existing]
    if missing:
        text = path.read_text(encoding="utf-8", errors="replace").rstrip()
        text += "\n\n# poppy: machine-local state\n" + "\n".join(missing) + "\n"
        atomic_write_text(path, text, mode=0o644)


def ensure_identity(home: Path, machine: str) -> None:
    """Fill in repo-local commit identity only when the user has none."""
    for key, value in (("user.name", f"poppy ({machine})"), ("user.email", f"poppy@{machine}.local")):
        proc = git(home, ["config", "--get", key])
        if proc.returncode != 0 or not proc.stdout.strip():
            git(home, ["config", key, value], check=True)


def commit_local(home: Path, machine: str, allow_empty: bool = False) -> bool:
    """Commit changes under the tracked paths. Returns True when a commit was made."""
    paths = tracked_existing(home)
    if paths:
        git(home, ["add", "-A", "--", *paths], check=True)
    status = git(home, ["status", "--porcelain", "--", *paths]) if paths else None
    staged = bool(status and status.stdout.strip())
    if not staged:
        head = git(home, ["rev-parse", "--verify", "-q", "HEAD"])
        if allow_empty and head.returncode != 0:
            git(
                home,
                ["commit", "-q", "--allow-empty", "-m", f"poppy: initialize ({machine})"],
                check=True,
            )
            return True
        return False
    count = len([line for line in status.stdout.splitlines() if line.strip()])
    git(home, ["commit", "-q", "-m", f"sync({machine}): {now_iso()} — {count} file(s)"], check=True)
    return True


def remote_ops(home: Path, remote: str, branch: str) -> dict:
    """Fetch, rebase onto the remote branch, push. Never force-pushes."""
    out: dict = {"fetched": False, "pulled": False, "pushed": False, "offline": False}
    if not remote:
        return out

    fetch = git(home, ["fetch", "origin"], timeout=GIT_TIMEOUT)
    if fetch.returncode != 0:
        out["offline"] = True
        out["error"] = tail((fetch.stderr or fetch.stdout).strip(), 300)
        return out
    out["fetched"] = True

    remote_ref = f"origin/{branch}"
    if git(home, ["rev-parse", "--verify", "-q", remote_ref]).returncode != 0:
        # First push: the remote branch does not exist yet.
        push = git(home, ["push", "-u", "origin", f"HEAD:{branch}"], timeout=GIT_TIMEOUT)
        if push.returncode != 0:
            out["error"] = tail((push.stderr or push.stdout).strip(), 300)
        else:
            out["pushed"] = True
        return out

    behind, ahead = _counts(home, remote_ref)
    if behind:
        rebase = git(home, ["rebase", remote_ref])
        if rebase.returncode != 0:
            git(home, ["rebase", "--abort"])
            out["conflict"] = True
            detail = (rebase.stderr or rebase.stdout).strip()
            lines = [line.strip() for line in detail.splitlines() if line.strip()]
            summary = next(
                (line for line in lines if "CONFLICT" in line), lines[0] if lines else "rebase failed"
            )
            out["error"] = tail(summary, 200)
            return out
        out["pulled"] = True

    _behind_now, ahead_now = _counts(home, remote_ref)
    if ahead_now:
        push = git(home, ["push", "origin", f"HEAD:{branch}"], timeout=GIT_TIMEOUT)
        if push.returncode != 0:
            out["error"] = tail((push.stderr or push.stdout).strip(), 300)
        else:
            out["pushed"] = True
    return out


def _counts(home: Path, remote_ref: str) -> tuple[int, int]:
    """(behind, ahead) of HEAD relative to the remote ref."""
    proc = git(home, ["rev-list", "--left-right", "--count", f"{remote_ref}...HEAD"])
    if proc.returncode != 0 or not proc.stdout.strip():
        return 0, 0
    parts = proc.stdout.strip().split()
    if len(parts) != 2:
        return 0, 0
    return int(parts[0]), int(parts[1])


# --------------------------------------------------------------------------- materialize


def mirror_status(home: Path, cfg: dict) -> dict:
    from . import library

    available = {
        path.parent.name: path.parent for path in sorted(library.skills_dir(home).glob("*/SKILL.md"))
    }
    managed = load_manifest(home).get("skills", {})
    not_mirrored: list[str] = []
    drifted: list[str] = []
    for name, source in available.items():
        entry = managed.get(name)
        if not entry:
            not_mirrored.append(name)
            continue
        if entry.get("sha") != skill_tree_hash(source) or not mirrors_current(name, entry, cfg):
            drifted.append(name)
    orphaned = sorted(set(managed) - set(available))
    return {
        "library": len(available),
        "not_mirrored": sorted(not_mirrored),
        "drifted": sorted(drifted),
        "orphaned": orphaned,
    }


def materialize(home: Path, cfg: dict) -> dict:
    from . import digest

    mirrors = reconcile_mirrors(home, cfg)
    digest_result = digest.export(home, cfg)
    return {"mirrors": mirrors, "digest": digest_result}


# --------------------------------------------------------------------------- scheduled environment


def env_file_path(cfg: dict) -> Path | None:
    raw = str((cfg.get("sync") or {}).get("env_file") or "").strip()
    return Path(raw).expanduser() if raw else None


def parse_env_file(text: str) -> dict[str, str]:
    """Read simple ``KEY=value`` lines (``export`` allowed, quotes stripped).

    Deliberately not a shell: no command substitution, no expansion. Enough
    for the credential files vault helpers and dotfiles setups write.
    """
    values: dict[str, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(path: Path) -> tuple[dict[str, str], str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {}, f"cannot read {path}: {exc}"
    return parse_env_file(text), ""


def apply_environment(cfg: dict) -> str:
    """Give this process the environment a scheduled run needs.

    Loads ``sync.env_file`` (credentials the timer cannot see) and extends PATH
    with the timer PATH and common user bin directories, so git credential
    helpers and other tools resolve the same way they do for the installer.
    Returns an error string when the configured env file cannot be read.
    """
    error = ""
    path = env_file_path(cfg)
    if path:
        values, error = load_env_file(path)
        os.environ.update(values)
    stored = scheduled_path(cfg)
    os.environ["PATH"] = extended_path(os.environ.get("PATH"), stored)
    return error


def _scheduler_env(cfg: dict) -> dict[str, str]:
    """An approximation of the environment a timer sees, for probing.

    Session-scoped services (the ssh agent, the user D-Bus) are available to
    timers, so they are carried over; interactive-shell variables are not.
    """
    env: dict[str, str] = {
        "HOME": str(Path.home()),
        "PATH": scheduled_path(cfg) or extended_path("", None),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": "ssh -o BatchMode=yes",
    }
    for key in ("SSH_AUTH_SOCK", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    path = env_file_path(cfg)
    if path:
        values, _error = load_env_file(path)
        env.update(values)
    return env


def probe_scheduled(home: Path, cfg: dict) -> dict:
    """Fetch with the timer's environment to catch credential/PATH problems."""
    remote = str((cfg.get("sync") or {}).get("remote") or "").strip()
    if not remote:
        return {"ok": None, "at": now_iso(), "detail": "no remote configured"}
    try:
        proc = subprocess.run(
            ["git", "-C", str(home), "fetch", "--quiet", "origin"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            env=_scheduler_env(cfg),
        )
    except FileNotFoundError:
        return {"ok": False, "at": now_iso(), "detail": "git is not installed"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "at": now_iso(), "detail": f"fetch timed out after {GIT_TIMEOUT}s"}
    detail = "" if proc.returncode == 0 else redact_userinfo(tail((proc.stderr or proc.stdout).strip(), 300))
    return {"ok": proc.returncode == 0, "at": now_iso(), "detail": detail}


def store_probe(home: Path, probe: dict) -> None:
    state = load_state(home)
    state["scheduled_probe"] = probe
    save_json(state_path(home), state)


# --------------------------------------------------------------------------- run


def state_path(home: Path) -> Path:
    return home / "state" / "sync.json"


def load_state(home: Path) -> dict:
    return load_json(state_path(home), {}) or {}


def _append_log(home: Path, result: dict) -> None:
    mirrors = (result.get("materialized") or {}).get("mirrors") or {}
    line = (
        f"{result.get('finished_at')} machine={result.get('machine')} code={result.get('code')} "
        f"committed={bool(result.get('committed'))} pulled={bool(result.get('pulled'))} "
        f"pushed={bool(result.get('pushed'))} offline={bool(result.get('offline'))} "
        f"conflict={bool(result.get('conflict'))} "
        f"mirrors={len(mirrors.get('installed', []))}+{len(mirrors.get('updated', []))}~"
        f"{len(mirrors.get('removed', []))}- errors={len(mirrors.get('errors', []))}\n"
    )
    log = home / "logs" / "sync.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(line)


def run(home: Path, cfg: dict) -> dict:
    """Commit, sync with the remote, then materialize. Returns a result dict with `code`."""
    if not repo_exists(home):
        raise PoppyError(f"no sync repo at {home} — run `poppy sync init` first")
    try:
        lock = acquire_lock(home, "sync")
    except PoppyError as exc:
        return {"repo": str(home), "skipped": str(exc), "code": 0}

    sync_cfg = cfg.get("sync") or {}
    machine = machine_name(cfg)
    remote = str(sync_cfg.get("remote") or "").strip()
    branch = str(sync_cfg.get("branch") or "main").strip() or "main"
    result: dict = {"repo": str(home), "machine": machine, "remote": remote, "branch": branch}
    env_file_error = apply_environment(cfg)
    if env_file_error:
        result["env_file_error"] = env_file_error
    try:
        ensure_gitignore(home)
        result["committed"] = commit_local(home, machine)
        ops = remote_ops(home, remote, branch)
        for key in ("fetched", "pulled", "pushed", "offline", "conflict"):
            result[key] = bool(ops.get(key))
        if ops.get("error"):
            result["remote_error"] = redact_userinfo(ops["error"])
        result["materialized"] = materialize(home, cfg)
    finally:
        release_lock(lock)

    mirrors = result["materialized"]["mirrors"]
    result["changed"] = bool(
        result.get("committed")
        or result.get("pulled")
        or result.get("pushed")
        or mirrors["installed"]
        or mirrors["updated"]
        or mirrors["removed"]
    )
    result["last_commit"] = git(home, ["rev-parse", "--short", "HEAD"]).stdout.strip()
    result["finished_at"] = now_iso()
    if result.get("conflict"):
        code = 2
    elif result.get("changed") or result.get("remote_error"):
        code = 1
    else:
        code = 0
    result["code"] = code
    if result.get("conflict"):
        status = "conflict"
    elif result.get("offline"):
        status = "offline"
    elif result.get("remote_error"):
        status = "error"
    elif code == 1:
        status = "synced"
    else:
        status = "ok"
    state = load_state(home)
    state.update(
        {
            "machine": machine,
            "last_run": result["finished_at"],
            "status": status,
            "code": code,
            "last_commit": result["last_commit"],
            "committed": bool(result.get("committed")),
            "pulled": bool(result.get("pulled")),
            "pushed": bool(result.get("pushed")),
            "offline": bool(result.get("offline")),
            "error": redact_userinfo(result.get("remote_error") or "") or None,
            "env_file_error": result.get("env_file_error") or None,
            "mirrors": {
                key: len(mirrors.get(key) or []) for key in ("installed", "updated", "removed", "errors")
            },
        }
    )
    save_json(state_path(home), state)
    _append_log(home, result)
    return result


def init(
    home: Path,
    cfg: dict,
    remote: str | None = None,
    branch: str | None = None,
    machine: str | None = None,
    with_schedule: bool = True,
) -> dict:
    """Initialize (or update) the sync repo, run one sync, and schedule auto-sync."""
    ensure_home_layout(home)
    from . import library as library_mod

    library_mod.ensure_library(home)

    sync_cfg = cfg.setdefault("sync", {})
    if branch:
        sync_cfg["branch"] = branch
    if machine:
        sync_cfg["machine"] = machine
    if remote is not None:
        sync_cfg["remote"] = remote
    sync_cfg.setdefault("branch", "main")
    sync_cfg["enabled"] = True
    sync_cfg["schedule"] = with_schedule
    save_config(home, cfg)

    branch_name = str(sync_cfg.get("branch") or "main")
    machine_id = machine_name(cfg)
    created = False
    if not repo_exists(home):
        git(home, ["init", "-q"], check=True)
        git(home, ["symbolic-ref", "HEAD", f"refs/heads/{branch_name}"], check=True)
        created = True
    ensure_gitignore(home)
    ensure_identity(home, machine_id)

    remote_url = str(sync_cfg.get("remote") or "").strip()
    if remote_url:
        existing = git(home, ["remote", "get-url", "origin"])
        if existing.returncode == 0:
            if existing.stdout.strip() != remote_url:
                git(home, ["remote", "set-url", "origin", remote_url], check=True)
        else:
            git(home, ["remote", "add", "origin", remote_url], check=True)

    commit_local(home, machine_id, allow_empty=True)
    result = run(home, cfg)
    out = {
        "created": created,
        "branch": branch_name,
        "remote": remote_url,
        "machine": machine_id,
        **result,
    }
    if with_schedule:
        try:
            sched = schedule_install(home, cfg, include_mine=False, include_sync=True)
            out["schedule"] = {
                "installed": True,
                "kind": sched.get("kind"),
                "interval_min": sync_interval_minutes(cfg),
                "files": list(sched.get("files") or {}),
            }
        except Exception as exc:  # a timer failure must not undo a successful sync
            out["schedule"] = {"installed": False, "error": str(exc)}
    else:
        out["schedule"] = {"installed": False, "skipped": True}
    if remote_url and (out.get("schedule") or {}).get("installed"):
        try:
            probe = probe_scheduled(home, cfg)
            store_probe(home, probe)
            out["scheduled_probe"] = probe
        except Exception as exc:  # a probe failure must not undo a successful sync
            out["scheduled_probe"] = {"ok": False, "at": now_iso(), "detail": str(exc)}
    return out


# --------------------------------------------------------------------------- status


def status(home: Path, cfg: dict) -> dict:
    sync_cfg = cfg.get("sync") or {}
    branch = str(sync_cfg.get("branch") or "main")
    out: dict = {
        "repo": str(home),
        "initialized": repo_exists(home),
        "enabled": bool(sync_cfg.get("enabled")),
        "machine": machine_name(cfg),
        "remote": str(sync_cfg.get("remote") or "").strip(),
        "branch": branch,
        "dirty": False,
        "ahead": 0,
        "behind": 0,
        "rebase_in_progress": False,
        "mirrors": {},
    }
    state = load_state(home)
    out["last_run"] = state.get("last_run")
    out["last_status"] = state.get("status")
    out["last_commit"] = state.get("last_commit")
    out["last_error"] = state.get("error")
    out["env_file_error"] = state.get("env_file_error")
    out["scheduled_probe"] = state.get("scheduled_probe")
    sched = schedule_status(home, cfg)
    sync_sched = sched.get("sync") or {}
    out["schedule"] = {
        "installed": bool(sync_sched.get("installed")),
        "detail": sync_sched.get("detail"),
        "interval_min": sync_interval_minutes(cfg),
    }
    if not out["initialized"]:
        return out

    paths = tracked_existing(home)
    if paths:
        dirty = git(home, ["status", "--porcelain", "--", *paths])
        out["dirty"] = bool(dirty.stdout.strip())
    out["rebase_in_progress"] = any(
        (home / ".git" / name).exists() for name in ("rebase-merge", "rebase-apply")
    )
    remote_ref = f"origin/{branch}"
    if git(home, ["rev-parse", "--verify", "-q", remote_ref]).returncode == 0:
        out["behind"], out["ahead"] = _counts(home, remote_ref)
    out["mirrors"] = mirror_status(home, cfg)
    return out
