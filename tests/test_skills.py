import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy.skills import (  # noqa: E402
    install_draft,
    load_manifest,
    parse_frontmatter,
    uninstall_skill,
    validate_skill,
)
from poppy.util import ensure_home_layout  # noqa: E402

GOOD = """---
name: frozen-lockfile-builds
description: >-
  Make CI builds reproducible with a frozen lockfile. Use when a build works
  locally but fails on CI with dependency drift.
---

# Frozen lockfile builds

Pass `--frozen-lockfile` to the package manager before building.
"""


class TestFrontmatter(unittest.TestCase):
    def test_folded_description(self):
        meta, body, errors = parse_frontmatter(GOOD)
        self.assertEqual(errors, [])
        self.assertEqual(meta["name"], "frozen-lockfile-builds")
        self.assertIn("Use when", meta["description"])
        self.assertIn("Pass `--frozen-lockfile`", body)

    def test_literal_block(self):
        text = "---\nname: x\ndescription: |\n  line one\n  line two\n---\n\nbody\n"
        meta, _body, errors = parse_frontmatter(text)
        self.assertEqual(errors, [])
        self.assertEqual(meta["description"], "line one\nline two")

    def test_missing_frontmatter(self):
        _meta, _body, errors = parse_frontmatter("no frontmatter here")
        self.assertTrue(errors)

    def test_quoted_value(self):
        text = '---\nname: "quoted-name"\ndescription: \'quoted desc\'\n---\n\nbody\n'
        meta, _body, errors = parse_frontmatter(text)
        self.assertEqual(errors, [])
        self.assertEqual(meta["name"], "quoted-name")
        self.assertEqual(meta["description"], "quoted desc")


class TestValidate(unittest.TestCase):
    def test_good(self):
        _meta, _body, errors, warnings = validate_skill(GOOD)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_missing_name(self):
        _meta, _body, errors, _warnings = validate_skill("---\ndescription: x\n---\n\nbody\n")
        self.assertTrue(any("name" in error for error in errors))

    def test_bad_name(self):
        text = "---\nname: Bad_Name\ndescription: x Use when y\n---\n\nbody\n"
        _meta, _body, errors, _warnings = validate_skill(text)
        self.assertTrue(any("kebab" in error for error in errors))

    def test_missing_description(self):
        _meta, _body, errors, _warnings = validate_skill("---\nname: fine\n---\n\nbody\n")
        self.assertTrue(any("description" in error for error in errors))

    def test_missing_trigger_warning(self):
        text = "---\nname: fine\ndescription: does something\n---\n\nbody\n"
        _meta, _body, errors, warnings = validate_skill(text)
        self.assertEqual(errors, [])
        self.assertTrue(any("when" in warning for warning in warnings))

    def test_secret_rejected(self):
        text = "---\nname: fine\ndescription: x Use when y\n---\n\nsk-" + "a" * 30 + "\n"
        _meta, _body, errors, _warnings = validate_skill(text)
        self.assertTrue(any("secret" in error for error in errors))


class TestInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        ensure_home_layout(self.home)
        self.skills_dir = Path(self.tmp.name) / "skills"
        draft_dir = self.home / "drafts" / "cand1"
        draft_dir.mkdir(parents=True)
        (draft_dir / "SKILL.md").write_text(GOOD, encoding="utf-8")
        self.cfg = {"skills_dirs": [str(self.skills_dir)]}
        self.candidate = {"id": "cand1", "title": "Frozen lockfile builds"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_manifest_uninstall(self):
        name, dirs = install_draft(self.home, self.cfg, self.candidate)
        self.assertEqual(name, "frozen-lockfile-builds")
        self.assertTrue((self.skills_dir / name / "SKILL.md").is_file())
        manifest = load_manifest(self.home)
        self.assertIn(name, manifest["skills"])

        # reinstalling over a managed skill is allowed
        install_draft(self.home, self.cfg, self.candidate)

        removed = uninstall_skill(self.home, name)
        self.assertEqual(len(removed), 1)
        self.assertFalse((self.skills_dir / name).exists())
        self.assertNotIn(name, load_manifest(self.home)["skills"])

    def test_refuses_unmanaged_overwrite(self):
        unmanaged = self.skills_dir / "frozen-lockfile-builds"
        unmanaged.mkdir(parents=True)
        with self.assertRaises(Exception):
            install_draft(self.home, self.cfg, self.candidate)


if __name__ == "__main__":
    unittest.main()
