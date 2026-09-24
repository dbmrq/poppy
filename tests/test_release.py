import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGING = REPO / "packaging"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check_version = load_module("poppy_check_version", PACKAGING / "check_version.py")
render_formula = load_module("poppy_render_formula", PACKAGING / "render_homebrew_formula.py")


class TestReleaseScripts(unittest.TestCase):
    def test_check_version_accepts_matching_tag(self):
        version = check_version.package_version()
        self.assertEqual(check_version.main(["check_version.py", f"v{version}"]), 0)
        self.assertEqual(check_version.main(["check_version.py", version]), 0)

    def test_check_version_rejects_mismatch(self):
        self.assertEqual(check_version.main(["check_version.py", "v9.9.9"]), 1)

    def test_render_formula_fills_url_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdist = Path(tmp) / "poppy_ai-9.9.9.tar.gz"
            sdist.write_bytes(b"fake sdist")
            formula = render_formula.render(sdist)
        self.assertIn("class PoppyAi < Formula", formula)
        self.assertNotIn("__SDIST_URL__", formula)
        self.assertNotIn("__SDIST_SHA256__", formula)
        self.assertIn("files.pythonhosted.org/packages/", formula)
        self.assertIn("poppy_ai-9.9.9.tar.gz", formula)
        self.assertIn(hashlib.sha256(b"fake sdist").hexdigest(), formula)

    def test_files_url_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdist = Path(tmp) / "poppy_ai-9.9.9.tar.gz"
            sdist.write_bytes(b"x")
            url = render_formula.files_url(sdist)
        digest = hashlib.blake2b(b"x", digest_size=32).hexdigest()
        self.assertEqual(
            url,
            "https://files.pythonhosted.org/packages/"
            f"{digest[:2]}/{digest[2:4]}/{digest[4:]}/poppy_ai-9.9.9.tar.gz",
        )


if __name__ == "__main__":
    unittest.main()
