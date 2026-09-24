import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy import pipeline, schedule, util  # noqa: E402
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


class TestMineCadence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "poppy-home"
        ensure_home_layout(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def test_cadences_shape_the_timer_units(self):
        cases = {
            "weekly": ("OnCalendar=Mon 09:00", "mine --quiet", False),
            "daily": ("OnCalendar=*-*-* 09:00", "mine --quiet", False),
            "every-other-day": ("OnUnitActiveSec=48h", "mine --quiet", False),
            "smart": ("OnCalendar=*-*-* 09:00", "mine --quiet --if-due", False),
        }
        for cadence, (timing, command, _) in cases.items():
            with self.subTest(cadence=cadence):
                root = Path(self.tmp.name) / cadence
                service = root / "poppy-mine.service"
                timer = root / "poppy-mine.timer"
                cfg = load_config(self.home)
                with mock.patch("poppy.schedule.platform_kind", return_value="systemd"), mock.patch(
                    "poppy.schedule.systemd_paths", return_value=(service, timer)
                ), mock.patch("poppy.schedule._systemctl"):
                    result = schedule.install(self.home, cfg, include_sync=False, cadence=cadence)
                self.assertEqual(result["mine_cadence"], cadence)
                self.assertIn(timing, timer.read_text(encoding="utf-8"))
                self.assertIn(command, service.read_text(encoding="utf-8"))
                self.assertEqual(load_config(self.home)["schedule"]["mine"], cadence)

    def test_off_removes_the_mining_job_and_returns(self):
        service = Path(self.tmp.name) / "poppy-mine.service"
        timer = Path(self.tmp.name) / "poppy-mine.timer"
        service.write_text("stale", encoding="utf-8")
        timer.write_text("stale", encoding="utf-8")
        cfg = load_config(self.home)
        with mock.patch("poppy.schedule.platform_kind", return_value="systemd"), mock.patch(
            "poppy.schedule.systemd_paths", return_value=(service, timer)
        ), mock.patch("poppy.schedule._systemctl"):
            result = schedule.install(self.home, cfg, include_sync=False, cadence="off")
        self.assertFalse(result["enabled"])
        self.assertTrue(result["mine_removed"])
        self.assertFalse(service.exists())
        self.assertFalse(timer.exists())
        self.assertEqual(load_config(self.home)["schedule"]["mine"], "off")

    def test_cron_line_follows_the_cadence(self):
        cfg = {"schedule": {"mine": "smart", "path": "/usr/bin"}, "sync": {}}
        line = schedule.cron_line(self.home, cfg)
        self.assertIn("0 9 * * *", line)
        self.assertIn("mine --quiet --if-due", line)
        line = schedule.cron_line(self.home, {"schedule": {"path": "/usr/bin"}})
        self.assertIn("0 9 * * 1", line)

    def test_launchd_daily_has_no_weekday(self):
        plist = schedule._plist(
            "com.poppy.mine",
            ["/usr/local/bin/poppy", "mine", "--quiet"],
            self.home,
            self.home / "logs" / "scheduled.log",
            schedule.MINE_CALENDAR.format(weekday=""),
            "/usr/bin",
        )
        self.assertNotIn("Weekday", plist)
        self.assertIn("<key>Hour</key><integer>9</integer>", plist)


class TestMineDue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "poppy-home"
        ensure_home_layout(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def test_due_without_a_previous_run(self):
        due, _count, detail = pipeline.mine_due(self.home, {"schedule": {"smart_min_sessions": 5}})
        self.assertTrue(due)
        self.assertIn("no previous", detail)

    def test_not_due_when_nothing_accumulated(self):
        pipeline.save_mine_state(self.home, running=False, finished_at=util.now_iso())
        with mock.patch("poppy.pipeline.load_sources", return_value=[{"name": "fake", "type": "files"}]), mock.patch(
            "poppy.pipeline.collect_sessions", return_value=[1, 2]
        ):
            due, count, detail = pipeline.mine_due(self.home, {"schedule": {"smart_min_sessions": 5}})
        self.assertFalse(due)
        self.assertEqual(count, 2)
        self.assertIn("fewer than 5", detail)

    def test_due_once_enough_accumulated(self):
        pipeline.save_mine_state(self.home, running=False, finished_at=util.now_iso())
        with mock.patch("poppy.pipeline.load_sources", return_value=[{"name": "fake", "type": "files"}]), mock.patch(
            "poppy.pipeline.collect_sessions", return_value=list(range(6))
        ):
            due, count, detail = pipeline.mine_due(self.home, {"schedule": {"smart_min_sessions": 5}})
        self.assertTrue(due)
        self.assertEqual(count, 6)
        self.assertNotIn("fewer than", detail)

    def test_mine_skips_quietly_when_not_due(self):
        pipeline.save_mine_state(self.home, running=False, finished_at=util.now_iso())
        summary = pipeline.mine(self.home, if_due=True, config={"schedule": {"smart_min_sessions": 5}})
        self.assertTrue(summary["skipped"])
        self.assertFalse(summary["due"])
        state = pipeline.mine_state(self.home)
        self.assertFalse(state["running"])
        self.assertTrue(state["skipped"])


if __name__ == "__main__":
    unittest.main()
