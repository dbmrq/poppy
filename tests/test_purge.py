import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy import digest, library, purge  # noqa: E402
from poppy.config import load_config  # noqa: E402
from poppy.skills import reconcile_mirrors  # noqa: E402
from poppy.util import ensure_home_layout  # noqa: E402

SKILL = """---
name: widget-deploys
description: Deploy the widget. Use when the widget needs a new release.
---

Run the deploy script.
"""


class TestPurge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        ensure_home_layout(self.home)
        library.ensure_library(self.home)
        skill_dir = library.skills_dir(self.home) / "widget-deploys"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(SKILL, encoding="utf-8")

        self.skills = Path(self.tmp.name) / "skills"
        self.cfg = load_config(self.home)
        self.cfg["skills_dirs"] = [str(self.skills)]
        reconcile_mirrors(self.home, self.cfg)

        self.target = Path(self.tmp.name) / "AGENTS.md"
        digest.wire(self.home, self.cfg, self.target)

        patcher = mock.patch(
            "poppy.purge.schedule_uninstall",
            return_value={"kind": "systemd", "removed": ["/tmp/poppy-mine.timer"]},
        )
        self.schedule_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_preview_removes_nothing(self):
        result = purge.purge(self.home, self.cfg)
        self.assertFalse(result["applied"])
        self.assertIn("widget-deploys", result["mirrors"])
        self.assertIn(str(self.target), result["wiring"])
        self.assertTrue((self.skills / "widget-deploys").is_dir())
        self.assertIn(digest.BEGIN, self.target.read_text(encoding="utf-8"))
        self.assertTrue(self.home.is_dir())
        self.schedule_mock.assert_not_called()

    def test_keep_data_removes_integration(self):
        result = purge.purge(self.home, self.cfg, yes=True, keep_data=True)
        self.assertTrue(result["applied"])
        self.assertFalse((self.skills / "widget-deploys").exists())
        self.assertNotIn(digest.BEGIN, self.target.read_text(encoding="utf-8"))
        self.assertTrue(self.home.is_dir())
        self.assertEqual(result["kept_data"], str(self.home))
        self.schedule_mock.assert_called_once()
        self.assertEqual(digest.list_wiring(self.home), [])

    def test_full_removal_deletes_data(self):
        result = purge.purge(self.home, self.cfg, yes=True)
        self.assertTrue(result["applied"])
        self.assertEqual(result["removed"]["data"], str(self.home))
        self.assertFalse(self.home.exists())
        self.assertFalse((self.skills / "widget-deploys").exists())


if __name__ == "__main__":
    unittest.main()
