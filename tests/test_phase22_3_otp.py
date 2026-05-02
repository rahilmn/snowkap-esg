"""Phase 22.3 — OTP magic-link auth.

Two-step login: `issue()` writes a 6-digit code to `auth_otp` and
sends an email; `verify()` consumes it and returns the JWT-ready
signal. Codes expire after 10 minutes and burn after 5 wrong tries.
"""

from __future__ import annotations

import time

import pytest

from api import auth_otp


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path):
    db = tmp_path / "test_otp.db"
    monkeypatch.setenv("SNOWKAP_DB_PATH", str(db))
    yield


def test_issue_returns_six_digit_code():
    code, exp = auth_otp.issue("user@example.com")
    assert len(code) == 6
    assert code.isdigit()
    assert exp > time.time()


def test_verify_happy_path_consumes_code():
    code, _ = auth_otp.issue("user@example.com")
    ok, err = auth_otp.verify("user@example.com", code)
    assert ok
    assert err is None
    # Replay must fail — code was burned
    ok2, err2 = auth_otp.verify("user@example.com", code)
    assert not ok2
    assert err2 is not None


def test_verify_wrong_code_increments_attempts():
    code, _ = auth_otp.issue("user@example.com")
    for _ in range(4):
        ok, _ = auth_otp.verify("user@example.com", "000000")
        assert not ok
    # The correct code should still work on the 5th try
    ok, err = auth_otp.verify("user@example.com", code)
    assert ok, f"correct code should still work: {err}"


def test_verify_burns_after_max_attempts():
    auth_otp.issue("user@example.com")
    for _ in range(auth_otp.MAX_ATTEMPTS):
        ok, _ = auth_otp.verify("user@example.com", "000000")
        assert not ok
    # Even the correct code is gone now
    ok, err = auth_otp.verify("user@example.com", "000000")
    assert not ok
    assert err is not None
    assert "request" in err.lower()


def test_verify_rejects_malformed():
    auth_otp.issue("user@example.com")
    ok, err = auth_otp.verify("user@example.com", "abc")
    assert not ok
    assert err is not None and "6 digits" in err


def test_verify_expired_code():
    code, _ = auth_otp.issue("user@example.com")
    # Mutate expires_at directly to simulate passage of time
    import sqlite3
    from pathlib import Path
    import os
    with sqlite3.connect(os.environ["SNOWKAP_DB_PATH"]) as c:
        c.execute("UPDATE auth_otp SET expires_at = ?", (time.time() - 1,))
    ok, err = auth_otp.verify("user@example.com", code)
    assert not ok
    assert "expired" in (err or "").lower()


def test_re_issue_resets_attempts():
    auth_otp.issue("user@example.com")
    for _ in range(3):
        auth_otp.verify("user@example.com", "000000")
    new_code, _ = auth_otp.issue("user@example.com")
    ok, err = auth_otp.verify("user@example.com", new_code)
    assert ok, err


def test_render_otp_email_contains_code():
    subject, html = auth_otp.render_otp_email("123456", name="Alice")
    assert "123456" in subject
    assert "123456" in html
    assert "Alice" in html


def test_is_email_otp_enabled_reflects_resend(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert not auth_otp.is_email_otp_enabled()
    monkeypatch.setenv("RESEND_API_KEY", "re_test_xxx")
    assert auth_otp.is_email_otp_enabled()
