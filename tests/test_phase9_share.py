"""Phase 9 tests: name-from-email + share_service + mocked Resend."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from engine.output.email_sender import (
    is_valid_email,
    name_from_email,
    send_email,
)
from engine.output.newsletter_renderer import NewsletterArticle
from engine.output.share_service import share_article_by_email


# ---------------------------------------------------------------------------
# name_from_email — the one most subject to edge cases
# ---------------------------------------------------------------------------


def test_name_from_email_dot_separator():
    assert name_from_email("ambalika.mehrotra@mintedit.com") == "Ambalika"


def test_name_from_email_underscore_separator():
    assert name_from_email("john_smith@company.com") == "John"


def test_name_from_email_hyphen_separator():
    assert name_from_email("first-name@x.com") == "First"


def test_name_from_email_single_word_gets_capitalized():
    assert name_from_email("priya@startup.in") == "Priya"


def test_name_from_email_strips_plus_tag():
    assert name_from_email("priya+weeklydrip@startup.in") == "Priya"


def test_name_from_email_initial_prefix_returns_none():
    """'f.lastname' → initial + surname → can't reliably greet."""
    assert name_from_email("f.lastname@x.com") is None


def test_name_from_email_single_letter_returns_none():
    assert name_from_email("a@x.com") is None


def test_name_from_email_generic_mailbox_returns_none():
    for local in ["info", "contact", "hello", "sales", "support", "noreply"]:
        assert name_from_email(f"{local}@company.com") is None, f"should reject {local}"


def test_name_from_email_numeric_heavy_returns_none():
    """'user12345' is probably an auto-generated alias; don't greet by name."""
    assert name_from_email("user12345@x.com") is None


def test_name_from_email_invalid_returns_none():
    assert name_from_email("notanemail") is None
    assert name_from_email("") is None
    assert name_from_email(None) is None  # type: ignore[arg-type]


def test_name_from_email_filters_blocked_tokens():
    """'editor.desk@mint.com' → both tokens blocked → None."""
    assert name_from_email("editor.desk@mint.com") is None


def test_name_from_email_mixed_case_normalises():
    assert name_from_email("AMBALIKA@x.com") == "Ambalika"
    assert name_from_email("Ambalika.Mehrotra@x.com") == "Ambalika"


def test_is_valid_email():
    assert is_valid_email("x@y.com") is True
    assert is_valid_email("x.y+tag@sub.domain.co.in") is True
    assert is_valid_email("invalid") is False
    assert is_valid_email("@domain.com") is False
    assert is_valid_email("") is False


# ---------------------------------------------------------------------------
# send_email — mocked Resend
# ---------------------------------------------------------------------------


def test_send_email_dry_run_returns_preview():
    r = send_email(
        to="test@example.com",
        subject="Test",
        html_body="<p>test</p>",
        dry_run=True,
    )
    assert r.status == "preview"
    assert r.recipient == "test@example.com"


def test_send_email_invalid_recipient_fails():
    r = send_email(to="not-an-email", subject="Test", html_body="<p>test</p>")
    assert r.status == "failed"
    assert "invalid recipient" in r.error


def test_send_email_no_key_returns_preview():
    """Missing RESEND_API_KEY → preview (not failure) so CI/dev stays green."""
    with patch.dict("os.environ", {"RESEND_API_KEY": ""}, clear=False):
        r = send_email(to="test@example.com", subject="Test", html_body="<p>test</p>")
    assert r.status == "preview"
    assert "RESEND_API_KEY missing" in r.error


def test_send_email_with_mocked_resend_success():
    fake_resp = {"id": "email_abc123"}
    mock_resend = MagicMock()
    mock_resend.api_key = None
    mock_resend.Emails.send.return_value = fake_resp

    with patch.dict("os.environ", {"RESEND_API_KEY": "re_testkey"}, clear=False):
        with patch.dict("sys.modules", {"resend": mock_resend}):
            r = send_email(
                to="test@example.com",
                subject="S",
                html_body="<p>h</p>",
                from_address="Snowkap <x@snowkap.com>",
            )

    assert r.status == "sent"
    assert r.provider_id == "email_abc123"
    # Resend was called with the expected payload
    mock_resend.Emails.send.assert_called_once()
    call_payload = mock_resend.Emails.send.call_args[0][0]
    assert call_payload["to"] == "test@example.com"
    assert call_payload["html"] == "<p>h</p>"
    assert call_payload["from"] == "Snowkap <x@snowkap.com>"


