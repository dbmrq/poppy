"""SKILL.md frontmatter parsing, validation, installation, and manifests."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .candidates import drafts_candidate_dir
from .util import PoppyError, load_json, now_iso, save_json, scan_secrets, sha

NAME_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
NAME_MAX = 64
DESCRIPTION_MAX = 1024
BODY_LINE_WARN = 500
FILE_SIZE_MAX = 64 * 1024


def parse_frontmatter(text: str) -> tuple[dict, str, list[str]]:
    """Parse a minimal YAML frontmatter (strings and folded/literal blocks)."""
    errors: list[str] = []
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, ["missing frontmatter (file must start with ---)"]
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        return {}, text, ["unterminated frontmatter"]

    meta: dict[str, str] = {}
    index = 1
    while index < end:
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if line[:1] in (" ", "\t"):
            errors.append(f"unexpected indented line in frontmatter: {line.strip()!r}")
            index += 1
            continue
        if ":" not in line:
            errors.append(f"invalid frontmatter line: {line.strip()!r}")
            index += 1
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value in (">", ">-", ">+", "|", "|-", "|+"):
            fold = value.startswith(">")
            index += 1
            block: list[str] = []
            while index < end:
                candidate = lines[index]
                if candidate.strip() and not candidate[:1] in (" ", "\t"):
                    break
                block.append(candidate)
                index += 1
            indents = [len(item) - len(item.lstrip()) for item in block if item.strip()]
            cut = min(indents) if indents else 0
            block = [item[cut:] if item.strip() else "" for item in block]
            if fold:
                value = " ".join(item.strip() for item in block if item.strip())
            else:
                value = "\n".join(block).strip("\n")
        else:
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            index += 1
        meta[key] = value

    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return meta, body, errors


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
            warnings.append("description does not say when to use the skill (\"Use when ...\")")

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


def manifest_path(home: Path) -> Path:
    return home / "installed.json"


def load_manifest(home: Path) -> dict:
    return load_json(manifest_path(home), {"skills": {}}) or {"skills": {}}


def save_manifest(home: Path, data: dict) -> None:
    save_json(manifest_path(home), data)


def install_draft(home: Path, cfg: dict, candidate: dict) -> tuple[str, list[str]]:
    draft_dir = drafts_candidate_dir(home, candidate["id"])
    skill_md = draft_dir / "SKILL.md"
    if not skill_md.is_file():
        raise PoppyError(f"draft for {candidate['id']} has no SKILL.md")
    text = skill_md.read_text(encoding="utf-8")
    meta, _body, errors, warnings = validate_skill(text)
    if errors:
        raise PoppyError("draft failed validation: " + "; ".join(errors))
    name = meta["name"]

    dirs = cfg.get("skills_dirs") or []
    if not dirs:
        raise PoppyError("no skills_dirs configured (run the installer prompt or set skills_dirs)")
    manifest = load_manifest(home)
    managed = manifest.get("skills", {})
    target_paths: list[str] = []
    for entry in dirs:
        target_root = Path(entry).expanduser()
        target_root.mkdir(parents=True, exist_ok=True)
        destination = target_root / name
        if destination.exists():
            if name in managed:
                shutil.rmtree(destination)
            else:
                raise PoppyError(
                    f"{destination} already exists and was not installed by poppy; refusing to overwrite"
                )
        shutil.copytree(
            draft_dir,
            destination,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "REJECT.md"),
        )
        target_paths.append(str(destination))

    managed[name] = {
        "candidate": candidate["id"],
        "title": candidate.get("title", ""),
        "dirs": target_paths,
        "installed_at": now_iso(),
        "sha": sha(text, 16),
    }
    manifest["skills"] = managed
    save_manifest(home, manifest)
    return name, target_paths


def uninstall_skill(home: Path, name: str) -> list[str]:
    manifest = load_manifest(home)
    managed = manifest.get("skills", {})
    if name not in managed:
        raise PoppyError(f"{name} is not a poppy-managed skill")
    entry = managed[name]
    removed = []
    for path_text in entry.get("dirs", []):
        path = Path(path_text)
        if not path.exists():
            continue
        skill_md = path / "SKILL.md"
        if not skill_md.is_file():
            raise PoppyError(f"refusing to remove {path}: no SKILL.md")
        meta, _body, _errors = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        if str(meta.get("name", "")).strip() != name:
            raise PoppyError(f"refusing to remove {path}: skill name mismatch")
        shutil.rmtree(path)
        removed.append(str(path))
    del managed[name]
    manifest["skills"] = managed
    save_manifest(home, manifest)
    return removed
