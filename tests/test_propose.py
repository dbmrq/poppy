import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy import propose  # noqa: E402
from poppy.candidates import load_candidate  # noqa: E402
from poppy.config import load_config  # noqa: E402
from poppy.sources import save_sources  # noqa: E402
from poppy.util import ensure_home_layout  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jsonl"
QUOTE = "Always pass --frozen-lockfile to the package manager so the widget build is reproducible."


class TestPropose(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        ensure_home_layout(self.home)
        save_sources(
            self.home,
            [{"name": "fixtures", "type": "files", "path": str(FIXTURES), "glob": "*.jsonl"}],
        )
        self.cfg = load_config(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def payload(self, **overrides):
        data = {
            "kind": "memory",
            "title": "Prefers frozen lockfiles",
            "summary": "Freezing the lockfile makes widget builds reproducible.",
            "trigger": "when configuring builds",
            "scope": "user",
            "evidence": [{"quote": QUOTE}],
        }
        data.update(overrides)
        return data

    def test_locates_quote_and_queues(self):
        result = propose.propose(self.home, self.cfg, self.payload())
        self.assertTrue(result["queued"])
        candidate = load_candidate(self.home, result["id"])
        self.assertEqual(candidate["status"], "pending")
        evidence = candidate["evidence"][0]
        self.assertEqual(evidence["source"], "fixtures")
        self.assertEqual(evidence["session"], "session-alpha.jsonl")
        self.assertTrue(evidence["excerpt"])

    def test_explicit_source_and_session_pass_through(self):
        data = self.payload(
            evidence=[{"source": "fixtures", "session": "session-alpha.jsonl", "quote": QUOTE}]
        )
        result = propose.propose(self.home, self.cfg, data)
        self.assertTrue(result["queued"])

    def test_unlocatable_quote(self):
        with self.assertRaises(Exception) as ctx:
            propose.propose(
                self.home,
                self.cfg,
                self.payload(evidence=[{"quote": "this quote appears in no session whatsoever"}]),
            )
        self.assertIn("not found", str(ctx.exception))

    def test_dedupe_and_secrets(self):
        propose.propose(self.home, self.cfg, self.payload())
        with self.assertRaises(Exception) as ctx:
            propose.propose(self.home, self.cfg, self.payload())
        self.assertIn("duplicate", str(ctx.exception))

        secret = self.payload(title="Leaked key", summary="The key is sk-" + "a" * 30)
        with self.assertRaises(Exception) as ctx:
            propose.propose(self.home, self.cfg, secret)
        self.assertIn("secret", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
