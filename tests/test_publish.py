import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy import library, publish  # noqa: E402
from poppy.config import load_config  # noqa: E402
from poppy.skills import archive_skill  # noqa: E402
from poppy.util import ensure_home_layout  # noqa: E402

SKILL = """---
name: widget-deploys
description: >-
  Deploy the widget. Use when the widget needs a new release.
---

# Widget deploys

Run the deploy script.
"""

GIT = shutil.which("git")


class TestPublish(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        ensure_home_layout(self.home)
        library.ensure_library(self.home)
        skill_dir = library.skills_dir(self.home) / "widget-deploys"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(SKILL, encoding="utf-8")
        self.target = Path(self.tmp.name) / "public"
        self.target.mkdir()
        self.cfg = load_config(self.home)
        self.cfg["publish"] = {"target": str(self.target), "subdir": ""}

    def tearDown(self):
        self.tmp.cleanup()

    def test_publish_and_unchanged(self):
        result = publish.publish(self.home, self.cfg, "widget-deploys")
        self.assertEqual(result["action"], "added")
        self.assertTrue((self.target / "widget-deploys" / "SKILL.md").is_file())

        again = publish.publish(self.home, self.cfg, "widget-deploys")
        self.assertEqual(again["action"], "unchanged")

    def test_subdir(self):
        result = publish.publish(self.home, self.cfg, "widget-deploys", subdir="skills")
        self.assertEqual(result["action"], "added")
        self.assertTrue((self.target / "skills" / "widget-deploys" / "SKILL.md").is_file())

    def test_refuses_divergent_overwrite(self):
        publish.publish(self.home, self.cfg, "widget-deploys")
        published = self.target / "widget-deploys" / "SKILL.md"
        published.write_text(SKILL + "\nEdited publicly.\n", encoding="utf-8")
        with self.assertRaises(Exception):
            publish.publish(self.home, self.cfg, "widget-deploys")
        result = publish.publish(self.home, self.cfg, "widget-deploys", force=True)
        self.assertEqual(result["action"], "updated")
        self.assertNotIn("Edited publicly.", published.read_text(encoding="utf-8"))

    def test_missing_archived_and_no_target(self):
        with self.assertRaises(Exception):
            publish.publish(self.home, self.cfg, "nope")
        archive_skill(self.home, "widget-deploys")
        with self.assertRaises(Exception) as ctx:
            publish.publish(self.home, self.cfg, "widget-deploys")
        self.assertIn("archived", str(ctx.exception))
        empty_cfg = load_config(self.home)
        with self.assertRaises(Exception) as ctx:
            publish.publish(self.home, empty_cfg, "widget-deploys")
        self.assertIn("archived", str(ctx.exception))

    def test_machine_specific_warning(self):
        skill_md = library.skills_dir(self.home) / "widget-deploys" / "SKILL.md"
        skill_md.write_text(SKILL + f"\nLocal path: {Path.home()}/projects\n", encoding="utf-8")
        result = publish.publish(self.home, self.cfg, "widget-deploys")
        self.assertTrue(any("home path" in warning for warning in result["warnings"]))

    @unittest.skipUnless(GIT, "git is not installed")
    def test_commit_and_push(self):
        remote = Path(self.tmp.name) / "remote.git"
        subprocess.run([GIT, "init", "--bare", "-q", str(remote)], check=True)
        subprocess.run([GIT, "init", "-q", str(self.target)], check=True)
        subprocess.run(
            [GIT, "-C", str(self.target), "symbolic-ref", "HEAD", "refs/heads/main"], check=True
        )
        for key, value in (("user.name", "tester"), ("user.email", "tester@example.com")):
            subprocess.run([GIT, "-C", str(self.target), "config", key, value], check=True)
        subprocess.run([GIT, "-C", str(self.target), "remote", "add", "origin", str(remote)], check=True)
        subprocess.run(
            [GIT, "-C", str(self.target), "commit", "-q", "--allow-empty", "-m", "init"], check=True
        )
        subprocess.run([GIT, "-C", str(self.target), "push", "-q", "-u", "origin", "main"], check=True)

        result = publish.publish(self.home, self.cfg, "widget-deploys", commit=True, push=True)
        self.assertTrue(result["committed"])
        self.assertTrue(result["pushed"])
        log = subprocess.run(
            [GIT, "-C", str(self.target), "log", "--oneline"], capture_output=True, text=True
        ).stdout
        self.assertIn("widget-deploys", log)


if __name__ == "__main__":
    unittest.main()
