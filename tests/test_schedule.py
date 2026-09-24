import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy import schedule  # noqa: E402


class TestLaunchdPlist(unittest.TestCase):
    def test_plist_carries_arguments_home_and_environment(self):
        text = schedule._plist(
            "com.poppy.mine",
            ["/opt/homebrew/bin/poppy", "mine", "--quiet"],
            Path("/Users/daniel/.poppy"),
            Path("/Users/daniel/.poppy/logs/scheduled.log"),
            schedule.MINE_CALENDAR,
        )
        self.assertIn("<string>/opt/homebrew/bin/poppy</string>", text)
        self.assertIn("<key>POPPY_HOME</key><string>/Users/daniel/.poppy</string>", text)
        self.assertIn("<key>HOME</key>", text)
        self.assertIn("<key>StartCalendarInterval</key>", text)

    def test_plist_escapes_special_characters(self):
        text = schedule._plist(
            "com.poppy.mine",
            ["/bin/echo", "a&b<c"],
            Path("/tmp/poppy home"),
            Path("/tmp/log"),
            schedule.SYNC_INTERVAL.format(interval_sec=1800),
        )
        self.assertIn("a&amp;b&lt;c", text)
        self.assertIn("/tmp/poppy home", text)
        self.assertIn("<key>StartInterval</key><integer>1800</integer>", text)


if __name__ == "__main__":
    unittest.main()
