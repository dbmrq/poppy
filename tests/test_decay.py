import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy import decay, library  # noqa: E402
from poppy.util import ensure_home_layout  # noqa: E402

OLD = "2020-01-01T00:00:00Z"

CANDIDATE = {
    "id": "cand1",
    "kind": "memory",
    "title": "An old fact",
    "summary": "A fact that has not been used in years.",
    "trigger": "never",
    "scope": "user",
    "evidence": [{"source": "f", "session": "s", "quote": "quote", "excerpt": "…"}],
}


class TestDecay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        ensure_home_layout(self.home)
        library.ensure_library(self.home)
        self.old = library.create_fact_entry(self.home, CANDIDATE)
        library.update_entry(self.old, created=OLD, last_verified=OLD)
        self.pinned = library.create_fact_entry(
            self.home, {**CANDIDATE, "id": "cand2", "title": "A pinned fact", "summary": "Pinned."}
        )
        library.update_entry(self.pinned, created=OLD, last_verified=OLD)
        library.set_pinned(self.pinned, True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_skips_pinned_and_is_idempotent(self):
        proposals = decay.scan(self.home, {"decay_after_days": 90})
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["target"], self.old.id)
        self.assertEqual(proposals[0]["kind"], "decay")

        again = decay.scan(self.home, {"decay_after_days": 90})
        self.assertEqual(again, [])

    def test_resolve_keep(self):
        proposal = decay.scan(self.home, {"decay_after_days": 90})[0]
        decay.resolve(self.home, {}, proposal["id"], "keep")
        entry = library.find_entry(self.home, self.old.id)
        self.assertFalse(entry.archived)
        self.assertNotEqual(entry.meta.get("last_verified"), OLD)
        self.assertFalse((self.home / "candidates" / f"{proposal['id']}.json").exists())

    def test_resolve_archive_and_restore(self):
        proposal = decay.scan(self.home, {"decay_after_days": 90})[0]
        decay.resolve(self.home, {}, proposal["id"], "archive")
        entry = library.find_entry(self.home, self.old.id)
        self.assertTrue(entry.archived)
        self.assertEqual(library.list_entries(self.home), [self.pinned])
        library.restore_entry(self.home, entry)
        self.assertEqual(len(library.list_entries(self.home)), 2)

    def test_resolve_pin(self):
        proposal = decay.scan(self.home, {"decay_after_days": 90})[0]
        decay.resolve(self.home, {}, proposal["id"], "pin")
        self.assertTrue(library.find_entry(self.home, self.old.id).pinned)


if __name__ == "__main__":
    unittest.main()
