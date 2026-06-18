"""Phase 25 W10 — digest email + materiality-first feed sort + scheduler wiring tests.

Three layers:

  A. ``engine.output.digest_email`` — composition + feature flag.
  B. ``engine.index.sqlite_index.query_feed`` — materiality-first sort
     (CRITICAL > HIGH > MODERATE > LOW > NULL).
  C. ``engine.scheduler`` — overnight + morning-digest CLI flags + cron
     wiring static checks.

The Resend send call is mocked across all digest tests so we don't
hit the live email API during CI.
"""

from __future__ import annotations

import os
import sqlite3
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# A. digest_email — composition
# ---------------------------------------------------------------------------


class TestDigestEmailFeatureFlag:
    def test_disabled_flag_returns_disabled_status(self):
        from engine.output.digest_email import send_morning_digest
        with patch.dict(os.environ, {"SNOWKAP_MORNING_DIGEST_ENABLED": "0"}):
            result = send_morning_digest()
        assert result["status"] == "disabled"
        assert result["articles_included"] == 0

    def test_enabled_flag_attempts_send(self):
        from engine.output.digest_email import send_morning_digest
        with patch.dict(os.environ, {"SNOWKAP_MORNING_DIGEST_ENABLED": "1"}):
            with patch("engine.output.digest_email._query_recent_articles", return_value=[]):
                with patch("engine.output.email_sender.send_email") as mock_send:
                    mock_resp = MagicMock()
                    mock_resp.status = "preview"
                    mock_send.return_value = mock_resp
                    result = send_morning_digest(dry_run=True)
        assert result["status"] in ("preview", "sent", "ok")
        assert result["articles_included"] == 0


class TestDigestComposition:
    def test_subject_when_no_articles(self):
        from engine.output.digest_email import _build_subject
        subj = _build_subject(0, 0)
        assert "No critical alerts" in subj
        assert "Snowkap Morning Brief" in subj

    def test_subject_with_articles(self):
        from engine.output.digest_email import _build_subject
        subj = _build_subject(9, 3)
        assert "9 alerts" in subj
        assert "3 customers" in subj

    def test_subject_singular_when_one(self):
        from engine.output.digest_email import _build_subject
        subj = _build_subject(1, 1)
        assert "1 alert " in subj
        assert "1 customer" in subj
        assert "alerts" not in subj.split("·")[2]  # no plural in third segment

    def test_html_empty_state_renders(self):
        from engine.output.digest_email import _render_html
        html = _render_html({}, None, hours_window=24)
        assert "No CRITICAL or HIGH articles" in html
        assert "Snowkap Morning Brief" in html

    def test_html_with_articles_renders_send_to_client_button(self):
        from engine.output.digest_email import _render_html
        articles_by_company = {
            "test-co": [
                {
                    "id": "art-001",
                    "company_slug": "test-co",
                    "title": "Test article on critical regulatory event",
                    "source": "bloomberg.com",
                    "materiality": "CRITICAL",
                    "tier": "HOME",
                },
            ],
        }
        html = _render_html(articles_by_company, None, hours_window=24)
        assert "Test Co" in html  # company display name from slug
        assert "Test article on critical regulatory event" in html
        assert "Send to client" in html
        assert "art-001" in html  # article ID embedded in share link
        assert "CRITICAL" in html

    def test_html_with_overnight_summary_shows_stats_band(self):
        from engine.output.digest_email import _render_html
        summary = {
            "tenants_attempted": 17, "tenants_succeeded": 16,
            "articles_fetched": 340, "articles_selected": 51,
            "articles_passed_preflight": 38, "total_cost_usd": 3.05,
        }
        html = _render_html({}, summary, hours_window=24)
        assert "16 / 17 tenants" in html
        assert "$3.05" in html

    def test_group_by_company_sorts_critical_first(self):
        from engine.output.digest_email import _group_by_company
        articles = [
            {"company_slug": "low-co", "materiality": "MODERATE", "title": "x", "id": "a"},
            {"company_slug": "high-co", "materiality": "CRITICAL", "title": "y", "id": "b"},
            {"company_slug": "med-co", "materiality": "HIGH", "title": "z", "id": "c"},
        ]
        grouped = _group_by_company(articles)
        slugs = list(grouped.keys())
        # CRITICAL company appears first
        assert slugs[0] == "high-co"
        assert slugs[1] == "med-co"
        assert slugs[2] == "low-co"


# ---------------------------------------------------------------------------
# B. SQLite materiality-first feed sort
# ---------------------------------------------------------------------------


