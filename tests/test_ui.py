import base64
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy import demo, library, mailer, ui  # noqa: E402
from poppy.candidates import list_candidates, load_candidate, save_candidate  # noqa: E402
from poppy.config import load_config  # noqa: E402
from poppy.util import PoppyError, ensure_home_layout  # noqa: E402

CANDIDATE = {
    "id": "cand1",
    "kind": "memory",
    "title": "Prefers minimal dependencies",
    "summary": "Daniel prefers tools with few or no dependencies.",
    "trigger": "when choosing libraries",
    "scope": "user",
    "evidence": [{"source": "fixtures", "session": "s1", "quote": "I prefer no dependencies."}],
}


class TestUiServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        ensure_home_layout(self.home)
        library.ensure_library(self.home)
        self.cfg = load_config(self.home)
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        self.tmp.cleanup()

    def start(self, **kwargs):
        server = ui.build_server(self.home, self.cfg, host="127.0.0.1", port=0, **kwargs)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.servers.append(server)
        return server

    def request(self, server, path, method="GET", headers=None, body=None):
        url = f"http://127.0.0.1:{server.server_address[1]}{path}"
        request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def basic(self, token: str) -> dict:
        encoded = base64.b64encode(f"user:{token}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}

    def test_localhost_without_token(self):
        server = self.start()
        status, body = self.request(server, "/")
        self.assertEqual(status, 200)
        self.assertIn("<html", body.lower())

    def test_page_is_loaded_once_and_survives_file_removal(self):
        page = Path(self.tmp.name) / "index.html"
        page.write_text("<!doctype html><html>cached page</html>", encoding="utf-8")
        with mock.patch.object(ui, "INDEX_HTML", page):
            server = self.start()
            page.unlink()  # as after a package upgrade replaces the install directory
            status, body = self.request(server, "/")
        self.assertEqual(status, 200)
        self.assertIn("cached page", body)

    def test_missing_page_fails_before_binding(self):
        with mock.patch.object(ui, "INDEX_HTML", Path(self.tmp.name) / "nope.html"):
            with self.assertRaises(PoppyError) as caught:
                ui.build_server(self.home, self.cfg, host="127.0.0.1", port=0)
        self.assertIn("missing", str(caught.exception))

    def test_port_in_use_reports_clearly(self):
        server = self.start()
        port = server.server_address[1]
        with self.assertRaises(PoppyError) as caught:
            ui.build_server(self.home, self.cfg, host="127.0.0.1", port=port)
        self.assertIn("already in use", str(caught.exception))

    def test_non_loopback_requires_token(self):
        with self.assertRaises(PoppyError):
            ui.build_server(self.home, self.cfg, host="0.0.0.0", port=0)
        server = ui.build_server(self.home, self.cfg, host="0.0.0.0", port=0, insecure=True)
        server.server_close()

    def test_token_enforced(self):
        server = self.start(token="secret")
        status, _ = self.request(server, "/")
        self.assertEqual(status, 401)
        status, _ = self.request(server, "/", headers=self.basic("wrong"))
        self.assertEqual(status, 401)
        status, body = self.request(server, "/", headers=self.basic("secret"))
        self.assertEqual(status, 200)
        self.assertIn("<html", body.lower())

    def test_accept_forwards_rewrite_instructions_to_the_writer(self):
        save_candidate(self.home, {**CANDIDATE, "id": "cand-skill", "kind": "skill", "status": "pending"})
        with mock.patch.object(ui.threading, "Thread") as thread:
            result = ui.handle_action(
                self.home, self.cfg, "accept", {"id": "cand-skill", "instructions": "be terse"}
            )
        self.assertEqual(result, {"ok": True, "status": "writing"})
        self.assertEqual(thread.call_args.kwargs["args"][-1], "be terse")

    def test_config_state_and_update(self):
        server = self.start(token="secret")
        status, body = self.request(server, "/api/state", headers=self.basic("secret"))
        config = json.loads(body)["config"]
        self.assertTrue(config["path"].endswith("config.json"))
        keys = [field["key"] for field in config["fields"]]
        self.assertIn("agent.miner.cmd", keys)

        headers = {**self.basic("secret"), "Content-Type": "application/json"}
        status, body = self.request(
            server,
            "/api/action",
            method="POST",
            headers=headers,
            body=json.dumps({"action": "config_set", "values": {"decay_after_days": "30"}}).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        self.assertEqual(load_config(self.home)["decay_after_days"], 30)

        # invalid changes are rejected and nothing is written
        status, body = self.request(
            server,
            "/api/action",
            method="POST",
            headers=headers,
            body=json.dumps({"action": "config_set", "values": {"decay_after_days": 0}}).encode("utf-8"),
        )
        self.assertEqual(status, 400)
        self.assertIn("at least 1", json.loads(body)["error"])
        self.assertEqual(load_config(self.home)["decay_after_days"], 30)

        # the live server config changed with the save
        status, body = self.request(server, "/api/state", headers=self.basic("secret"))
        fields = {field["key"]: field for field in json.loads(body)["config"]["fields"]}
        self.assertEqual(fields["decay_after_days"]["value"], 30)

    def test_doctor_and_mine_actions(self):
        server = self.start(token="secret")
        headers = {**self.basic("secret"), "Content-Type": "application/json"}
        status, body = self.request(
            server,
            "/api/action",
            method="POST",
            headers=headers,
            body=json.dumps({"action": "doctor"}).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        checks = json.loads(body)["checks"]
        self.assertTrue(any(check["name"] == "python" for check in checks))
        self.assertTrue(all({"name", "status", "detail"} <= set(check) for check in checks))

        # a manual mining run starts; with no miner configured it fails cleanly
        status, body = self.request(
            server,
            "/api/action",
            method="POST",
            headers=headers,
            body=json.dumps({"action": "mine"}).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["running"])
        mining = {"running": True}
        deadline = time.time() + 5
        while time.time() < deadline:
            status, body = self.request(server, "/api/state", headers=self.basic("secret"))
            mining = json.loads(body)["mining"]
            if not mining.get("running"):
                break
            time.sleep(0.1)
        self.assertFalse(mining.get("running"))
        self.assertTrue(mining.get("error"))

    def test_api_state_and_action(self):
        entry = library.create_fact_entry(self.home, CANDIDATE)
        server = self.start(token="secret")

        status, body = self.request(server, "/api/state", headers=self.basic("secret"))
        self.assertEqual(status, 200)
        self.assertIn("library", body)

        # a cross-site form post cannot act on the library
        status, _ = self.request(
            server,
            "/api/action",
            method="POST",
            headers={**self.basic("secret"), "Content-Type": "text/plain"},
            body=b'{"action":"entry_verify"}',
        )
        self.assertEqual(status, 415)

        status, body = self.request(
            server,
            "/api/action",
            method="POST",
            headers={**self.basic("secret"), "Content-Type": "application/json"},
            body=json.dumps({"action": "entry_verify", "id": entry.id}).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])


class TestDecideLinks(unittest.TestCase):
    """The /decide routes used by notification emails: token-authenticated,
    read-only on GET, and single-use."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        ensure_home_layout(self.home)
        library.ensure_library(self.home)
        self.cfg = load_config(self.home)
        save_candidate(self.home, {**CANDIDATE, "status": "pending"})
        self.server = ui.build_server(self.home, self.cfg, host="127.0.0.1", port=0, token="secret")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, path, method="GET", body=None):
        url = f"http://127.0.0.1:{self.server.server_address[1]}{path}"
        data = body.encode("utf-8") if isinstance(body, str) else body
        headers = {"Content-Type": "application/x-www-form-urlencoded"} if data else {}
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def test_get_shows_the_confirm_page_without_changing_anything(self):
        token = mailer.mint_token(self.home, "cand1", "accept")
        status, body = self.request(f"/decide?token={token}")
        self.assertEqual(status, 200)
        self.assertIn("Prefers minimal dependencies", body)
        self.assertIn("Accept", body)
        self.assertEqual(load_candidate(self.home, "cand1")["status"], "pending")

    def test_post_accepts_and_burns_the_token(self):
        token = mailer.mint_token(self.home, "cand1", "accept")
        status, body = self.request("/decide", method="POST", body=f"token={token}")
        self.assertEqual(status, 200)
        self.assertIn("Accepted", body)
        self.assertEqual(len(library.list_entries(self.home, kinds=("memory",))), 1)
        self.assertTrue(mailer.token_used(self.home, token))
        self.assertEqual(self.request("/decide", method="POST", body=f"token={token}")[0], 410)
        self.assertEqual(self.request(f"/decide?token={token}")[0], 410)

    def test_reject_records_the_reason(self):
        token = mailer.mint_token(self.home, "cand1", "reject")
        status, body = self.request("/decide", method="POST", body=f"token={token}&reason=not+useful")
        self.assertEqual(status, 200)
        self.assertIn("Rejected", body)
        self.assertEqual(list_candidates(self.home), [])
        payload = json.loads((self.home / "rejected" / "cand1.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["rejection"]["reason"], "not useful")

    def test_bad_expired_and_replayed_tokens_are_refused(self):
        self.assertEqual(self.request("/decide?token=nope")[0], 410)
        expired = mailer.mint_token(self.home, "cand1", "accept", ttl_sec=-10)
        self.assertEqual(self.request(f"/decide?token={expired}")[0], 410)
        self.assertEqual(self.request("/decide", method="POST", body="token=nope")[0], 410)
        # the email token is not a substitute for the UI password elsewhere
        self.assertEqual(self.request("/api/state")[0], 401)


class TestUiDemo(unittest.TestCase):
    """`poppy ui --demo` serves mock data and never touches the home."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        ensure_home_layout(self.home)
        self.cfg = load_config(self.home)
        self.server = ui.build_server(self.home, self.cfg, host="127.0.0.1", port=0, demo=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, path, method="GET", body=None):
        url = f"http://127.0.0.1:{self.server.server_address[1]}{path}"
        request = urllib.request.Request(
            url,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers={} if body is None else {"Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def state(self):
        return self.request("/api/state")

    def act(self, action, **payload):
        return self.request("/api/action", method="POST", body={"action": action, **payload})

    def test_state_covers_every_card_and_row(self):
        state = self.state()
        self.assertTrue(state["demo"])
        self.assertEqual(state["home"], demo.DEMO_HOME)
        statuses = {c["status"] for c in state["candidates"]}
        self.assertTrue({"pending", "writing", "draft", "draft_failed", "draft_invalid", "writer_rejected"} <= statuses)
        kinds = {c["kind"] for c in state["candidates"]}
        self.assertTrue({"skill", "memory", "rule", "decay"} <= kinds)
        for group in ("skill", "memory", "rule"):
            self.assertTrue(state["library"][group], f"library has no {group} entries")
        self.assertTrue(state["archived"])

    def test_accept_memory_moves_it_into_the_library(self):
        state = self.state()
        candidate = next(c for c in state["candidates"] if c["id"] == "memory-lean-deps")
        result = self.act("accept", id=candidate["id"], scope="project", project="/tmp/demo-project")
        self.assertEqual(result["status"], "active")
        state = self.state()
        self.assertNotIn(candidate["id"], [c["id"] for c in state["candidates"]])
        entry = next(e for e in state["library"]["memory"] if e["title"] == candidate["title"])
        self.assertEqual(entry["scope"], "project")
        self.assertEqual(entry["project"], "/tmp/demo-project")

    def test_accept_skill_writes_then_drafts(self):
        with mock.patch.object(demo, "WRITER_DELAY", 0.01):
            result = self.act("accept", id="skill-verify-backup")
            self.assertEqual(result["status"], "writing")
            time.sleep(0.3)
        candidate = next(c for c in self.state()["candidates"] if c["id"] == "skill-verify-backup")
        self.assertEqual(candidate["status"], "draft")
        self.assertIn("## Steps", candidate["draft_preview"])
        self.assertIn("writing_started_at", candidate)

    def test_rewrite_instructions_are_stored_and_cleared(self):
        with mock.patch.object(demo, "WRITER_DELAY", 0.01):
            self.act("accept", id="skill-verify-backup", instructions="Focus on the wrapper, not the cron entry")
            time.sleep(0.3)
            candidate = next(c for c in self.state()["candidates"] if c["id"] == "skill-verify-backup")
            self.assertEqual(candidate["writer_instructions"], "Focus on the wrapper, not the cron entry")
            self.act("accept", id="skill-verify-backup", instructions="")
            time.sleep(0.3)
        candidate = next(c for c in self.state()["candidates"] if c["id"] == "skill-verify-backup")
        self.assertNotIn("writer_instructions", candidate)

    def test_install_moves_a_draft_into_the_library(self):
        self.act("install", id="skill-tap-check")
        state = self.state()
        self.assertNotIn("skill-tap-check", [c["id"] for c in state["candidates"]])
        self.assertTrue(
            any(e["title"] == "Check the Homebrew tap before installing" for e in state["library"]["skill"])
        )

    def test_reject_and_decay_clear_the_queue(self):
        self.act("reject", id="rule-no-force-push")
        self.act("resolve_decay", id="decay-4f2a91c3d8", resolution="archive")
        ids = [c["id"] for c in self.state()["candidates"]]
        self.assertNotIn("rule-no-force-push", ids)
        self.assertNotIn("decay-4f2a91c3d8", ids)

    def test_entry_actions_round_trip(self):
        self.act("entry_verify", id="memory-vault-creds")
        self.act("entry_pin", id="memory-vault-creds", pinned=False)
        entry = next(e for e in self.state()["library"]["memory"] if e["id"] == "memory-vault-creds")
        self.assertFalse(entry["pinned"])
        self.act("entry_archive", id="memory-vault-creds")
        state = self.state()
        self.assertNotIn("memory-vault-creds", [e["id"] for e in state["library"]["memory"]])
        self.assertIn("memory-vault-creds", [e["id"] for e in state["archived"]])
        self.act("entry_restore", id="memory-vault-creds")
        self.assertIn("memory-vault-creds", [e["id"] for e in self.state()["library"]["memory"]])

    def test_decay_card_restores_the_entry(self):
        before = next(e for e in self.state()["archived"] if e["id"] == "memory-openrouter-spend")
        self.act("resolve_decay", id="decay-4f2a91c3d8", resolution="restore")
        state = self.state()
        self.assertNotIn("decay-4f2a91c3d8", [c["id"] for c in state["candidates"]])
        self.assertNotIn("memory-openrouter-spend", [e["id"] for e in state["archived"]])
        entry = next(e for e in state["library"]["memory"] if e["id"] == "memory-openrouter-spend")
        self.assertNotEqual(entry["last_verified"], before["last_verified"])

    def test_decay_card_archive_confirms_and_clears(self):
        self.act("resolve_decay", id="decay-4f2a91c3d8", resolution="archive")
        state = self.state()
        self.assertNotIn("decay-4f2a91c3d8", [c["id"] for c in state["candidates"]])
        self.assertIn("memory-openrouter-spend", [e["id"] for e in state["archived"]])

    def test_config_updates_in_memory(self):
        fields = {field["key"]: field for field in self.state()["config"]["fields"]}
        self.assertIn("opencode", fields["agent.miner.cmd"]["value"])
        self.act("config_set", values={"decay_after_days": "45"})
        fields = {field["key"]: field for field in self.state()["config"]["fields"]}
        self.assertEqual(fields["decay_after_days"]["value"], 45)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.act("config_set", values={"decay_after_days": 0})
        self.assertEqual(caught.exception.code, 400)
        self.assertIn("at least 1", json.loads(caught.exception.read())["error"])
        fields = {field["key"]: field for field in self.state()["config"]["fields"]}
        self.assertEqual(fields["decay_after_days"]["value"], 45)

    def test_doctor_action_lists_checks(self):
        names = [check["name"] for check in self.act("doctor")["checks"]]
        self.assertIn("python", names)
        self.assertNotIn("agent.miner.test", names)
        names = [check["name"] for check in self.act("doctor", agent=True)["checks"]]
        self.assertIn("agent.miner.test", names)

    def test_mine_runs_once_at_a_time_and_adds_a_candidate(self):
        with mock.patch.object(demo, "MINE_DELAY", 0.05):
            self.assertTrue(self.act("mine")["running"])
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.act("mine")
            self.assertEqual(caught.exception.code, 400)
            time.sleep(0.3)
        state = self.state()
        self.assertFalse(state["mining"]["running"])
        self.assertEqual(state["mining"]["accepted"], 1)
        self.assertIn("skill-quiet-cron", [c["id"] for c in state["candidates"]])

    def test_demo_never_writes_to_the_home(self):
        before = sorted(str(path) for path in self.home.rglob("*"))
        self.act("install", id="skill-tap-check")
        self.act("accept", id="memory-lean-deps")
        self.act("entry_archive", id="backup-restore-drill")
        after = sorted(str(path) for path in self.home.rglob("*"))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
