import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poppy import cli, mailer  # noqa: E402
from poppy.config import load_config, save_config  # noqa: E402
from poppy.candidates import save_candidate  # noqa: E402
from poppy.util import PoppyError, ensure_home_layout  # noqa: E402

CANDIDATE = {
    "id": "cand1",
    "kind": "memory",
    "title": "Prefers minimal dependencies",
    "summary": "Daniel prefers tools with few or no dependencies.",
    "trigger": "when choosing libraries",
    "scope": "user",
    "evidence": [{"source": "fixtures", "session": "s1", "quote": "I prefer no dependencies.", "excerpt": "…"}],
}


def email_cfg(**overrides):
    block = {
        "enabled": True,
        "host": "smtp.example.com",
        "port": "",
        "security": "starttls",
        "user": "poppy",
        "password": "secret",
        "from": "poppy@example.com",
        "to": "daniel@example.com",
        "base_url": "http://10.0.0.10:8788",
    }
    block.update(overrides)
    return {"email": block, "ui": {"host": "127.0.0.1", "port": 8788}}


class FakeSMTP:
    instances: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.started = False
        self.logged = None
        self.messages = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.started = True

    def login(self, user, password):
        self.logged = (user, password)

    def send_message(self, message):
        self.messages.append(message)


class TestMailer(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances = []
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        ensure_home_layout(self.home)
        self.cfg = email_cfg()

    def tearDown(self):
        self.tmp.cleanup()

    def test_tokens_roundtrip_and_expire(self):
        token = mailer.mint_token(self.home, "cand1", "accept")
        self.assertEqual(mailer.verify_token(self.home, token), ("cand1", "accept"))
        self.assertIsNone(mailer.verify_token(self.home, token + "x"))
        self.assertIsNone(mailer.verify_token(self.home, "not-a-token"))
        self.assertIsNone(mailer.verify_token(self.home, mailer.mint_token(self.home, "cand1", "accept", ttl_sec=-10)))
        with self.assertRaises(PoppyError):
            mailer.mint_token(self.home, "cand1", "explode")
        # a token from another home (other secret) is rejected
        other = Path(self.tmp.name) / "other"
        ensure_home_layout(other)
        self.assertIsNone(mailer.verify_token(other, token))

    def test_tokens_are_single_use(self):
        token = mailer.mint_token(self.home, "cand1", "reject")
        self.assertFalse(mailer.token_used(self.home, token))
        self.assertTrue(mailer.consume_token(self.home, token))
        self.assertTrue(mailer.token_used(self.home, token))
        self.assertFalse(mailer.consume_token(self.home, token))

    def test_email_config_env_overrides(self):
        with mock.patch.dict(os.environ, {"SMTP_HOST": "env.example.com", "SMTP_FROM": "env@example.com"}):
            block = mailer.email_config(self.cfg)
        self.assertEqual(block["host"], "env.example.com")
        self.assertEqual(block["from"], "env@example.com")
        self.assertEqual(block["to"], "daniel@example.com")

    def test_email_ready(self):
        self.assertEqual(mailer.email_ready(self.cfg), (True, ""))
        ready, reason = mailer.email_ready(email_cfg(enabled=False))
        self.assertFalse(ready)
        self.assertIn("off", reason)
        ready, reason = mailer.email_ready({"email": {"enabled": True, "host": "x", "from": "", "to": ""}})
        self.assertFalse(ready)
        self.assertIn("from", reason)

    def test_send_starttls_logs_in_and_sets_headers(self):
        with mock.patch("poppy.mailer.smtplib.SMTP", FakeSMTP):
            mailer.send(self.cfg, "Subject here", "Body here")
        server = FakeSMTP.instances[-1]
        self.assertEqual((server.host, server.port), ("smtp.example.com", 587))
        self.assertTrue(server.started)
        self.assertEqual(server.logged, ("poppy", "secret"))
        message = server.messages[0]
        self.assertEqual(message["Subject"], "Subject here")
        self.assertEqual(message["From"], "poppy@example.com")
        self.assertEqual(message["To"], "daniel@example.com")
        self.assertIn("Body here", message.get_content())

    def test_send_ssl_skips_starttls_and_uses_its_port(self):
        with mock.patch("poppy.mailer.smtplib.SMTP", FakeSMTP), mock.patch(
            "poppy.mailer.smtplib.SMTP_SSL", FakeSMTP
        ):
            mailer.send(email_cfg(security="ssl"), "s", "b")
        self.assertEqual(FakeSMTP.instances[-1].port, 465)
        self.assertFalse(FakeSMTP.instances[-1].started)

    def test_long_links_survive_the_transfer_encoding(self):
        # SMTP may quoted-printable the body (soft line breaks); the decoded
        # message must still carry the link byte-for-byte
        import email.parser

        url = "http://10.0.0.10:8788/decide?token=" + "a" * 80 + ".accept.1791504184." + "b" * 32
        with mock.patch("poppy.mailer.smtplib.SMTP", FakeSMTP):
            mailer.send(self.cfg, "Subject", f"Accept: {url}\n")
        raw = FakeSMTP.instances[-1].messages[0].as_bytes()
        parsed = email.parser.BytesParser().parsebytes(raw)
        self.assertIn(url, parsed.get_payload(decode=True).decode("utf-8"))

    def test_send_reports_provider_errors(self):
        class Broken(FakeSMTP):
            def send_message(self, message):
                import smtplib

                raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

        with mock.patch("poppy.mailer.smtplib.SMTP", Broken):
            with self.assertRaises(PoppyError) as caught:
                mailer.send(self.cfg, "s", "b")
        self.assertIn("bad credentials", str(caught.exception))

    def test_digest_has_links_and_the_evidence_quote(self):
        save_candidate(self.home, CANDIDATE)
        subject, body = mailer.candidate_digest(self.home, self.cfg, [CANDIDATE], "mining run")
        self.assertIn("1 new candidate", subject)
        self.assertIn("Prefers minimal dependencies", body)
        self.assertIn("I prefer no dependencies.", body)
        self.assertIn("http://10.0.0.10:8788/decide?token=", body)
        self.assertIn("mining run", body)

    def test_notify_candidates_statuses(self):
        save_candidate(self.home, CANDIDATE)
        with mock.patch("poppy.mailer.smtplib.SMTP", FakeSMTP):
            self.assertEqual(mailer.notify_candidates(self.home, self.cfg, ["cand1"], "mining run"), "sent")
        self.assertEqual(mailer.notify_candidates(self.home, email_cfg(enabled=False), ["cand1"], "x"), "off")
        with mock.patch("poppy.mailer.send", side_effect=PoppyError("smtp is down")):
            status = mailer.notify_candidates(self.home, self.cfg, ["cand1"], "x")
        self.assertTrue(status.startswith("failed"))
        self.assertIn("smtp is down", status)


class TestEmailCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        ensure_home_layout(self.home)
        self.env = mock.patch.dict(os.environ, {"POPPY_HOME": str(self.home)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_set_show_and_enable(self):
        code = cli.main(
            [
                "email", "set",
                "--host", "smtp.example.com",
                "--from", "poppy@example.com",
                "--user", "poppy",
                "--password", "app-password",
                "--to", "daniel@example.com",
                "--base-url", "http://10.0.0.10:8788",
                "--enable",
            ]
        )
        self.assertEqual(code, 0)
        cfg = load_config(self.home)
        self.assertTrue(cfg["email"]["enabled"])
        self.assertEqual(cfg["email"]["host"], "smtp.example.com")
        self.assertEqual(cfg["email"]["base_url"], "http://10.0.0.10:8788")
        self.assertEqual(mailer.email_ready(cfg), (True, ""))
        # show never prints the password
        with mock.patch("sys.stdout") as stdout:
            self.assertEqual(cli.main(["email", "show", "--json"]), 0)
        printed = "".join(str(call.args[0]) for call in stdout.write.call_args_list if call.args)
        payload = json.loads(printed)
        self.assertNotIn("app-password", printed)
        self.assertTrue(payload["password_set"])

    def test_enabling_without_a_host_is_refused(self):
        code = cli.main(["email", "set", "--enable"])
        self.assertEqual(code, 1)
        self.assertFalse(load_config(self.home)["email"]["enabled"])

    def test_test_command_reports_smtp_errors(self):
        cli.main(["email", "set", "--host", "smtp.example.com", "--from", "poppy@example.com", "--enable"])
        with mock.patch("poppy.mailer.send", side_effect=PoppyError("sending mail failed: connection refused")):
            self.assertEqual(cli.main(["email", "test"]), 1)
        with mock.patch("poppy.mailer.send") as send:
            self.assertEqual(cli.main(["email", "test", "--json"]), 0)
        self.assertEqual(send.call_args.args[0]["email"]["to"], "poppy@example.com")


if __name__ == "__main__":
    unittest.main()
