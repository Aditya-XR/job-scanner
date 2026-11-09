"""Gmail summary notifications and SMTP error handling."""
import logging
import smtplib

import pytest

import jobscan.notify as notify_mod
from jobscan.core import Job


class FakeGmailSMTP(smtplib.SMTP):
    """Simulates Gmail's SMTP server: advertises AUTH LOGIN PLAIN, returns 535 on bad
    credentials and immediately closes the connection (so a second AUTH raises
    SMTPServerDisconnected, just like real smtp.gmail.com)."""

    def __init__(self, accept=True, disconnect_on_login=False):
        self.accept = accept
        self.disconnect_on_login = disconnect_on_login
        self.closed_after_535 = False
        self.sent = []
        self.esmtp_features = {"auth": "LOGIN PLAIN XOAUTH2"}
        self.does_esmtp = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def ehlo_or_helo_if_needed(self):
        pass

    def docmd(self, cmd, args=""):
        if self.disconnect_on_login or self.closed_after_535:
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        if cmd == "AUTH":
            if self.accept:
                return 235, b"2.7.0 Accepted"
            self.closed_after_535 = True
            return 535, b"5.7.8 Username and Password not accepted.\n5.7.8  https://support.google.com/mail/?p=BadCredentials"
        raise AssertionError(f"Unexpected SMTP command: {cmd} {args}")

    def sendmail(self, from_addr, to_addrs, msg):
        self.sent.append((from_addr, to_addrs, msg))


def _set_gmail_env(monkeypatch, addr="user@gmail.com", pw="abcd efgh ijkl mnop"):
    monkeypatch.setenv("GMAIL_ADDRESS", addr)
    monkeypatch.setenv("GMAIL_APP_PASSWORD", pw)
    monkeypatch.delenv("ALERT_TO", raising=False)


def test_smtp_login_surfaces_535_instead_of_disconnected():
    # Standard smtplib.SMTP.login() tries PLAIN (gets 535, server closes socket) then
    # retries LOGIN on the closed socket, hiding the 535 behind SMTPServerDisconnected.
    s_std = FakeGmailSMTP(accept=False)
    with pytest.raises(smtplib.SMTPServerDisconnected):
        s_std.login("user@gmail.com", "badpassword12345")

    # smtp_login() uses AUTH PLAIN directly and surfaces the real 535 SMTPAuthenticationError.
    s_direct = FakeGmailSMTP(accept=False)
    with pytest.raises(smtplib.SMTPAuthenticationError) as exc:
        notify_mod.smtp_login(s_direct, "user@gmail.com", "badpassword12345")
    assert exc.value.smtp_code == 535


def test_send_summary_sends_email_on_valid_login(monkeypatch, fixed_today):
    import email
    _set_gmail_env(monkeypatch)
    fake = FakeGmailSMTP(accept=True)
    monkeypatch.setattr(notify_mod.smtplib, "SMTP_SSL", lambda *a, **kw: fake)

    job = Job(company="Stripe", title="Software Engineer", location="Bengaluru",
              url="https://stripe.com/jobs/1", job_id="gh:1", source="Greenhouse",
              experience="0+ yrs")
    assert notify_mod.send_summary("cloud", [job], "https://sheet.example/1", set()) is True
    assert len(fake.sent) == 1
    assert fake.sent[0][0] == "user@gmail.com"
    body = email.message_from_string(fake.sent[0][2]).get_payload(decode=True).decode("utf-8")
    assert "Stripe" in body


def test_send_summary_logs_clear_warning_on_bad_app_password(monkeypatch, caplog, fixed_today):
    _set_gmail_env(monkeypatch)
    monkeypatch.setattr(notify_mod.smtplib, "SMTP_SSL", lambda *a, **kw: FakeGmailSMTP(accept=False))

    with caplog.at_level(logging.WARNING, logger="jobscan"):
        sent = notify_mod.send_summary("cloud", [], "https://sheet.example/1", set())

    assert sent is False
    assert "535" in caplog.text
    assert "regenerate GMAIL_APP_PASSWORD" in caplog.text


def test_send_summary_handles_unexpected_disconnect_without_crashing(monkeypatch, caplog, fixed_today):
    _set_gmail_env(monkeypatch)
    monkeypatch.setattr(notify_mod.smtplib, "SMTP_SSL",
                        lambda *a, **kw: FakeGmailSMTP(disconnect_on_login=True))

    with caplog.at_level(logging.WARNING, logger="jobscan"):
        sent = notify_mod.send_summary("cloud", [], "https://sheet.example/1", set())

    assert sent is False
    assert "SMTPServerDisconnected" in caplog.text
