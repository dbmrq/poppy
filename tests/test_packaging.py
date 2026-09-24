import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy import __version__, update  # noqa: E402
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

    def test_launch_command_installed_prefers_the_console_script(self):
        script = str(Path(sys.executable).parent / "poppy")
        with mock.patch("poppy.util.REPO_ROOT", Path("/nonexistent/poppy")), mock.patch(
            "poppy.util.shutil.which", return_value=script
        ):
            self.assertEqual(launch_command(), [script])

    def test_launch_command_installed_fallback(self):
        with mock.patch("poppy.util.REPO_ROOT", Path("/nonexistent/poppy")), mock.patch(
            "poppy.util.shutil.which", return_value=None
        ):
            self.assertEqual(launch_command(), [sys.executable, "-m", "poppy"])
            self.assertIn("-m poppy", launch_command_str())
        # a different install's script on PATH must not be adopted
        with mock.patch("poppy.util.REPO_ROOT", Path("/nonexistent/poppy")), mock.patch(
            "poppy.util.shutil.which", return_value="/somewhere/else/poppy"
        ):
            self.assertEqual(launch_command(), [sys.executable, "-m", "poppy"])

    def test_install_mode_in_a_checkout(self):
        self.assertEqual(update.install_mode(), "checkout")

    def test_install_mode_detects_brew(self):
        brew_path = Path("/opt/homebrew/Cellar/poppy-ai/0.1.0/lib/python3.13/site-packages/poppy")
        with mock.patch.object(update, "PACKAGE_DIR", brew_path):
            self.assertEqual(update.install_mode(), "brew")


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

    def test_brew_update_command(self):
        with mock.patch.object(update, "install_mode", return_value="brew"), mock.patch.object(
            update, "_run", return_value=(0, "ok")
        ), mock.patch.object(update, "_refresh", return_value=(True, "")), mock.patch.object(
            update, "version", return_value="poppy 9.9.9"
        ):
            result = update.update(self.home, {})
        self.assertEqual(result["mode"], "brew")
        self.assertEqual(result["command"], "brew upgrade poppy-ai")

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

    def test_version_key_orders_numerically(self):
        self.assertGreater(update._version_key("0.10.0"), update._version_key("0.9.0"))
        self.assertGreater(update._version_key("1.0.0"), update._version_key("1.0.0-rc1"))
        self.assertEqual(update._version_key("0.1.0"), update._version_key("0.1.0"))

    def test_check_reports_update_from_pypi(self):
        with mock.patch.object(update, "install_mode", return_value="pipx"), mock.patch.object(
            update, "_latest_from_pypi", return_value=("9.9.9", "")
        ):
            result = update.check(self.home, {})
        self.assertTrue(result["update_available"])
        self.assertEqual(result["latest"], "9.9.9")
        self.assertEqual(result["installed"], __version__)

    def test_check_reports_up_to_date(self):
        with mock.patch.object(update, "install_mode", return_value="pipx"), mock.patch.object(
            update, "_latest_from_pypi", return_value=(__version__, "")
        ):
            result = update.check(self.home, {})
        self.assertFalse(result["update_available"])

    def test_check_offline_is_unknown(self):
        with mock.patch.object(update, "install_mode", return_value="pip"), mock.patch.object(
            update, "_latest_from_pypi", return_value=(None, "offline")
        ):
            result = update.check(self.home, {})
        self.assertIsNone(result["update_available"])
        self.assertEqual(result["detail"], "offline")

    def test_check_checkout_behind(self):
        with mock.patch.object(update, "install_mode", return_value="checkout"), mock.patch.object(
            update, "_checkout_behind", return_value=(2, "")
        ):
            result = update.check(self.home, {})
        self.assertTrue(result["update_available"])
        self.assertEqual(result["behind"], 2)


if __name__ == "__main__":
    unittest.main()
