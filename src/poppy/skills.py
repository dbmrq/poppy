"""SKILL.md validation, library installation, harness mirrors, and manifests."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .candidates import drafts_candidate_dir
from .frontmatter import parse_frontmatter  # re-exported for convenience
from .util import PoppyError, load_json, now_iso, save_json, scan_secrets, sha

__all__ = [
    "parse_frontmatter",
    "validate_skill",
    "install_draft",
    "install_builtin_skill",
    "uninstall_skill",
    "archive_skill",
    "restore_skill",
    "mirror_skill",
    "remove_mirrors",
    "reconcile_mirrors",
    "skill_tree_hash",
    "expected_mirrors",
    "mirrors_current",
    "adopt_installed",
    "load_manifest",
    "save_manifest",
    "skills_dir_paths",
    "manifest_path",
]

NAME_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
NAME_MAX = 64
DESCRIPTION_MAX = 1024
BODY_LINE_WARN = 500
FILE_SIZE_MAX = 64 * 1024

COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "REJECT.md")


def validate_skill(text: str, expected_name: str | None = None) -> tuple[dict, str, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    meta, body, fm_errors = parse_frontmatter(text)
    errors.extend(fm_errors)

    name = str(meta.get("name", "")).strip()
    description = str(meta.get("description", "")).strip()

    if not name:
        errors.append("frontmatter: missing name")
    elif not NAME_PATTERN.fullmatch(name):
        errors.append("frontmatter: name must be kebab-case ([a-z0-9-])")
    elif len(name) > NAME_MAX:
        errors.append(f"frontmatter: name longer than {NAME_MAX} chars")
    if expected_name and name and name != expected_name:
        warnings.append(f"name {name!r} differs from expected {expected_name!r}")

    if not description:
        errors.append("frontmatter: missing description")
    else:
        if len(description) > DESCRIPTION_MAX:
            errors.append(f"description longer than {DESCRIPTION_MAX} chars")
        if "use when" not in description.lower():
            warnings.append('description does not say when to use the skill ("Use when ...")')

    if not body.strip():
        errors.append("empty body")
    else:
        body_lines = len(body.splitlines())
        if body_lines > BODY_LINE_WARN:
            warnings.append(f"body is longer than {BODY_LINE_WARN} lines")

    size = len(text.encode("utf-8"))
    if size > FILE_SIZE_MAX:
        errors.append(f"skill is larger than {FILE_SIZE_MAX // 1024} KB")

    for severity, label, _snippet in scan_secrets(text):
        if severity == "high":
            errors.append(f"secret detected: {label}")
        else:
            warnings.append(f"possible secret: {label}")

    return meta, body, errors, warnings


# --------------------------------------------------------------------------- manifests


def manifest_path(home: Path) -> Path:
    return home / "installed.json"


def load_manifest(home: Path) -> dict:
    return load_json(manifest_path(home), {"skills": {}}) or {"skills": {}}


def save_manifest(home: Path, data: dict) -> None:
    save_json(manifest_path(home), data)


def skills_dir_paths(cfg: dict) -> list[str]:
    """Resolve skills_dirs config into a list of paths.

    Poppy always copies skills, never symlinks them: symlinked skill
    directories behave inconsistently across harnesses and installers.
    Dict entries are tolerated for compatibility (their ``path`` is used).
    """
    out: list[str] = []
    for item in cfg.get("skills_dirs") or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and item.get("path"):
            out.append(str(item["path"]))
    return out


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


# --------------------------------------------------------------------------- mirrors


def _check_mirror_targets(cfg: dict, name: str, managed: dict) -> None:
    """Refuse to clobber directories that poppy does not manage."""
    if name in managed:
        return
    for entry in skills_dir_paths(cfg):
        destination = Path(entry).expanduser() / name
        if destination.exists() or destination.is_symlink():
            raise PoppyError(
                f"{destination} already exists and was not installed by poppy; refusing to overwrite"
            )


def skill_tree_hash(path: Path) -> str:
    """Stable hash of a skill directory's contents (relative paths + bytes).

    Used to detect any change to a library skill — SKILL.md or support files —
    so mirrors can be refreshed and drift reported.
    """
    parts = []
    for file in sorted(p for p in path.rglob("*") if p.is_file() and ".git" not in p.parts):
        relative = file.relative_to(path).as_posix()
        parts.append(f"{relative}:{sha(file.read_bytes().decode('utf-8', 'replace'), 16)}")
    return sha("\n".join(parts), 16)


def mirror_skill(home: Path, cfg: dict, name: str) -> list[str]:
    from . import library

    source = library.skills_dir(home) / name
    if not (source / "SKILL.md").is_file():
        raise PoppyError(f"skill not in library: {name}")
    manifest = load_manifest(home)
    managed = manifest.get("skills", {})
    dirs: list[str] = []
    for entry in skills_dir_paths(cfg):
        root = Path(entry).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        destination = root / name
        if destination.exists() or destination.is_symlink():
            if name in managed:
                _remove_path(destination)
            else:
                raise PoppyError(
                    f"{destination} already exists and was not installed by poppy; refusing to overwrite"
                )
        shutil.copytree(source, destination, ignore=COPY_IGNORE)
        dirs.append(str(destination))
    return dirs


def remove_mirrors(home: Path, name: str) -> list[str]:
    manifest = load_manifest(home)
    entry = (manifest.get("skills") or {}).get(name) or {}
    removed: list[str] = []
    for path_text in entry.get("dirs", []):
        path = Path(path_text)
        if path.is_symlink():
            path.unlink()
            removed.append(str(path))
        elif path.is_dir():
            skill_md = path / "SKILL.md"
            if not skill_md.is_file():
                raise PoppyError(f"refusing to remove {path}: no SKILL.md")
            meta, _body, _errors = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
            if str(meta.get("name", "")).strip() != name:
                raise PoppyError(f"refusing to remove {path}: skill name mismatch")
            shutil.rmtree(path)
            removed.append(str(path))
    return removed


def expected_mirrors(name: str, cfg: dict) -> list[str]:
    """Destination paths for *name* given the current skills_dirs config."""
    return [str(Path(entry).expanduser() / name) for entry in skills_dir_paths(cfg)]


def mirrors_current(name: str, entry: dict, cfg: dict) -> bool:
    """True when the recorded mirrors match the configured destinations and exist."""
    recorded = [str(Path(path_text)) for path_text in entry.get("dirs", [])]
    if sorted(recorded) != sorted(expected_mirrors(name, cfg)):
        return False
    return all(Path(path_text).is_dir() for path_text in recorded)


# --------------------------------------------------------------------------- install


def install_draft(home: Path, cfg: dict, candidate: dict) -> tuple[str, list[str]]:
    from . import library

    draft_dir = drafts_candidate_dir(home, candidate["id"])
    skill_md = draft_dir / "SKILL.md"
    if not skill_md.is_file():
        raise PoppyError(f"draft for {candidate['id']} has no SKILL.md")
    text = skill_md.read_text(encoding="utf-8")
    meta, _body, errors, _warnings = validate_skill(text)
    if errors:
        raise PoppyError("draft failed validation: " + "; ".join(errors))
    name = meta["name"]

    manifest = load_manifest(home)
    managed = manifest.setdefault("skills", {})
    _check_mirror_targets(cfg, name, managed)

    library.ensure_library(home)
    target = library.skills_dir(home) / name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    shutil.copytree(draft_dir, target, dirs_exist_ok=True, ignore=COPY_IGNORE)

    managed_value = {
        "candidate": candidate["id"],
        "title": candidate.get("title", ""),
        "library": str(target),
        "dirs": [],
        "installed_at": now_iso(),
        "sha": skill_tree_hash(target),
    }
    managed[name] = managed_value
    save_manifest(home, manifest)

    dirs = mirror_skill(home, cfg, name)
    managed_value["dirs"] = dirs
    save_manifest(home, manifest)
    return name, dirs


def install_builtin_skill(home: Path, cfg: dict, name: str) -> list[str]:
    from . import library

    library.ensure_library(home)
    source = library.skills_dir(home) / name
    if not (source / "SKILL.md").is_file():
        raise PoppyError(f"builtin skill missing from library: {name}")
    manifest = load_manifest(home)
    managed = manifest.setdefault("skills", {})
    _check_mirror_targets(cfg, name, managed)
    managed[name] = {
        "candidate": None,
        "title": "Poppy builtin",
        "builtin": True,
        "library": str(source),
        "dirs": [],
        "installed_at": now_iso(),
        "sha": skill_tree_hash(source),
    }
    save_manifest(home, manifest)
    dirs = mirror_skill(home, cfg, name)
    managed[name]["dirs"] = dirs
    save_manifest(home, manifest)
    return dirs


def archive_skill(home: Path, name: str) -> list[str]:
    """Remove mirrors and move the library copy to the archive (never delete)."""
    from . import library

    manifest = load_manifest(home)
    managed = manifest.get("skills", {})
    removed = remove_mirrors(home, name)
    library_copy = library.skills_dir(home) / name
    if library_copy.is_dir():
        entry = library.Entry(kind="skill", id=name, path=library_copy / "SKILL.md", meta={"name": name}, body="")
        library.archive_entry(home, entry)
        removed.append(f"{library_copy} -> archive")
    managed.pop(name, None)
    manifest["skills"] = managed
    save_manifest(home, manifest)
    return removed


def uninstall_skill(home: Path, name: str) -> list[str]:
    manifest = load_manifest(home)
    if name not in (manifest.get("skills") or {}):
        raise PoppyError(f"{name} is not a poppy-managed skill")
    return archive_skill(home, name)


def restore_skill(home: Path, cfg: dict, name: str) -> list[str]:
    """Move an archived skill back into the library and mirror it again."""
    from . import library

    source = library.archive_dir(home) / "skills" / name
    if not (source / "SKILL.md").is_file():
        raise PoppyError(f"no archived skill named {name}")
    destination = library.skills_dir(home) / name
    if destination.exists():
        shutil.rmtree(destination)
    shutil.move(str(source), str(destination))
    manifest = load_manifest(home)
    managed = manifest.setdefault("skills", {})
    managed[name] = {
        "candidate": None,
        "title": "restored",
        "library": str(destination),
        "dirs": [],
        "installed_at": now_iso(),
        "sha": skill_tree_hash(destination),
    }
    save_manifest(home, manifest)
    dirs = mirror_skill(home, cfg, name)
    managed[name]["dirs"] = dirs
    save_manifest(home, manifest)
    return dirs


def adopt_installed(home: Path, cfg: dict) -> list[str]:
    """V1 migration: copy installed mirrors that predate the library into it."""
    from . import library

    manifest = load_manifest(home)
    managed = manifest.get("skills", {})
    library.ensure_library(home)
    adopted: list[str] = []
    for name, entry in managed.items():
        if entry.get("library"):
            continue
        source = None
        for path_text in entry.get("dirs", []):
            candidate_path = Path(path_text)
            if (candidate_path / "SKILL.md").is_file():
                source = candidate_path
                break
        if source is None:
            continue
        target = library.skills_dir(home) / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, ignore=COPY_IGNORE)
        entry["library"] = str(target)
        adopted.append(name)
    if adopted:
        save_manifest(home, manifest)
    return adopted


def reconcile_mirrors(home: Path, cfg: dict) -> dict:
    """Make harness mirrors match the library: install, refresh, remove orphans.

    Idempotent and safe: a directory poppy does not manage is never
    overwritten. Used after a sync pull (and by `poppy sync run`) so a machine
    materializes whatever arrived from the shared repo.
    """
    from . import library

    library.ensure_library(home)
    manifest = load_manifest(home)
    managed = manifest.setdefault("skills", {})
    available = {
        path.parent.name: path.parent for path in sorted(library.skills_dir(home).glob("*/SKILL.md"))
    }
    result: dict = {"installed": [], "updated": [], "removed": [], "errors": []}

    for name in sorted(set(managed) - set(available)):
        try:
            remove_mirrors(home, name)
            managed.pop(name, None)
            result["removed"].append(name)
        except PoppyError as exc:
            result["errors"].append(f"{name}: {exc}")

    for name in sorted(available):
        source = available[name]
        tree_sha = skill_tree_hash(source)
        entry = managed.get(name) or {}
        if entry.get("sha") == tree_sha and mirrors_current(name, entry, cfg):
            continue
        try:
            if entry:
                remove_mirrors(home, name)
            dirs = mirror_skill(home, cfg, name)
            refreshed = {
                "candidate": entry.get("candidate"),
                "title": entry.get("title") or name,
                "library": str(source),
                "dirs": dirs,
                "installed_at": entry.get("installed_at") or now_iso(),
                "sha": tree_sha,
            }
            if entry.get("builtin"):
                refreshed["builtin"] = True
            managed[name] = refreshed
            result["updated" if entry else "installed"].append(name)
        except PoppyError as exc:
            result["errors"].append(f"{name}: {exc}")

    save_manifest(home, manifest)
    return result
