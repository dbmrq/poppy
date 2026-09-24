import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy.candidates import load_candidate  # noqa: E402
from poppy.config import load_config, save_config  # noqa: E402
from poppy.doctor import run_checks  # noqa: E402
from poppy.pipeline import accept, mine  # noqa: E402
from poppy.skills import install_draft, uninstall_skill  # noqa: E402
from poppy.sources import save_sources  # noqa: E402
from poppy.ui import _state  # noqa: E402
from poppy.util import ensure_home_layout  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jsonl"

FAKE_MINER = '''\
import json
import os
import pathlib

inbox = pathlib.Path(os.environ["POPPY_INBOX"])
candidate = {
    "title": "Pin dependencies for reproducible builds",
    "summary": "The widget build failed on CI until the lockfile was frozen. Freezing transitive dependencies makes the build reproducible.",
    "trigger": "Use when a build works locally but fails on CI with dependency drift.",
    "evidence": [
        {
            "source": "fixtures",
            "session": "session-alpha.jsonl",
            "quote": "Always pass --frozen-lockfile to the package manager so the widget build is reproducible.",
        }
    ],
}
(inbox / "candidate.json").write_text(json.dumps(candidate), encoding="utf-8")
'''

FAKE_WRITER = '''\
import os
import pathlib

draft = pathlib.Path(os.environ["POPPY_DRAFT_DIR"])
draft.mkdir(parents=True, exist_ok=True)
(draft / "SKILL.md").write_text("""---
name: frozen-lockfile-builds
description: >-
  Make CI builds reproducible with a frozen lockfile. Use when a build works
  locally but fails on CI with dependency drift.
---

# Frozen lockfile builds

Pass `--frozen-lockfile` to the package manager before building.
""", encoding="utf-8")
'''


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "poppy-home"
        ensure_home_layout(self.home)
        os.environ["POPPY_HOME"] = str(self.home)
        self.skills_dir = root / "skills"

        miner = root / "fake_miner.py"
        miner.write_text(FAKE_MINER, encoding="utf-8")
        writer = root / "fake_writer.py"
        writer.write_text(FAKE_WRITER, encoding="utf-8")

        save_sources(
            self.home,
            [{"name": "fixtures", "type": "files", "path": str(FIXTURES), "glob": "*.jsonl"}],
        )
        cfg = load_config(self.home)
        cfg["skills_dirs"] = [str(self.skills_dir)]
        cfg["agent"]["miner"]["cmd"] = [sys.executable, str(miner), "{prompt}"]
        cfg["agent"]["writer"]["cmd"] = [sys.executable, str(writer), "{prompt}"]
        save_config(self.home, cfg)
        self.cfg = cfg

    def tearDown(self):
        os.environ.pop("POPPY_HOME", None)
        self.tmp.cleanup()

    def test_full_loop(self):
        summary = mine(self.home, since_seconds=365 * 86400)
        self.assertEqual(summary["agent_exit"], 0)
        self.assertEqual(len(summary["accepted"]), 1)
        candidate_id = summary["accepted"][0]["id"]

        # a second run re-proposing the same candidate is rejected as a duplicate
        second = mine(self.home, since_seconds=365 * 86400)
        self.assertEqual(second["accepted"], [])
        self.assertTrue(
            any("duplicate" in error for item in second["invalid"] for error in item["errors"])
        )

        result = accept(self.home, candidate_id)
        self.assertEqual(result["status"], "draft")

        candidate = load_candidate(self.home, candidate_id)
        name, _dirs = install_draft(self.home, self.cfg, candidate)
        self.assertEqual(name, "frozen-lockfile-builds")
        self.assertTrue((self.skills_dir / name / "SKILL.md").is_file())

        state = _state(self.home, self.cfg)
        self.assertIn(name, state["installed"])

        failures = [check for check in run_checks(self.home) if check.status == "fail"]
        self.assertEqual(failures, [], failures)

        uninstall_skill(self.home, name)
        self.assertFalse((self.skills_dir / name).exists())

    def test_dry_run_does_not_invoke_agent(self):
        summary = mine(self.home, since_seconds=365 * 86400, dry_run=True)
        self.assertTrue(summary["dry_run"])
        self.assertGreater(summary["sessions"], 0)
        self.assertEqual(list((self.home / "candidates").glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
