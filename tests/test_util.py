import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy.util import PoppyError, norm_ws, parse_duration, scan_secrets, sha  # noqa: E402


class TestUtil(unittest.TestCase):
    def test_parse_duration(self):
        self.assertEqual(parse_duration("14d"), 14 * 86400)
        self.assertEqual(parse_duration("36h"), 36 * 3600)
        self.assertEqual(parse_duration("90m"), 90 * 60)
        self.assertEqual(parse_duration("3600"), 3600)
        with self.assertRaises(PoppyError):
            parse_duration("soon")

    def test_norm_ws(self):
        self.assertEqual(norm_ws("a\n\n  b\tc "), "a b c")
        self.assertEqual(norm_ws(None), "")

    def test_sha_stable(self):
        self.assertEqual(sha("hello"), sha("hello"))
        self.assertNotEqual(sha("hello"), sha("hello!"))

    def test_scan_secrets(self):
        high = scan_secrets("token sk-" + "a" * 30)
        self.assertTrue(any(severity == "high" for severity, _, _ in high))
        low = scan_secrets('api_key = "abcdefghijklmnop"')
        self.assertTrue(any(severity == "low" for severity, _, _ in low))
        self.assertEqual(scan_secrets("nothing to see here"), [])


if __name__ == "__main__":
    unittest.main()
