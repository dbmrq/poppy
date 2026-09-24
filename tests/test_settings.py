import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy import settings  # noqa: E402
from poppy.config import DEFAULT_CONFIG  # noqa: E402
from poppy.util import PoppyError  # noqa: E402


class TestSettings(unittest.TestCase):
    def test_fields_render_the_current_config(self):
        fields = {field["key"]: field for field in settings.fields(DEFAULT_CONFIG)}
        self.assertEqual(set(fields), set(settings.KEYS))
        self.assertEqual(fields["agent.miner.cmd"]["kind"], "lines")
        self.assertEqual(fields["agent.miner.cmd"]["value"], "")
        self.assertEqual(fields["decay_after_days"]["value"], 90)

    def test_validate_normalizes_and_applies(self):
        cfg = {
            "lookback_days": 14,
            "decay_after_days": 90,
            "skills_dirs": [],
            "agent": {
                "miner": {"cmd": None, "timeout_sec": 1800},
                "writer": {"cmd": None, "timeout_sec": 900},
            },
        }
        normalized = settings.validate(
            {
                "decay_after_days": "30",
                "agent.miner.cmd": f"{sys.executable}\n-c\npass\n{{prompt}}\n\n",
                "skills_dirs": "~/one\n\n~/two",
                "lookback_days": 7,
            }
        )
        self.assertEqual(normalized["decay_after_days"], 30)
        self.assertEqual(normalized["agent.miner.cmd"], [sys.executable, "-c", "pass", "{prompt}"])
        self.assertEqual(normalized["skills_dirs"], ["~/one", "~/two"])
        settings.apply(cfg, normalized)
        self.assertEqual(cfg["decay_after_days"], 30)
        self.assertEqual(cfg["agent"]["miner"]["cmd"][0], sys.executable)
        self.assertEqual(cfg["agent"]["writer"]["timeout_sec"], 900)  # untouched

    def test_validate_rejects_bad_values(self):
        cases = [
            ({"nope": 1}, "unknown setting"),
            ({"decay_after_days": 0}, "at least 1"),
            ({"decay_after_days": "soon"}, "whole number"),
            ({"agent.miner.cmd": ""}, "needs at least the program"),
            ({"agent.writer.cmd": "/no/such/binary"}, "not an executable file"),
            ({"agent.writer.cmd": "definitely-not-a-real-binary-xyz"}, "not found on PATH"),
            ({"skills_dirs": 5}, "expected one item per line"),
        ]
        for values, needle in cases:
            with self.subTest(values=values):
                with self.assertRaises(PoppyError) as caught:
                    settings.validate(values)
                self.assertIn(needle, str(caught.exception))

    def test_binary_check_can_be_skipped_for_the_demo(self):
        normalized = settings.validate(
            {"agent.miner.cmd": "definitely-not-a-real-binary-xyz"}, check_binaries=False
        )
        self.assertEqual(normalized["agent.miner.cmd"], ["definitely-not-a-real-binary-xyz"])


if __name__ == "__main__":
    unittest.main()