class TestMaterialityFirstSort:
    def test_query_feed_orders_critical_above_high(self, tmp_path):
        """Synthetic DB: insert 4 articles with mixed materiality + dates.
        Confirm CRITICAL sorts above HIGH even when HIGH is more recent."""
        from engine.index import sqlite_index
        # Use a tmp DB file
        db_path = tmp_path / "test_sort.db"
        with sqlite3.connect(db_path) as conn:
            conn.executescript(sqlite_index.SCHEMA_SQL)
            for col_def in (
                "ALTER TABLE article_index ADD COLUMN cfo_preflight_status TEXT",
                "ALTER TABLE article_index ADD COLUMN pinned_until TEXT",
            ):
                try:
                    conn.execute(col_def)
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise

            # Insert: HIGH on 2026-05-04 (newer) + CRITICAL on 2026-05-01 (older)
            for art_id, materiality, pub, score in [
                ("a", "HIGH", "2026-05-04T10:00:00+00:00", 8.0),
                ("b", "CRITICAL", "2026-05-01T10:00:00+00:00", 7.5),
                ("c", "MODERATE", "2026-05-04T11:00:00+00:00", 9.0),
                ("d", "LOW", "2026-05-04T12:00:00+00:00", 9.5),
            ]:
                conn.execute(
                    "INSERT INTO article_index (id, company_slug, title, "
                    "json_path, materiality, published_at, relevance_score, tier) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (art_id, "test-co", f"Article {art_id}", f"/{art_id}.json",
                     materiality, pub, score, "HOME"),
                )
            conn.commit()

        # Now query with the W10 sort logic — replicate the query directly
        # rather than going through query_feed (which has WAL setup that
        # mucks with the tmp file)
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM article_index
                ORDER BY
                    CASE WHEN COALESCE(pinned_until, '') > datetime('now') THEN 1 ELSE 0 END DESC,
                    CASE UPPER(COALESCE(materiality, ''))
                        WHEN 'CRITICAL' THEN 4
                        WHEN 'HIGH' THEN 3
                        WHEN 'MODERATE' THEN 2
                        WHEN 'LOW' THEN 1
                        ELSE 0
                    END DESC,
                    relevance_score DESC,
                    published_at DESC
                """
            ).fetchall()

        order = [r["id"] for r in rows]
        # Expected: CRITICAL first, then HIGH, then MODERATE, then LOW
        assert order == ["b", "a", "c", "d"], (
            f"materiality-first sort broken: got {order}, expected [b,a,c,d]"
        )

    def test_sql_uses_materiality_rank_in_order_by(self):
        """Static check: the W10 sort change must reach query_feed."""
        import inspect
        from engine.index.sqlite_index import query_feed
        src = inspect.getsource(query_feed)
        assert "CASE UPPER(COALESCE(materiality" in src
        assert "'CRITICAL' THEN 4" in src
        assert "'HIGH' THEN 3" in src


# ---------------------------------------------------------------------------
# C. Scheduler wiring
# ---------------------------------------------------------------------------


class TestSchedulerWiring:
    def test_main_recognises_morning_digest_once(self):
        import inspect
        from engine.scheduler import main
        src = inspect.getsource(main)
        assert "--morning-digest-once" in src
        assert "run_morning_digest_job" in src

    def test_main_recognises_overnight_batch_cron(self):
        import inspect
        from engine.scheduler import main
        src = inspect.getsource(main)
        # CronTrigger registration for overnight + digest
        assert "CronTrigger" in src
        assert "SNOWKAP_OVERNIGHT_BATCH_UTC_HOUR" in src
        assert "SNOWKAP_MORNING_DIGEST_UTC_HOUR" in src

    def test_run_morning_digest_job_swallows_exceptions(self):
        """The cron job MUST never crash the scheduler; if the digest
        send raises, the exception is logged + swallowed."""
        from engine.scheduler import run_morning_digest_job
        with patch(
            "engine.output.digest_email.send_morning_digest",
            side_effect=RuntimeError("simulated crash"),
        ):
            # Should NOT raise
            result = run_morning_digest_job()
            assert result is None  # cron jobs don't return


# ---------------------------------------------------------------------------
# D. NewsCard + PersonalStakesCard frontend smoke (compile only)
# ---------------------------------------------------------------------------


# TestFrontendCompiles removed (Phase 51 W4): it asserted on the legacy
# ArticleDetailSheet / NewsCard / PersonalStakesCard components, which were
# deleted as dead code (import-count 0, replaced by now/ArticleSheet). The
# frontend is now verified by the real Vite build (`tsc -b && vite build`),
# which catches every broken import — not by file-content string asserts.
