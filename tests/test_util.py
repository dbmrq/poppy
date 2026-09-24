import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy.util import (  # noqa: E402
    PoppyError,
    extended_path,
    norm_ws,
    parse_duration,
    redact_userinfo,
    scan_secrets,
    sha,
)


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

    def test_extended_path_keeps_precedence_and_appends(self):
        out = extended_path("/first:/usr/bin", "/first:/extra")
        parts = out.split(os.pathsep)
        self.assertEqual(parts[0], "/first")
        self.assertEqual(parts[1], "/usr/bin")
        self.assertIn("/extra", parts)
        self.assertLess(parts.index("/usr/bin"), parts.index("/extra"))
        # fallback directories are always present, and nothing repeats
        self.assertIn("/bin", parts)
        self.assertIn("/usr/sbin", parts)
        self.assertEqual(len(parts), len(set(parts)))

    def test_extended_path_from_empty_base(self):
        parts = extended_path("", "/opt/homebrew/bin").split(os.pathsep)
        self.assertEqual(parts[0], "/opt/homebrew/bin")
        self.assertIn("/usr/bin", parts)

    def test_redact_userinfo(self):
        self.assertEqual(
            redact_userinfo("fetch https://user:tok@github.com/x.git failed"),
            "fetch https://***@github.com/x.git failed",
        )
        self.assertEqual(redact_userinfo("plain https://github.com/x.git"), "plain https://github.com/x.git")
        self.assertEqual(redact_userinfo(None), "")


if __name__ == "__main__":
    unittest.main()
