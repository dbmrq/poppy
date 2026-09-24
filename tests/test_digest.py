import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy import digest, library  # noqa: E402
from poppy.config import load_config  # noqa: E402
from poppy.util import ensure_home_layout  # noqa: E402

BASE = {
    "id": "c1",
    "kind": "memory",
    "title": "Prefers minimal dependencies",
    "summary": "Daniel prefers tools with no dependencies.",
    "trigger": "when choosing libraries",
    "scope": "user",
    "evidence": [{"source": "f", "session": "s", "quote": "quote", "excerpt": "…"}],
}


class TestDigest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        ensure_home_layout(self.home)
        library.ensure_library(self.home)
        self.cfg = load_config(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def fact(self, **overrides):
        candidate = dict(BASE)
        candidate.update(overrides)
        return library.create_fact_entry(self.home, candidate)

    def test_binding_rules_inline_and_memory_headlines(self):
        self.fact(
            id="r1",
            kind="rule",
            title="Never edit generated files",
            summary="Do not edit generated files.",
            trigger="when touching generated code",
        )
        memory = self.fact()
        self.fact(
            id="r2",
            kind="rule",
            title="Never deploy on Fridays",
            summary="Avoid Friday deploys.",
            trigger="when releasing",
            scope="project",
            project="/tmp/proj",
        )
        text, stats = digest.build_digest(self.home, self.cfg)
        self.assertIn("Never edit generated files", text)
        self.assertIn("Do not edit generated files.", text)  # binding rule body inline
        self.assertIn("Prefers minimal dependencies", text)
        self.assertIn(f"({digest.short_ref(memory)})", text)
        self.assertIn("when choosing libraries", text)
        self.assertNotIn("Daniel prefers tools with no dependencies.", text)  # body not inline
        self.assertIn("Scoped rules", text)
        self.assertIn("Never deploy on Fridays", text)
        self.assertEqual(stats["binding_rules"], 1)
        self.assertEqual(stats["memories"], 1)
        self.assertEqual(stats["scoped_rules"], 1)

    def test_export_status_and_freshness(self):
        self.fact()
        digest.export(self.home, self.cfg)
        status = digest.status(self.home, self.cfg)
        self.assertTrue(status["exists"])
        self.assertTrue(status["fresh"])

        self.fact(id="c2", title="Another fact", summary="Another.")
        self.assertFalse(digest.status(self.home, self.cfg)["fresh"])

        digest.export(self.home, self.cfg)
        self.assertTrue(digest.status(self.home, self.cfg)["fresh"])

    def test_wire_and_unwire(self):
        self.fact()
        target = Path(self.tmp.name) / "AGENTS.md"
        target.write_text("# My rules\n\nKeep me.\n", encoding="utf-8")

        digest.wire(self.home, self.cfg, target)
        content = target.read_text(encoding="utf-8")
        self.assertIn("Keep me.", content)
        self.assertIn(digest.BEGIN, content)
        self.assertIn("Prefers minimal dependencies", content)

        status = digest.status(self.home, self.cfg)
        self.assertEqual(status["targets"][0]["file"], str(target))
        self.assertTrue(status["targets"][0]["block"])

        digest.wire(self.home, self.cfg, target)  # idempotent
        self.assertEqual(target.read_text(encoding="utf-8").count(digest.BEGIN), 1)

        digest.unwire(self.home, self.cfg, target)
        content = target.read_text(encoding="utf-8")
        self.assertNotIn(digest.BEGIN, content)
        self.assertIn("Keep me.", content)
        self.assertEqual(digest.status(self.home, self.cfg)["targets"], [])

    def test_budget_truncation(self):
        for index in range(45):
            self.fact(id=f"c{index}", title=f"Fact number {index}", summary=f"Summary {index}.")
        text, stats = digest.build_digest(self.home, self.cfg)
        self.assertEqual(stats["memories"], 40)
        self.assertEqual(stats["dropped"], 5)
        self.assertIn("+5 more", text)

    def test_verify_pass_and_fail(self):
        self.fact(
            id="r1",
            kind="rule",
            title="Never edit generated files",
            summary="Do not edit generated files.",
            trigger="when touching generated code",
        )
        good = Path(self.tmp.name) / "good.py"
        good.write_text(
            "import os, pathlib\n"
            "home = pathlib.Path(os.environ['POPPY_HOME'])\n"
            "text = (home / 'context' / 'poppy.md').read_text()\n"
            "for line in text.splitlines():\n"
            "    if line.startswith('- ['):\n"
            "        print(line.split(']', 1)[-1].strip()[:32])\n"
            "        break\n",
            encoding="utf-8",
        )
        cfg = dict(self.cfg)
        cfg["agent"] = {"miner": {"cmd": [sys.executable, str(good), "{prompt}"]}, "writer": {"cmd": None}}
        digest.export(self.home, cfg)
        ok, detail = digest.verify(self.home, cfg)
        self.assertTrue(ok, detail)

        bad = Path(self.tmp.name) / "bad.py"
        bad.write_text("print('NONE')\n", encoding="utf-8")
        cfg["agent"]["miner"]["cmd"] = [sys.executable, str(bad), "{prompt}"]
        ok, detail = digest.verify(self.home, cfg)
        self.assertFalse(ok)
        self.assertIn("no Poppy block", detail)


if __name__ == "__main__":
    unittest.main()
