#!/usr/bin/env python3
"""Render the Homebrew formula for a built sdist.

Usage: render_homebrew_formula.py <sdist.tar.gz> [output.rb]

PyPI's file URL is derived from the file's blake2b-256 digest, so the formula
can be rendered before the upload finishes; the sha256 matches the uploaded
artifact.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "packaging" / "homebrew" / "poppy-ai.rb.template"


def files_url(path: Path) -> str:
    digest = hashlib.blake2b(path.read_bytes(), digest_size=32).hexdigest()
    return f"https://files.pythonhosted.org/packages/{digest[:2]}/{digest[2:4]}/{digest[4:]}/{path.name}"


def render(sdist: Path) -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    return text.replace("__SDIST_URL__", files_url(sdist)).replace(
        "__SDIST_SHA256__", hashlib.sha256(sdist.read_bytes()).hexdigest()
    )


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print("usage: render_homebrew_formula.py <sdist.tar.gz> [output.rb]", file=sys.stderr)
        return 2
    sdist = Path(argv[1])
    if not sdist.is_file():
        print(f"no such file: {sdist}", file=sys.stderr)
        return 1
    formula = render(sdist)
    if len(argv) == 3:
        output = Path(argv[2])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(formula, encoding="utf-8")
        print(f"wrote {output}")
    else:
        print(formula, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
