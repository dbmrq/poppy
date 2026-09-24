import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy import library  # noqa: E402
from poppy.util import ensure_home_layout  # noqa: E402

BASE = {
    "id": "cand1",
    "kind": "memory",
    "title": "Prefers minimal dependencies",
    "summary": "Daniel prefers tools with few or no dependencies.",
    "trigger": "when choosing libraries",
    "scope": "user",
    "evidence": [
        {"source": "fixtures", "session": "s1", "quote": "I prefer tools with no dependencies.", "excerpt": "…"}
    ],
}


class TestLibrary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        ensure_home_layout(self.home)
        library.ensure_library(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, **overrides):
        candidate = dict(BASE)
        candidate.update(overrides)
        return library.create_fact_entry(self.home, candidate)

    def test_create_list_verify_pin_archive_restore(self):
        entry = self.make()
        self.assertEqual(entry.kind, "memory")
        self.assertTrue(entry.path.is_file())

        listed = library.list_entries(self.home)
        self.assertEqual([e.id for e in listed], [entry.id])
        self.assertEqual(listed[0].title, "Prefers minimal dependencies")
        # the body is the agent-facing text; quotes stay on the source candidate
        self.assertIn("few or no dependencies", listed[0].body)
        self.assertNotIn("Evidence", listed[0].body)
        self.assertNotIn("## Evidence", listed[0].to_dict()["body"])

        library.set_pinned(listed[0], True)
        self.assertTrue(library.find_entry(self.home, entry.id).pinned)

        library.verify_entry(library.find_entry(self.home, entry.id))
        self.assertTrue(library.find_entry(self.home, entry.id).meta.get("last_verified"))

        library.archive_entry(self.home, library.find_entry(self.home, entry.id))
        self.assertEqual(library.list_entries(self.home), [])
        archived = library.list_entries(self.home, include_archived=True)
        self.assertEqual(len(archived), 1)
        self.assertTrue(archived[0].archived)

        library.restore_entry(self.home, archived[0])
        self.assertEqual(len(library.list_entries(self.home)), 1)

    def test_delivery_body_strips_legacy_evidence_sections(self):
        legacy = "A fact that matters.\n\n## Evidence\n- source:session — “quote”"
        self.assertEqual(library.delivery_body(legacy), "A fact that matters.")
        self.assertEqual(library.delivery_body("Nothing to strip."), "Nothing to strip.")

    def test_scope_matching(self):
        user_entry = self.make(title="User fact")
        machine_entry = self.make(id="cand2", title="Machine fact", scope="machine")
        project_entry = self.make(
            id="cand3", title="Project fact", scope="project", project="/tmp/poppy-project"
        )
        # move the machine entry to a different host so it cannot match here
        library.update_entry(library.find_entry(self.home, machine_entry.id), machine="not-this-host")

        context = library.build_context(
            self.home, {}, cwd=Path("/tmp/poppy-project/sub"), mark_used=False
        )
        titles = [entry["title"] for entry in context["memories"]]
        self.assertIn("User fact", titles)
        self.assertIn("Project fact", titles)
        self.assertNotIn("Machine fact", titles)

        elsewhere = library.build_context(self.home, {}, cwd=Path("/tmp/other"), mark_used=False)
        titles = [entry["title"] for entry in elsewhere["memories"]]
        self.assertIn("User fact", titles)
        self.assertNotIn("Project fact", titles)

        on_host = library.update_entry(
            library.find_entry(self.home, machine_entry.id), machine=socket.gethostname().split(".")[0]
        )
        context = library.build_context(self.home, {}, cwd=Path("/tmp/other"), mark_used=False)
        self.assertIn(on_host.title, [entry["title"] for entry in context["memories"]])

    def test_usage_touch_and_context_marks_used(self):
        self.make()
        context = library.build_context(self.home, {}, cwd=Path("/tmp"), mark_used=True)
        entry_id = context["memories"][0]["id"]
        usage = library.load_usage(self.home)
        self.assertEqual(usage[entry_id]["uses"], 1)

    def test_rules_are_separate_from_memories(self):
        self.make(id="r1", kind="rule", title="Never edit generated files", summary="Do not edit generated files.")
        context = library.build_context(self.home, {}, cwd=Path("/tmp"), mark_used=False)
        self.assertEqual(len(context["rules"]), 1)
        self.assertEqual(len(context["memories"]), 0)


class TestBuiltins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        ensure_home_layout(self.home)
        self.builtin = Path(self.tmp.name) / "builtin"
        self.source = self.builtin / "demo"
        self.source.mkdir(parents=True)
        self.write_builtin("v1")

    def tearDown(self):
        self.tmp.cleanup()

    def write_builtin(self, marker: str) -> None:
        (self.source / "SKILL.md").write_text(
            f"---\nname: demo\ndescription: Demo skill. Use when testing.\n---\n\n{marker}\n",
            encoding="utf-8",
        )

    def test_refresh_respects_user_edits(self):
        with mock.patch.object(library, "BUILTIN_DIR", self.builtin):
            self.assertEqual(library.ensure_builtin_skills(self.home), ["demo"])
            installed = library.skills_dir(self.home) / "demo" / "SKILL.md"
            self.assertIn("v1", installed.read_text(encoding="utf-8"))

            # an unmodified copy is refreshed when the builtin changes
            self.write_builtin("v2")
            self.assertEqual(library.ensure_builtin_skills(self.home), ["demo"])
            self.assertIn("v2", installed.read_text(encoding="utf-8"))

            # a user-edited copy is left alone
            installed.write_text(
                installed.read_text(encoding="utf-8") + "\nuser edit\n", encoding="utf-8"
            )
            self.write_builtin("v3")
            self.assertEqual(library.ensure_builtin_skills(self.home), [])
            self.assertIn("user edit", installed.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
