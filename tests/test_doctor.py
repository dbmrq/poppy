import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy import doctor, library  # noqa: E402
from poppy.config import init_config, load_config, save_config  # noqa: E402
from poppy.util import ensure_home_layout  # noqa: E402


def make_home(root: Path) -> Path:
    home = root / "poppy-home"
    ensure_home_layout(home)
    library.ensure_library(home)
    init_config(home)
    return home


class TestScheduleEnvCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def check_named(self, home: Path, name: str):
        checks = doctor.run_checks(home)
        return next(check for check in checks if check.name == name)

    def test_warns_when_timer_has_no_path(self):
        home = make_home(self.root)
        cfg = load_config(home)
        cfg["agent"]["miner"]["cmd"] = ["/bin/sh", "{prompt}"]
        save_config(home, cfg)
        installed = {"installed": True, "kind": "launchd", "detail": "plist"}
        with mock.patch("poppy.doctor.schedule_status", return_value=installed):
            check = self.check_named(home, "schedule:env")
        self.assertEqual(check.status, "warn")
        self.assertIn("PATH", check.detail)

    def test_fails_when_agent_is_missing_from_the_timer_path(self):
        home = make_home(self.root)
        cfg = load_config(home)
        cfg["agent"]["miner"]["cmd"] = ["poppy-test-agent-not-real", "{prompt}"]
        cfg["schedule"]["path"] = "/nonexistent-bin"
        save_config(home, cfg)
        installed = {"installed": True, "kind": "systemd", "detail": "unit"}
        with mock.patch("poppy.doctor.schedule_status", return_value=installed):
            check = self.check_named(home, "schedule:env")
        self.assertEqual(check.status, "fail")
        self.assertIn("miner", check.detail)

    def test_ok_when_agents_resolve_under_the_timer_path(self):
        home = make_home(self.root)
        cfg = load_config(home)
        cfg["agent"]["miner"]["cmd"] = ["/bin/sh", "{prompt}"]
        cfg["agent"]["writer"]["cmd"] = ["/bin/sh", "{prompt}"]
        cfg["schedule"]["path"] = "/usr/bin:/bin"
        save_config(home, cfg)
        installed = {"installed": True, "kind": "launchd", "detail": "plist"}
        with mock.patch("poppy.doctor.schedule_status", return_value=installed):
            check = self.check_named(home, "schedule:env")
        self.assertEqual(check.status, "ok")

    def test_no_schedule_env_check_without_an_installed_timer(self):
        home = make_home(self.root)
        not_installed = {"installed": False, "detail": "no plist"}
        with mock.patch("poppy.doctor.schedule_status", return_value=not_installed):
            checks = doctor.run_checks(home)
        self.assertFalse(any(check.name == "schedule:env" for check in checks))


if __name__ == "__main__":
    unittest.main()
