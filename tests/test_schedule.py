import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy import schedule  # noqa: E402
from poppy.config import load_config  # noqa: E402
from poppy.util import ensure_home_layout  # noqa: E402


class TestLaunchdPlist(unittest.TestCase):
    def test_plist_carries_arguments_home_and_environment(self):
        text = schedule._plist(
            "com.poppy.mine",
            ["/opt/homebrew/bin/poppy", "mine", "--quiet"],
            Path("/Users/daniel/.poppy"),
            Path("/Users/daniel/.poppy/logs/scheduled.log"),
            schedule.MINE_CALENDAR,
            "/opt/homebrew/bin:/usr/bin:/bin",
        )
        self.assertIn("<string>/opt/homebrew/bin/poppy</string>", text)
        self.assertIn("<key>POPPY_HOME</key><string>/Users/daniel/.poppy</string>", text)
        self.assertIn("<key>HOME</key>", text)
        self.assertIn("<key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin</string>", text)
        self.assertIn("<key>StartCalendarInterval</key>", text)

    def test_plist_escapes_special_characters(self):
        text = schedule._plist(
            "com.poppy.mine",
            ["/bin/echo", "a&b<c"],
            Path("/tmp/poppy home"),
            Path("/tmp/log"),
            schedule.SYNC_INTERVAL.format(interval_sec=1800),
            "/tmp/a&b",
        )
        self.assertIn("a&amp;b&lt;c", text)
        self.assertIn("/tmp/poppy home", text)
        self.assertIn("/tmp/a&amp;b", text)
        self.assertIn("<key>StartInterval</key><integer>1800</integer>", text)


class TestTimerPath(unittest.TestCase):
    def test_cron_lines_carry_the_timer_path(self):
        cfg = {"schedule": {"path": "/opt/homebrew/bin:/usr/bin"}, "sync": {}}
        line = schedule.cron_line(Path("/tmp/poppy"), cfg)
        self.assertIn("/usr/bin/env PATH=", line)
        self.assertIn("/opt/homebrew/bin", line)
        self.assertIn("mine --quiet", line)
        sync_line = schedule.sync_cron_line(Path("/tmp/poppy"), cfg)
        self.assertIn("/usr/bin/env PATH=", sync_line)
        self.assertIn("/opt/homebrew/bin", sync_line)
        self.assertIn("sync run --quiet", sync_line)

    def test_install_embeds_and_persists_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "poppy-home"
            ensure_home_layout(home)
            cfg = load_config(home)
            service = root / "poppy-mine.service"
            timer = root / "poppy-mine.timer"
            with mock.patch("poppy.schedule.platform_kind", return_value="systemd"), mock.patch(
                "poppy.schedule.systemd_paths", return_value=(service, timer)
            ), mock.patch("poppy.schedule._systemctl"):
                result = schedule.install(home, cfg, include_sync=False)

            self.assertEqual(result["kind"], "systemd")
            text = service.read_text(encoding="utf-8")
            stored = load_config(home)["schedule"]["path"]
            self.assertTrue(stored)
            self.assertIn("/usr/bin", stored)
            self.assertIn(f"Environment=PATH={stored}", text)


if __name__ == "__main__":
    unittest.main()
