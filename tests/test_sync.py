import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy import digest, library, sync  # noqa: E402
from poppy.config import load_config  # noqa: E402
from poppy.skills import archive_skill, load_manifest, reconcile_mirrors  # noqa: E402
from poppy.util import ensure_home_layout  # noqa: E402

GIT = shutil.which("git")


def make_home(root: Path, name: str) -> tuple[Path, Path, dict]:
    home = root / name
    skills = root / f"{name}-skills"
    ensure_home_layout(home)
    library.ensure_library(home)
    cfg = load_config(home)
    cfg["skills_dirs"] = [str(skills)]
    return home, skills, cfg


def memory_candidate(title: str = "Remember the widget port", summary: str = "The widget runs on port 4711.") -> dict:
    return {
        "id": f"cand-{title[:4].lower().replace(' ', '-')}",
        "kind": "memory",
        "title": title,
        "summary": summary,
        "trigger": "when configuring the widget",
        "scope": "user",
        "evidence": [{"source": "fixtures", "session": "session-alpha.jsonl", "quote": "quote"}],
    }


@unittest.skipUnless(GIT, "git is not installed")
class TestSync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def bare_remote(self) -> Path:
        remote = self.root / "remote.git"
        subprocess.run([GIT, "init", "--bare", "-q", str(remote)], check=True)
        return remote

    def test_init_creates_repo_and_gitignore(self):
        home, skills, cfg = make_home(self.root, "a")
        library.ensure_builtin_skills(home)
        result = sync.init(home, cfg)

        self.assertTrue(sync.repo_exists(home))
        self.assertEqual(result["branch"], "main")
        self.assertTrue((home / ".gitignore").is_file())
        tracked = sync.git(home, ["ls-files"]).stdout.splitlines()
        self.assertIn(".gitignore", tracked)
        self.assertTrue(any(path.startswith("library/") for path in tracked))
        self.assertFalse(any(path.startswith("config.json") for path in tracked))
        self.assertFalse(any(path.startswith("state/") for path in tracked))
        self.assertFalse(any("poppy.md" in path for path in tracked))

        self.assertTrue((skills / "poppy-context" / "SKILL.md").is_file())
        self.assertTrue(digest.digest_path(home).is_file())

    def test_round_trip_between_machines(self):
        remote = self.bare_remote()
        home_a, _skills_a, cfg_a = make_home(self.root, "a")
        library.ensure_builtin_skills(home_a)
        library.create_fact_entry(home_a, memory_candidate())
        sync.init(home_a, cfg_a, remote=str(remote))

        home_b, skills_b, cfg_b = make_home(self.root, "b")
        sync.init(home_b, cfg_b, remote=str(remote))

        self.assertTrue((library.skills_dir(home_b) / "poppy-context" / "SKILL.md").is_file())
        self.assertTrue((skills_b / "poppy-context" / "SKILL.md").is_file())
        titles = [entry.title for entry in library.list_entries(home_b)]
        self.assertIn("Remember the widget port", titles)
        digest_text = digest.digest_path(home_b).read_text(encoding="utf-8")
        self.assertIn("Remember the widget port", digest_text)

    def test_archive_propagates_and_removes_mirrors(self):
        remote = self.bare_remote()
        home_a, _skills_a, cfg_a = make_home(self.root, "a")
        library.ensure_builtin_skills(home_a)
        sync.init(home_a, cfg_a, remote=str(remote))

        home_b, skills_b, cfg_b = make_home(self.root, "b")
        sync.init(home_b, cfg_b, remote=str(remote))
        self.assertTrue((skills_b / "poppy-context").is_dir())

        archive_skill(home_a, "poppy-context")
        result_a = sync.run(home_a, cfg_a)
        self.assertEqual(result_a["code"], 1)
        self.assertTrue(result_a["pushed"])

        result_b = sync.run(home_b, cfg_b)
        self.assertEqual(result_b["code"], 1)
        self.assertTrue(result_b["pulled"])
        self.assertFalse((skills_b / "poppy-context").exists())
        self.assertFalse((library.skills_dir(home_b) / "poppy-context").exists())
        self.assertNotIn("poppy-context", load_manifest(home_b)["skills"])

    def test_conflict_aborts_and_surfaces(self):
        remote = self.bare_remote()
        home_a, _skills_a, cfg_a = make_home(self.root, "a")
        entry = library.create_fact_entry(home_a, memory_candidate())
        sync.init(home_a, cfg_a, remote=str(remote))

        home_b, _skills_b, cfg_b = make_home(self.root, "b")
        sync.init(home_b, cfg_b, remote=str(remote))
        entry_b = library.find_entry(home_b, entry.id)

        entry.path.write_text(
            entry.path.read_text(encoding="utf-8") + "\nA change\n", encoding="utf-8"
        )
        self.assertEqual(sync.run(home_a, cfg_a)["code"], 1)

        entry_b.path.write_text(
            entry_b.path.read_text(encoding="utf-8") + "\nB change\n", encoding="utf-8"
        )
        result_b = sync.run(home_b, cfg_b)
        self.assertEqual(result_b["code"], 2)
        self.assertTrue(result_b["conflict"])
        self.assertFalse((home_b / ".git" / "rebase-merge").exists())
        self.assertFalse((home_b / ".git" / "rebase-apply").exists())

        state = sync.status(home_b, cfg_b)
        self.assertGreaterEqual(state["ahead"], 1)
        self.assertGreaterEqual(state["behind"], 1)
        self.assertEqual(sync.load_state(home_b)["status"], "conflict")

    def test_offline_soft_fails(self):
        remote = self.bare_remote()
        home, _skills, cfg = make_home(self.root, "a")
        library.create_fact_entry(home, memory_candidate())
        sync.init(home, cfg, remote=str(remote))
        shutil.rmtree(remote)

        library.create_fact_entry(
            home, memory_candidate(title="Another fact", summary="Something else entirely.")
        )
        result = sync.run(home, cfg)
        self.assertTrue(result["offline"])
        self.assertTrue(result["committed"])
        self.assertEqual(result["code"], 1)  # local work done; pushes next run
        self.assertEqual(sync.load_state(home)["status"], "offline")

    def test_reconcile_mirrors_refreshes_and_removes(self):
        home, skills, cfg = make_home(self.root, "a")
        library.ensure_builtin_skills(home)

        first = reconcile_mirrors(home, cfg)
        self.assertIn("poppy-context", first["installed"])
        self.assertTrue((skills / "poppy-context").is_dir())

        second = reconcile_mirrors(home, cfg)
        self.assertEqual(second["installed"], [])
        self.assertEqual(second["updated"], [])

        skill_md = library.skills_dir(home) / "poppy-context" / "SKILL.md"
        skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\nExtra line.\n", encoding="utf-8")
        third = reconcile_mirrors(home, cfg)
        self.assertIn("poppy-context", third["updated"])
        self.assertIn("Extra line.", (skills / "poppy-context" / "SKILL.md").read_text(encoding="utf-8"))

        # gone from the library (as after a sync pull): mirror and manifest entry removed
        shutil.rmtree(library.skills_dir(home) / "poppy-context")
        fourth = reconcile_mirrors(home, cfg)
        self.assertIn("poppy-context", fourth["removed"])
        self.assertFalse((skills / "poppy-context").exists())
        self.assertNotIn("poppy-context", load_manifest(home)["skills"])


    def test_mirrors_follow_config_changes(self):
        home, skills, cfg = make_home(self.root, "a")
        library.ensure_builtin_skills(home)
        cfg["skills_dirs"] = []
        first = reconcile_mirrors(home, cfg)
        self.assertIn("poppy-context", first["installed"])

        cfg["skills_dirs"] = [str(skills)]
        second = reconcile_mirrors(home, cfg)
        self.assertIn("poppy-context", second["updated"])
        self.assertTrue((skills / "poppy-context" / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