def test_send_email_resend_error_returns_failed():
    mock_resend = MagicMock()
    mock_resend.Emails.send.side_effect = RuntimeError("API blew up")

    with patch.dict("os.environ", {"RESEND_API_KEY": "re_testkey"}, clear=False):
        with patch.dict("sys.modules", {"resend": mock_resend}):
            r = send_email(to="test@example.com", subject="S", html_body="<p>h</p>")

    assert r.status == "failed"
    assert "API blew up" in r.error


# ---------------------------------------------------------------------------
# share_article_by_email — end-to-end with mocked outputs + Resend
# ---------------------------------------------------------------------------


def test_share_rejects_invalid_email():
    result = share_article_by_email(
        article_id="abc",
        company_slug="test",
        recipient_email="not-an-email",
    )
    assert result.status == "failed"
    assert "invalid recipient" in result.error


def test_share_rejects_unknown_article():
    """When the article_id isn't in any company's outputs, return failed."""
    with tempfile.TemporaryDirectory() as tmp:
        outputs = Path(tmp)
        # Create a company dir with no matching insight
        (outputs / "adani-power" / "insights").mkdir(parents=True)

        result = share_article_by_email(
            article_id="nonexistent_id",
            company_slug="adani-power",
            recipient_email="test@example.com",
            outputs_root=outputs,
        )
    assert result.status == "failed"
    assert "no HOME-tier analysis found" in result.error


def test_share_end_to_end_preview_mode():
    """Dry-run through outputs → renders → returns preview SendResult."""
    with tempfile.TemporaryDirectory() as tmp:
        outputs = Path(tmp)
        insights_dir = outputs / "adani-power" / "insights"
        insights_dir.mkdir(parents=True)
        ceo_dir = outputs / "adani-power" / "perspectives" / "ceo"
        ceo_dir.mkdir(parents=True)

        article_id = "test_article_xyz"
        insight_payload = {
            "article": {
                "id": article_id,
                "title": "Test article headline",
                "url": "https://example.com/article",
                "source": "Mint",
                "published_at": "2026-04-22T10:00:00+00:00",
            },
            "insight": {
                "headline": "Test headline for ESG event",
                "decision_summary": {
                    "materiality": "HIGH",
                    "key_risk": "₹100 Cr penalty exposure from non-disclosure",
                    "top_opportunity": "Commission an audit",
                },
                "net_impact_summary": "Net impact summary",
            },
        }
        (insights_dir / f"2026-04-22_{article_id}.json").write_text(
            json.dumps(insight_payload), encoding="utf-8",
        )
        (ceo_dir / f"2026-04-22_{article_id}.json").write_text(
            json.dumps({
                "board_paragraph": "Board should approve remediation within 4 weeks.",
            }),
            encoding="utf-8",
        )

        # Mock companies.json lookup
        import engine.config as cfg
        # Force load_companies to fail gracefully → fallback to slug title
        with patch.object(cfg.load_companies, "cache_clear", lambda: None):
            with patch("engine.config.load_companies", side_effect=Exception("no")):
                result = share_article_by_email(
                    article_id=article_id,
                    company_slug="adani-power",
                    recipient_email="ambalika.m@mintedit.com",
                    outputs_root=outputs,
                    dry_run=True,
                )

    assert result.status == "preview"
    assert result.recipient == "ambalika.m@mintedit.com"
    assert result.recipient_name == "Ambalika"
    assert result.article_id == article_id
    assert result.company_slug == "adani-power"
    assert "Test article headline" in result.subject or "Adani Power" in result.subject
    assert result.html_length > 500


def test_share_subject_line_format():
    """Subject includes 'Snowkap ESG · <company> · <headline>' and is ≤ 90 chars."""
    from engine.output.share_service import _build_subject
    subj = _build_subject("Adani Power", "SEBI imposes ₹275 Cr penalty on Adani Power for non-disclosure")
    assert "Snowkap ESG" in subj
    assert "Adani Power" in subj
    assert len(subj) <= 90


def test_share_subject_truncates_long_headline():
    from engine.output.share_service import _build_subject
    long = "A very long article headline that probably exceeds reasonable subject-line length limits for email clients"
    subj = _build_subject("Adani Power", long)
    assert len(subj) <= 90
    assert subj.endswith("…")
