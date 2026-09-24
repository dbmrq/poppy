import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy import update  # noqa: E402
from poppy.util import DATA_DIR, REPO_ROOT, launch_command, launch_command_str  # noqa: E402


class TestPackaging(unittest.TestCase):
    def test_runtime_data_ships_inside_the_package(self):
        self.assertTrue((DATA_DIR / "prompts" / "miner.md").is_file())
        self.assertTrue((DATA_DIR / "prompts" / "writer.md").is_file())
        self.assertTrue((DATA_DIR / "ui" / "index.html").is_file())
        builtins = sorted(path.parent.name for path in (DATA_DIR / "builtin").glob("*/SKILL.md"))
        self.assertEqual(builtins, ["poppy", "poppy-context", "poppy-propose"])

    def test_launch_command_from_a_checkout(self):
        self.assertTrue((REPO_ROOT / "bin" / "poppy").is_file())
        command = launch_command()
        self.assertEqual(command[0], sys.executable)
        self.assertTrue(command[1].endswith("bin/poppy"))
        self.assertIn("bin/poppy", launch_command_str())

    def test_launch_command_installed_fallback(self):
        with mock.patch("poppy.util.REPO_ROOT", Path("/nonexistent/poppy")):
            self.assertEqual(launch_command(), [sys.executable, "-m", "poppy"])
            self.assertIn("-m poppy", launch_command_str())

    def test_install_mode_in_a_checkout(self):
        self.assertEqual(update.install_mode(), "checkout")


class TestUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"

    def tearDown(self):
        self.tmp.cleanup()

    def test_checkout_update_refreshes(self):
        with mock.patch.object(update, "install_mode", return_value="checkout"), mock.patch.object(
            update, "_run", return_value=(0, "Already up to date.")
        ), mock.patch.object(update, "_refresh", return_value=(True, "")), mock.patch.object(
            update, "version", return_value="poppy 0.1.0"
        ):
            result = update.update(self.home, {})
        self.assertEqual(result["mode"], "checkout")
        self.assertIn("git", result["command"])
        self.assertTrue(result["refreshed"])
        self.assertEqual(result["version"], "poppy 0.1.0")

    def test_update_failure_raises(self):
        with mock.patch.object(update, "install_mode", return_value="checkout"), mock.patch.object(
            update, "_run", return_value=(1, "boom")
        ):
            with self.assertRaises(Exception) as ctx:
                update.update(self.home, {})
        self.assertIn("boom", str(ctx.exception))

    def test_unsupported_mode_raises_with_guidance(self):
        with mock.patch.object(update, "install_mode", return_value="pip"):
            with self.assertRaises(Exception) as ctx:
                update.update(self.home, {})
        self.assertIn("pip install --upgrade", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
