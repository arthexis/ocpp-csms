from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocpp_csms.mail import load_mail_config, mail_status, send_test
from ocpp_csms.cli import build_parser


CONFIG = """
[mail]
enabled = true
from = "csms@example.com"
to = ["admin@example.com"]

[mail.smtp]
host = "smtp.example.com"
port = 587
starttls = true
username = "csms@example.com"
password_env = "TEST_MAIL_SECRET"

[mail.events.transaction_started]
enabled = false
"""


class MailTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "mail.toml"
        self.path.write_text(CONFIG)

    def test_config_and_redacted_status(self):
        config = load_mail_config(self.path)
        with patch.dict(os.environ, {"TEST_MAIL_SECRET": "secret"}):
            status = mail_status(config)
        self.assertTrue(status["credentials_available"])
        self.assertNotIn("secret", repr(status))
        self.assertEqual(status["smtp_host"], "smtp.example.com")

    def test_absent_config_disabled(self):
        self.assertIsNone(load_mail_config(self.path.with_name("missing.toml")))

    def test_starttls_is_required(self):
        self.path.write_text(CONFIG.replace("starttls = true", "starttls = false"))
        with self.assertRaisesRegex(ValueError, "requires TLS"):
            load_mail_config(self.path)

    def test_missing_credentials_prevents_sending(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "credential"):
                send_test(load_mail_config(self.path))

    def test_test_message_uses_tls_and_auth(self):
        config = load_mail_config(self.path)
        with patch.dict(os.environ, {"TEST_MAIL_SECRET": "secret"}):
            with patch("ocpp_csms.mail.smtplib.SMTP") as smtp_factory:
                send_test(config)
        smtp = smtp_factory.return_value.__enter__.return_value
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("csms@example.com", "secret")
        smtp.send_message.assert_called_once()
        self.assertEqual(smtp.send_message.call_args.args[0]["Subject"], "OCPP-CSMS SMTP test")

    def test_cli_parses_mail(self):
        parser, _ = build_parser()
        args = parser.parse_args(["mail", "--config", str(self.path), "status", "--json"])
        self.assertEqual(args.mail_command, "status")
        self.assertTrue(args.json)


if __name__ == "__main__":
    unittest.main()
