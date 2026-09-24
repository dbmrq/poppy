#!/usr/bin/env python3
"""Fail when a release tag does not match poppy.__version__.

Usage: check_version.py <tag>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INIT = REPO_ROOT / "src" / "poppy" / "__init__.py"


def package_version() -> str:
    match = re.search(r'__version__\s*=\s*"([^"]+)"', INIT.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit(f"cannot find __version__ in {INIT}")
    return match.group(1)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_version.py <tag>", file=sys.stderr)
        return 2
    tag = argv[1].removeprefix("v")
    version = package_version()
    if tag != version:
        print(f"tag {tag!r} does not match poppy.__version__ {version!r}", file=sys.stderr)
        return 1
    print(f"version {version} ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
