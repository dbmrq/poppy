import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

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

    def test_init_sources_sessions_mine_status(self):
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

    def test_config_set_get(self):
        self.run_cli("init")
        code, _ = self.run_cli("config", "set", "lookback_days", "7")
        self.assertEqual(code, 0)
        code, output = self.run_cli("config", "get", "lookback_days")
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "7")


if __name__ == "__main__":
    unittest.main()
