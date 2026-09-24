"""Headless agent invocation.

The installer stores an argv template per role in config; ``{prompt}`` is
replaced inline, or ``{prompt_file}`` with a temp file path. If neither is
present, the prompt is fed on stdin.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .schedule import scheduled_path
from .util import PoppyError, atomic_write_text, extended_path, tail, temp_text_file


@dataclass
class AgentResult:
    role: str
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_sec: float
    timed_out: bool = False


def agent_spec(cfg: dict, role: str) -> tuple[list | None, int]:
    spec = (cfg.get("agent") or {}).get(role) or {}
    cmd = spec.get("cmd")
    if cmd is not None and not isinstance(cmd, list):
        raise PoppyError(f"agent.{role}.cmd must be a JSON array of strings")
    return cmd, int(spec.get("timeout_sec", 1800))


def _build_argv(cmd: list[str], prompt: str) -> tuple[list[str], str | None, list[Path]]:
    argv: list[str] = []
    stdin_text: str | None = None
    temp_files: list[Path] = []
    used = False
    for part in cmd:
        patched = str(part)
        if "{prompt_file}" in patched:
            path = temp_text_file("poppy-prompt-", prompt)
            temp_files.append(path)
            patched = patched.replace("{prompt_file}", str(path))
            used = True
        if "{prompt}" in patched:
            patched = patched.replace("{prompt}", prompt)
            used = True
        argv.append(patched)
    if not used:
        stdin_text = prompt
    return argv, stdin_text, temp_files


def format_result(result: AgentResult) -> str:
    lines = [
        f"role: {result.role}",
        f"argv: {result.argv}",
        f"exit: {result.returncode}{' (timed out)' if result.timed_out else ''}",
        f"duration: {result.duration_sec:.1f}s",
        "",
        "--- stdout (tail) ---",
        tail(result.stdout, 4000),
        "",
        "--- stderr (tail) ---",
        tail(result.stderr, 2000),
        "",
    ]
    return "\n".join(lines)


def run_agent(
    role: str,
    prompt: str,
    cfg: dict,
    home: Path,
    env_extra: dict | None = None,
    log_path: Path | None = None,
) -> AgentResult:
    cmd, timeout = agent_spec(cfg, role)
    if not cmd:
        raise PoppyError(
            f"agent.{role}.cmd is not configured — run the installer prompt, or set it with "
            f"`poppy config set agent.{role}.cmd '[\"your-agent\", \"...\", \"{{prompt}}\"]'`"
        )
    argv, stdin_text, temp_files = _build_argv(cmd, prompt)
    env = os.environ.copy()
    env["POPPY_HOME"] = str(home)
    # Scheduled runs start from a minimal PATH; add the timer PATH and common
    # user bin directories so the command the installer verified still resolves.
    env["PATH"] = extended_path(env.get("PATH"), scheduled_path(cfg))
    env.update(env_extra or {})

    started = time.time()
    timed_out = False
    try:
        proc = subprocess.run(
            argv, input=stdin_text, capture_output=True, text=True, timeout=timeout, env=env
        )
        returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError as exc:
        raise PoppyError(f"agent.{role}: command not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = -1
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", "replace")
        stderr = (stderr or "") + f"\n[poppy] timed out after {timeout}s"
    finally:
        for path in temp_files:
            try:
                path.unlink()
            except OSError:
                pass

    result = AgentResult(
        role=role,
        argv=argv,
        returncode=returncode,
        stdout=stdout or "",
        stderr=stderr or "",
        duration_sec=time.time() - started,
        timed_out=timed_out,
    )
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(log_path, format_result(result))
    return result


def test_agent(role: str, cfg: dict, home: Path) -> tuple[bool, str]:
    """Run the configured command with a trivial prompt and check for the reply."""
    try:
        result = run_agent(role, "Reply with exactly POPPY_OK and nothing else.", cfg, home)
    except PoppyError as exc:
        return False, str(exc)
    if result.timed_out:
        return False, f"timed out after {result.duration_sec:.0f}s"
    if result.returncode != 0:
        return False, f"exit code {result.returncode}: {tail(result.stderr, 300)}"
    if "POPPY_OK" not in result.stdout:
        return False, f"command ran but did not reply as expected: {tail(result.stdout, 300)!r}"
    return True, "ok"
