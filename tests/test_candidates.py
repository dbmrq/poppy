import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy.candidates import (  # noqa: E402
    candidate_id,
    load_candidate,
    mark_rejected,
    save_candidate,
    validate_raw,
)
from poppy.sources import FilesSource  # noqa: E402
from poppy.util import ensure_home_layout  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jsonl"
QUOTE = "Always pass --frozen-lockfile to the package manager so the widget build is reproducible."


def raw_candidate(quote: str = QUOTE, source: str = "fixtures") -> dict:
    return {
        "title": "Pin dependencies for reproducible builds",
        "summary": "The widget build failed on CI until the lockfile was frozen.",
        "trigger": "Use when a build works locally but fails on CI.",
        "evidence": [{"source": source, "session": "session-alpha.jsonl", "quote": quote}],
    }


class TestValidate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        ensure_home_layout(self.home)
        self.source = FilesSource("fixtures", {"path": str(FIXTURES), "glob": "*.jsonl"})
        self.sources = [self.source]

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_candidate(self):
        candidate, errors, warnings = validate_raw(raw_candidate(), self.home, {"min_evidence": 1}, self.sources)
        self.assertEqual(errors, [])
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["status"], "pending")
        self.assertTrue(candidate["evidence"][0]["excerpt"])
        self.assertEqual(candidate["id"], candidate_id(candidate["title"], candidate["summary"]))

    def test_unverifiable_quote(self):
        candidate, errors, _warnings = validate_raw(
            raw_candidate(quote="this quote does not exist in any session"), self.home, {"min_evidence": 1}, self.sources
        )
        self.assertIsNone(candidate)
        self.assertTrue(any("not found" in error for error in errors))

    def test_unknown_source(self):
        candidate, errors, _warnings = validate_raw(
            raw_candidate(source="nope"), self.home, {"min_evidence": 1}, self.sources
        )
        self.assertIsNone(candidate)
        self.assertTrue(any("unknown source" in error for error in errors))

    def test_min_evidence(self):
        candidate, errors, _warnings = validate_raw(
            raw_candidate(), self.home, {"min_evidence": 2}, self.sources
        )
        self.assertIsNone(candidate)
        self.assertTrue(any("at least 2" in error for error in errors))

    def test_duplicate_and_rejection_memory(self):
        candidate, errors, _warnings = validate_raw(raw_candidate(), self.home, {"min_evidence": 1}, self.sources)
        self.assertIsNotNone(candidate)
        save_candidate(self.home, candidate)

        _candidate, errors, _warnings = validate_raw(raw_candidate(), self.home, {"min_evidence": 1}, self.sources)
        self.assertTrue(any("duplicate" in error for error in errors))

        mark_rejected(self.home, candidate, "not useful")
        _candidate, errors, _warnings = validate_raw(raw_candidate(), self.home, {"min_evidence": 1}, self.sources)
        self.assertTrue(any("previously rejected" in error for error in errors))
        with self.assertRaises(Exception):
            load_candidate(self.home, candidate["id"])


if __name__ == "__main__":
    unittest.main()
