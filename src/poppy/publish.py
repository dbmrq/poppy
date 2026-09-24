"""Publish reviewed skills from the private library to a public skills repo.

Publishing is explicit and one skill at a time: the library is the private
working set, a public repo is a curated subset. Poppy copies the skill into a
local checkout of the public repo (optionally committing and pushing), refuses
to overwrite a destination that differs unless asked, and warns when a skill
embeds machine-specific details.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import library
from .skills import COPY_IGNORE, skill_tree_hash, validate_skill
from .sync import git
from .util import PoppyError, tail

PUSH_TIMEOUT = 180


def _machine_warnings(text: str) -> list[str]:
    warnings: list[str] = []
    home = str(Path.home())
    if home in text:
        warnings.append(f"contains this machine's home path ({home})")
    host = library.host_name()
    if host and re.search(rf"\b{re.escape(host)}\b", text):
        warnings.append(f"mentions this machine's hostname ({host})")
    return warnings


def _scan_machine_specific(path: Path) -> list[str]:
    warnings: list[str] = []
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        text = file.read_bytes().decode("utf-8", "replace")
        for warning in _machine_warnings(text):
            warnings.append(f"{file.relative_to(path).as_posix()}: {warning}")
    return warnings


def publish(
    home: Path,
    cfg: dict,
    name: str,
    target: str | None = None,
    subdir: str | None = None,
    commit: bool = False,
    push: bool = False,
    force: bool = False,
) -> dict:
    """Copy one library skill into a public-repo checkout. Returns a result dict."""
    if push:
        commit = True

    source = library.skills_dir(home) / name
    if not (source / "SKILL.md").is_file():
        if (library.archive_dir(home) / "skills" / name).is_dir():
            raise PoppyError(f"{name} is archived — restore it first (`poppy library restore {name}`)")
        raise PoppyError(f"skill not in library: {name}")

    text = (source / "SKILL.md").read_text(encoding="utf-8")
    _meta, _body, errors, validation_warnings = validate_skill(text, expected_name=name)
    if errors:
        raise PoppyError("skill failed validation: " + "; ".join(errors))

    publish_cfg = cfg.get("publish") or {}
    target_text = str(target or publish_cfg.get("target") or "").strip()
    if not target_text:
        raise PoppyError(
            "no publish target — pass --to <checkout> or set `poppy config set publish.target <path>`"
        )
    subdir_text = str(subdir if subdir is not None else publish_cfg.get("subdir") or "").strip()
    root = Path(target_text).expanduser().resolve()
    if not root.is_dir():
        raise PoppyError(f"publish target is not a directory: {root}")

    destination = root / subdir_text / name if subdir_text else root / name
    relative = destination.relative_to(root).as_posix()

    source_hash = skill_tree_hash(source)
    action = "added"
    if destination.is_symlink():
        raise PoppyError(f"{destination} is a symlink; refusing to publish into it")
    if destination.exists():
        if not destination.is_dir():
            raise PoppyError(f"{destination} exists and is not a directory")
        if skill_tree_hash(destination) == source_hash:
            return {
                "name": name,
                "path": str(destination),
                "action": "unchanged",
                "warnings": [],
                "committed": False,
                "pushed": False,
            }
        if not force:
            raise PoppyError(
                f"{destination} exists and differs — review it, or re-run with --force to overwrite"
            )
        shutil.rmtree(destination)
        action = "updated"

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, ignore=COPY_IGNORE)

    warnings = list(validation_warnings) + _scan_machine_specific(destination)
    result = {
        "name": name,
        "path": str(destination),
        "action": action,
        "warnings": warnings,
        "committed": False,
        "pushed": False,
    }
    if not commit:
        return result

    if git(root, ["rev-parse", "--show-toplevel"]).returncode != 0:
        raise PoppyError(f"--commit needs a git repository; {root} is not one")
    git(root, ["add", "--", relative], check=True)
    status = git(root, ["status", "--porcelain", "--", relative])
    if status.stdout.strip():
        git(root, ["commit", "-q", "-m", f"{action} skill: {name} (poppy)"], check=True)
        result["committed"] = True
    if push:
        pushed = git(root, ["push"], timeout=PUSH_TIMEOUT)
        if pushed.returncode != 0:
            detail = tail((pushed.stderr or pushed.stdout).strip(), 300)
            raise PoppyError(f"push failed: {detail}")
        result["pushed"] = True
    return result
