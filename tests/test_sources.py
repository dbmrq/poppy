import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy.sources import CommandSource, FilesSource, PoppyError, SqliteSource  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jsonl"


class TestFilesSource(unittest.TestCase):
    def test_list_search_read(self):
        source = FilesSource("fixtures", {"path": str(FIXTURES), "glob": "*.jsonl"})
        sessions = source.list_sessions()
        self.assertEqual(len(sessions), 2)
        self.assertEqual({s.id for s in sessions}, {"session-alpha.jsonl", "session-beta.jsonl"})

        hits = source.search("frozen-lockfile")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].session, "session-alpha.jsonl")

        text = source.read_session("session-alpha.jsonl")
        self.assertIn("reproducible", text)

        future = __import__("time").time() + 60
        self.assertEqual(source.list_sessions(since=future), [])

    def test_path_escape_rejected(self):
        source = FilesSource("fixtures", {"path": str(FIXTURES)})
        with self.assertRaises(PoppyError):
            source.read_session("../outside.jsonl")


class TestSqliteSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "sessions.db"
        conn = sqlite3.connect(self.db)
        conn.executescript(
            """
            CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, updated INTEGER);
            CREATE TABLE messages (session_id TEXT, seq INTEGER, body TEXT);
            INSERT INTO sessions VALUES ('s1', 'First session', 1790000000000);
            INSERT INTO messages VALUES ('s1', 1, 'hello world');
            INSERT INTO messages VALUES ('s1', 2, 'the widget flags are --fast and --dry');
            """
        )
        conn.commit()
        conn.close()
        self.source = SqliteSource(
            "db",
            {
                "path": str(self.db),
                "list_query": "SELECT id, title, updated FROM sessions ORDER BY updated DESC LIMIT :limit",
                "read_query": "SELECT body AS text FROM messages WHERE session_id = :id ORDER BY seq",
            },
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_read_search(self):
        sessions = self.source.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].id, "s1")
        self.assertEqual(int(sessions[0].time), 1790000000)

        text = self.source.read_session("s1")
        self.assertIn("hello world", text)

        hits = self.source.search("--dry")
        self.assertEqual(len(hits), 1)
        self.assertIn("--dry", hits[0].snippet)


class TestCommandSource(unittest.TestCase):
    def test_command_source(self):
        lister = 'import json; print(json.dumps([{"id": "abc", "title": "T", "time": 1790000000}]))'
        reader = 'print("command source text with a marker-token")'
        source = CommandSource(
            "cmd",
            {
                "list_cmd": [sys.executable, "-c", lister],
                "read_cmd": [sys.executable, "-c", reader],
            },
        )
        sessions = source.list_sessions()
        self.assertEqual(sessions[0].id, "abc")
        self.assertIn("marker-token", source.read_session("abc"))
        self.assertEqual(len(source.search("marker-token")), 1)


if __name__ == "__main__":
    unittest.main()
