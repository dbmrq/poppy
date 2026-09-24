import base64
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy import library, ui  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
