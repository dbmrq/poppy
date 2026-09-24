import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy.cli import main  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jsonl"


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        os.environ["POPPY_HOME"] = str(self.home)

    def tearDown(self):
        os.environ.pop("POPPY_HOME", None)
        self.tmp.cleanup()

    def run_cli(self, *argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(list(argv))
        return code, output.getvalue()

    @mock.patch("poppy.config.detect_skills_dirs", return_value=[])
    def test_init_sources_sessions_mine_status(self, _mock_detect):
        code, _ = self.run_cli("init")
        self.assertEqual(code, 0)

        entry = {"name": "fixtures", "type": "files", "path": str(FIXTURES), "glob": "*.jsonl"}
        code, _ = self.run_cli("sources", "add", "--json", json.dumps(entry))
        self.assertEqual(code, 0)

        code, output = self.run_cli("sources", "test")
        self.assertEqual(code, 0, output)
        self.assertIn("fixtures", output)

        code, output = self.run_cli("sessions", "list", "--since", "14d")
        self.assertEqual(code, 0, output)
        self.assertIn("session-alpha.jsonl", output)

        code, output = self.run_cli("sessions", "search", "frozen-lockfile")
        self.assertEqual(code, 0, output)
        self.assertIn("session-alpha.jsonl", output)

        code, output = self.run_cli("sessions", "read", "session-beta.jsonl")
        self.assertEqual(code, 0, output)
        self.assertIn("smoke tests", output)

        code, output = self.run_cli("mine", "--dry-run")
        self.assertEqual(code, 0, output)
        self.assertIn("dry run", output)

        code, output = self.run_cli("status")
        self.assertEqual(code, 0, output)
        self.assertIn("fixtures", output)

        code, output = self.run_cli("library", "list")
        self.assertEqual(code, 0, output)

        code, output = self.run_cli("context")
        self.assertEqual(code, 0, output)

        code, output = self.run_cli("context", "show", "--brief")
        self.assertEqual(code, 0, output)

        code, output = self.run_cli("context", "export")
        self.assertEqual(code, 0, output)
        self.assertIn("digest", output)

        code, output = self.run_cli("context", "status")
        self.assertEqual(code, 0, output)

        wire_target = Path(self.tmp.name) / "AGENTS.md"
        code, output = self.run_cli("context", "wire", "--file", str(wire_target))
        self.assertEqual(code, 0, output)
        self.assertTrue(wire_target.is_file())

        code, output = self.run_cli("context", "unwire", "--file", str(wire_target))
        self.assertEqual(code, 0, output)
        self.assertNotIn("poppy:begin", wire_target.read_text(encoding="utf-8"))

        code, output = self.run_cli("decay")
        self.assertEqual(code, 0, output)

    @mock.patch("poppy.config.detect_skills_dirs", return_value=[])
    def test_config_set_get(self, _mock_detect):
        self.run_cli("init")
        code, _ = self.run_cli("config", "set", "lookback_days", "7")
        self.assertEqual(code, 0)
        code, output = self.run_cli("config", "get", "lookback_days")
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "7")

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    @mock.patch("poppy.config.detect_skills_dirs", return_value=[])
    def test_sync_commands(self, _mock_detect):
        self.run_cli("init")

        code, output = self.run_cli("sync", "status")
        self.assertEqual(code, 0, output)
        self.assertIn("not initialized", output)

        code, output = self.run_cli("sync", "init")
        self.assertEqual(code, 0, output)
        self.assertIn("repo:", output)

        code, output = self.run_cli("sync", "status")
        self.assertEqual(code, 0, output)
        self.assertIn("branch main", output)

        code, output = self.run_cli("sync", "run")
        self.assertEqual(code, 0, output)
        self.assertIn("sync:", output)


if __name__ == "__main__":
    unittest.main()
