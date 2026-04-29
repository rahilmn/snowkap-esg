"""Phase 13 — Demo-readiness resilience tests.

Covers Day 1 blockers that protect a live demo from cosmetic 5xx failures:

  B4 — `GET /api/insights/{id}` must return 202 (regenerating) when the
       indexed JSON file is missing or malformed, NOT a raw 500. Found
       during the ET/Mint demo-readiness audit.

  B3 — `engine/output/email_sender.send_email` must classify Resend
       transient errors (rate-limit, connection timeout) into a distinct
       error code so the share-flow UI can render an actionable retry
       message instead of an opaque "send failed".

  B2 — On-demand pipeline crash reporting (status-poll endpoint).
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Test plumbing — JWT-signed admin token (matches Phase 11 pattern)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env_setup():
    """Set required env so the API boots in dev mode + JWT works."""
    keys = {
        "JWT_SECRET": "test-secret-xxxxxxxxxxxxxxxxxxxxxx",
        "SNOWKAP_API_KEY": "test-api-key",
        # Don't set SNOWKAP_ENV=production — keep dev-mode permissive auth
    }
    with patch.dict(os.environ, keys, clear=False):
        yield


def _api_headers() -> dict:
    return {"X-API-Key": "test-api-key"}


# ---------------------------------------------------------------------------
# B4 — JSON deserialization fallback to 202 regenerating
# ---------------------------------------------------------------------------


def test_insight_returns_202_regenerating_when_file_missing(tmp_path) -> None:
    """If the indexed JSON path no longer exists on disk, the endpoint
    must return HTTP 202 with a `regenerating` state instead of 500."""
    from api.main import app

    fake_row = {
        "id": "missing-article-1",
        "company_slug": "adani-power",
        "json_path": str(tmp_path / "does-not-exist.json"),
        "title": "test",
        "tier": "HOME",
    }
    with patch("api.routes.insights.get_by_id", return_value=fake_row), \
         patch("api.routes.insights._trigger_background_regenerate") as mock_regen:
        with TestClient(app) as client:
            r = client.get("/api/insights/missing-article-1", headers=_api_headers())
    assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["state"] == "regenerating"
    assert body["reason"] == "file_missing_on_disk"
    assert body["retry_after_seconds"] == 30
    assert r.headers.get("Retry-After") == "30"
    mock_regen.assert_called_once()


def test_insight_returns_202_when_file_is_malformed_json(tmp_path) -> None:
    """If the indexed JSON file exists but is truncated/malformed, the
    endpoint must return HTTP 202 + queue a regenerate, NOT 500."""
    from api.main import app

    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{\"headline\": \"truncated mid-write...", encoding="utf-8")

    fake_row = {
        "id": "malformed-1",
        "company_slug": "jsw-energy",
        "json_path": str(bad_file),
        "title": "test",
        "tier": "HOME",
    }
    with patch("api.routes.insights.get_by_id", return_value=fake_row), \
         patch("api.routes.insights._trigger_background_regenerate") as mock_regen:
        with TestClient(app) as client:
            r = client.get("/api/insights/malformed-1", headers=_api_headers())
    assert r.status_code == 202
    assert r.json()["reason"] == "malformed_json"
    mock_regen.assert_called_once()


def test_insight_returns_404_when_index_row_absent() -> None:
    """If the article isn't in the index at all, that's a real 404 — not
    a regenerate candidate."""
    from api.main import app

    with patch("api.routes.insights.get_by_id", return_value=None):
        with TestClient(app) as client:
            r = client.get("/api/insights/never-existed", headers=_api_headers())
    assert r.status_code == 404


def test_insight_serves_payload_when_file_is_valid(tmp_path) -> None:
    """Happy path: well-formed JSON file is served as-is at 200."""
    from api.main import app

    good_file = tmp_path / "good.json"
    good_file.write_text(
        json.dumps({"article": {"title": "x"}, "insight": {"headline": "y"}}),
        encoding="utf-8",
    )
    fake_row = {
        "id": "good-1",
        "company_slug": "adani-power",
        "json_path": str(good_file),
        "title": "test",
        "tier": "HOME",
    }
    with patch("api.routes.insights.get_by_id", return_value=fake_row):
        with TestClient(app) as client:
            r = client.get("/api/insights/good-1", headers=_api_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["payload"]["insight"]["headline"] == "y"


# ---------------------------------------------------------------------------
# B3 — Resend error taxonomy (rate-limit, timeout) surfaced via SendResult
# ---------------------------------------------------------------------------


def test_resend_rate_limit_returns_retryable_error_class() -> None:
    """When Resend raises a rate-limit error, the SendResult must classify
    it as `error_class='rate_limit'` so the UI can render a retry banner."""
    from engine.output import email_sender

    fake_resend = MagicMock()

    class FakeRateLimitError(Exception):
        pass

    fake_resend.RateLimitError = FakeRateLimitError
    fake_resend.Emails.send.side_effect = FakeRateLimitError("429 Too Many Requests")

    with patch.dict(os.environ, {"RESEND_API_KEY": "re_test"}):
        with patch.dict("sys.modules", {"resend": fake_resend}):
            res = email_sender.send_email(
                to="dev@example.com",
                subject="x",
                html_body="<p>x</p>",
            )
    assert res.status == "failed"
    assert res.error_class == "rate_limit"
    assert "rate" in res.error.lower() or "429" in res.error


def test_resend_timeout_returns_distinct_error_class() -> None:
    """A connection timeout must surface as `error_class='timeout'`, not
    swallowed in a generic 'send failed'."""
    from engine.output import email_sender

    fake_resend = MagicMock()

    class FakeAPIConnectionError(Exception):
        pass

    fake_resend.APIConnectionError = FakeAPIConnectionError
    fake_resend.Emails.send.side_effect = FakeAPIConnectionError("connection reset")

    with patch.dict(os.environ, {"RESEND_API_KEY": "re_test"}):
        with patch.dict("sys.modules", {"resend": fake_resend}):
            res = email_sender.send_email(
                to="dev@example.com",
                subject="x",
                html_body="<p>x</p>",
            )
    assert res.status == "failed"
    assert res.error_class == "timeout"


def test_resend_generic_error_falls_back_to_unknown_class() -> None:
    """Any unrecognised Resend exception lands in `error_class='unknown'`
    — preserves backwards compatibility for callers that don't switch on
    error_class yet."""
    from engine.output import email_sender

    fake_resend = MagicMock()

    fake_resend.RateLimitError = type("RateLimitError", (Exception,), {})
    fake_resend.APIConnectionError = type("APIConnectionError", (Exception,), {})
    fake_resend.Emails.send.side_effect = ValueError("malformed payload")

    with patch.dict(os.environ, {"RESEND_API_KEY": "re_test"}):
        with patch.dict("sys.modules", {"resend": fake_resend}):
            res = email_sender.send_email(
                to="dev@example.com",
                subject="x",
                html_body="<p>x</p>",
            )
    assert res.status == "failed"
    assert res.error_class == "unknown"


def test_resend_success_path_carries_error_class_none() -> None:
    """Sent email has no error → error_class is empty/None."""
    from engine.output import email_sender

    fake_resend = MagicMock()
    fake_resend.RateLimitError = type("RateLimitError", (Exception,), {})
    fake_resend.APIConnectionError = type("APIConnectionError", (Exception,), {})
    fake_resend.Emails.send.return_value = {"id": "msg_123"}

    with patch.dict(os.environ, {"RESEND_API_KEY": "re_test"}):
        with patch.dict("sys.modules", {"resend": fake_resend}):
            res = email_sender.send_email(
                to="dev@example.com",
                subject="x",
                html_body="<p>x</p>",
            )
    assert res.status == "sent"
    assert res.error_class == ""
    assert res.provider_id == "msg_123"


# ---------------------------------------------------------------------------
# B2 — On-demand pipeline status-poll endpoint
# ---------------------------------------------------------------------------


def test_analysis_status_endpoint_returns_unknown_when_no_job() -> None:
    """No tracked job for this article → returns state=unknown (200), NOT 404.
    The UI uses this to distinguish "never started" from "in progress"."""
    from api.main import app

    with TestClient(app) as client:
        r = client.get(
            f"/api/news/never-tracked-{os.urandom(4).hex()}/analysis-status",
            headers=_api_headers(),
        )
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "unknown"
    assert body["error_class"] is None


def test_analysis_status_returns_running_state_after_mark_running() -> None:
    """After we mark a job pending → running, the endpoint must reflect it."""
    from api.main import app
    from engine.models import article_analysis_status

    aid = f"test-running-{os.urandom(4).hex()}"
    article_analysis_status.mark_pending(aid, "adani-power")
    article_analysis_status.mark_running(aid)

    with TestClient(app) as client:
        r = client.get(f"/api/news/{aid}/analysis-status", headers=_api_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "running"
    assert body["error"] is None


def test_analysis_status_returns_failed_with_classification() -> None:
    """When the bg job fails, the status endpoint surfaces the
    error_class + retry_after_seconds for transient failures."""
    from api.main import app
    from engine.models import article_analysis_status

    aid = f"test-failed-{os.urandom(4).hex()}"
    article_analysis_status.mark_pending(aid, "jsw-energy")
    article_analysis_status.mark_failed(
        aid, error_class="openai_rate_limit",
        error="429 Too Many Requests from OpenAI",
    )

    with TestClient(app) as client:
        r = client.get(f"/api/news/{aid}/analysis-status", headers=_api_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "failed"
    assert body["error_class"] == "openai_rate_limit"
    assert body["retry_after_seconds"] == 30
    assert "429" in body["error"]


def test_analysis_status_classification_helper() -> None:
    """The classify_pipeline_error helper maps common Python errors to
    actionable error classes."""
    from engine.models.article_analysis_status import classify_pipeline_error

    class FakeRateLimit(Exception):
        pass

    assert classify_pipeline_error(FakeRateLimit("429 too many requests")) == "openai_rate_limit"

    class TimeoutError(Exception):
        pass

    assert classify_pipeline_error(TimeoutError("connection timeout")) == "openai_timeout"
    assert classify_pipeline_error(FileNotFoundError("no such file")) == "article_not_found"
    assert classify_pipeline_error(KeyError("company icici-bank not found")) == "company_not_found"
    assert classify_pipeline_error(ValueError("random crash")) == "pipeline_crash"


def test_email_config_status_returns_disabled_when_key_missing() -> None:
    """B7: with no RESEND_API_KEY, the endpoint reports enabled=False
    + a human-readable reason."""
    from api.main import app

    with patch.dict(os.environ, {
        "JWT_SECRET": "test-secret-xxxxxxxxxxxxxxxxxxxxxx",
        "SNOWKAP_API_KEY": "test-api-key",
    }, clear=True):
        with TestClient(app) as client:
            r = client.get("/api/admin/email-config-status", headers=_api_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert "RESEND_API_KEY" in body["reason"]


def test_email_config_status_returns_enabled_when_configured() -> None:
    """B7: with both RESEND_API_KEY + SNOWKAP_FROM_ADDRESS set, enabled=True."""
    from api.main import app

    with patch.dict(os.environ, {
        "JWT_SECRET": "test-secret-xxxxxxxxxxxxxxxxxxxxxx",
        "SNOWKAP_API_KEY": "test-api-key",
        "RESEND_API_KEY": "re_real_test_key_12345",
        "SNOWKAP_FROM_ADDRESS": "Snowkap ESG <newsletter@snowkap.co.in>",
    }, clear=False):
        with TestClient(app) as client:
            r = client.get("/api/admin/email-config-status", headers=_api_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["sender"]
    assert "@" in body["sender"]
    assert "reason" not in body  # only set when disabled


def test_active_signals_count_uses_home_tier_high_impact_query() -> None:
    """B8: count_active_signals must filter by HOME tier + CRITICAL/HIGH
    materiality (or relevance >= 7) within the last 7 days. Tests the
    SQL filter logic without requiring real article data."""
    from engine.index import sqlite_index

    # The function should accept (company_slug, days) and return an int.
    n = sqlite_index.count_active_signals()
    assert isinstance(n, int)
    assert n >= 0

    n_company = sqlite_index.count_active_signals(company_slug="adani-power")
    assert isinstance(n_company, int)
    assert n_company >= 0

    # Custom days window (recent 30) must not error
    n_30 = sqlite_index.count_active_signals(days=30)
    assert isinstance(n_30, int)
    assert n_30 >= n  # 30-day window must be ≥ 7-day window


def test_news_stats_endpoint_emits_active_signals_count() -> None:
    """B8: GET /api/news/stats returns active_signals_count + back-compat
    predictions_count (= same value)."""
    from api.main import app

    with TestClient(app) as client:
        r = client.get("/api/news/stats", headers=_api_headers())
    assert r.status_code == 200
    body = r.json()
    assert "active_signals_count" in body
    assert "predictions_count" in body
    assert body["active_signals_count"] == body["predictions_count"]
    assert isinstance(body["active_signals_count"], int)


def test_email_config_status_rejects_placeholder_key() -> None:
    """B7: a placeholder like 'your_resend_key_here' must report disabled."""
    from api.main import app

    with patch.dict(os.environ, {
        "JWT_SECRET": "test-secret-xxxxxxxxxxxxxxxxxxxxxx",
        "SNOWKAP_API_KEY": "test-api-key",
        "RESEND_API_KEY": "your_resend_key_here",
        "SNOWKAP_FROM_ADDRESS": "test@example.com",
    }, clear=False):
        with TestClient(app) as client:
            r = client.get("/api/admin/email-config-status", headers=_api_headers())
    body = r.json()
    assert body["enabled"] is False
    assert "placeholder" in body["reason"].lower()


def test_analysis_status_marked_ready_when_completes() -> None:
    """After mark_ready, state flips to ready + elapsed_seconds > 0."""
    from engine.models import article_analysis_status
    import time

    aid = f"test-ready-{os.urandom(4).hex()}"
    t0 = time.perf_counter()
    article_analysis_status.mark_pending(aid, "icici-bank")
    article_analysis_status.mark_running(aid)
    time.sleep(0.05)
    article_analysis_status.mark_ready(aid, t0)
    status = article_analysis_status.get_status(aid)
    assert status is not None
    assert status.state == "ready"
    assert status.elapsed_seconds > 0
    assert status.error is None
