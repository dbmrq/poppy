"""Minimal YAML frontmatter parsing and writing (stdlib only)."""

from __future__ import annotations

import json

_NEEDS_QUOTE = set(':#{}[]&*?|>!%@`"\'')
_RESERVED = {"true", "false", "null", "yes", "no", "on", "off", "~"}


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
                if value.startswith('"'):
                    try:
                        value = json.loads('"' + value + '"')
                    except json.JSONDecodeError:
                        pass
            index += 1
        meta[key] = value

    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return meta, body, errors


def scalar(value) -> str:
    text = str(value)
    needs_quote = (
        text == ""
        or text != text.strip()
        or text.lower() in _RESERVED
        or text.startswith("-")
        or any(char in text for char in _NEEDS_QUOTE)
    )
    return json.dumps(text) if needs_quote else text


def dump_frontmatter(meta: dict, body: str, order: list[str] | None = None) -> str:
    keys: list[str] = []
    for key in order or []:
        if key in meta and meta[key] not in (None, ""):
            keys.append(key)
    for key in meta:
        if key not in keys and meta[key] not in (None, ""):
            keys.append(key)
    lines = ["---"]
    for key in keys:
        lines.append(f"{key}: {scalar(meta[key])}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.strip() + "\n"
