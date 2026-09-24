"""Prompt rendering: {{PLACEHOLDER}} substitution from prompts/*.md."""

from __future__ import annotations

import re

from .util import PoppyError, REPO_ROOT

TOKEN = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


def render(name: str, variables: dict) -> str:
    path = REPO_ROOT / "prompts" / f"{name}.md"
    if not path.is_file():
        raise PoppyError(f"missing prompt file: {path}")
    text = path.read_text(encoding="utf-8")
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", str(value))
    missing = sorted(set(TOKEN.findall(text)))
    if missing:
        raise PoppyError(f"prompt {name} has unresolved placeholders: {', '.join(missing)}")
    return text
