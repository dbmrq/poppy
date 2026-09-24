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

    def test_scan_archives_stale_entries_and_skips_pinned(self):
        cards = decay.scan(self.home, {"decay_after_days": 90})
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["target"], self.old.id)
        self.assertEqual(cards[0]["kind"], "decay")
        self.assertIn("archived automatically", cards[0]["summary"])
        entry = library.find_entry(self.home, self.old.id, include_archived=True)
        self.assertTrue(entry.archived)
        self.assertEqual(library.list_entries(self.home), [self.pinned])

        # idempotent: the open card is returned again and nothing is archived twice
        again = decay.scan(self.home, {"decay_after_days": 90})
        self.assertEqual([card["id"] for card in again], [cards[0]["id"]])
        self.assertEqual(len(library.list_entries(self.home, include_archived=True)), 2)

    def test_scan_dry_run_changes_nothing(self):
        cards = decay.scan(self.home, {"decay_after_days": 90}, dry_run=True)
        self.assertEqual(len(cards), 1)
        self.assertFalse(library.find_entry(self.home, self.old.id).archived)
        self.assertEqual(list((self.home / "candidates").glob("*.json")), [])

    def test_resolve_restore_brings_it_back_and_refreshes(self):
        card = decay.scan(self.home, {"decay_after_days": 90})[0]
        decay.resolve(self.home, {}, card["id"], "restore")
        entry = library.find_entry(self.home, self.old.id)
        self.assertFalse(entry.archived)
        self.assertNotEqual(entry.meta.get("last_verified"), OLD)
        self.assertFalse((self.home / "candidates" / f"{card['id']}.json").exists())
        # refreshed, so the next scan leaves it alone
        self.assertEqual(decay.scan(self.home, {"decay_after_days": 90}), [])

    def test_resolve_archive_keeps_it_archived_and_clears_the_card(self):
        card = decay.scan(self.home, {"decay_after_days": 90})[0]
        decay.resolve(self.home, {}, card["id"], "archive")
        entry = library.find_entry(self.home, self.old.id, include_archived=True)
        self.assertTrue(entry.archived)
        self.assertFalse((self.home / "candidates" / f"{card['id']}.json").exists())

    def test_card_for_a_manually_restored_entry_is_dropped_as_moot(self):
        card = decay.scan(self.home, {"decay_after_days": 90})[0]
        library.restore_entry(self.home, library.find_entry(self.home, self.old.id, include_archived=True))
        self.assertEqual(decay.scan(self.home, {"decay_after_days": 90}), [])
        self.assertFalse((self.home / "candidates" / f"{card['id']}.json").exists())


if __name__ == "__main__":
    unittest.main()
