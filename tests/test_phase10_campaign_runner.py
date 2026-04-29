"""Phase 10 — campaign_runner tests.

Covers the ship-gate behaviours:
  * latest_home resolution picks the most recent HOME article
  * No HOME article → `skipped_stale` per recipient, next_send_at NOT advanced
  * Accuracy check rejects insights without materiality/frameworks
  * Per-recipient iteration continues after a single Resend failure
  * Dedup: two runs in the cadence window → second writes `skipped_dedup`
  * next_send_at advances on successful run; force_send does NOT advance it
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from engine.models import campaign_store
from engine.output.campaign_runner import run_due_campaigns
from engine.output.share_service import ShareResult


def _minimal_insight_payload(
    article_id: str,
    *,
    company_slug: str = "test-company",
    materiality: str = "HIGH",
    with_frameworks: bool = True,
    schema_version: str = "2.0-primitives-l2",
) -> dict:
    frameworks = []
    if with_frameworks:
        frameworks = [
            {"id": "BRSR", "section": "P6", "rationale": "test framework section"},
        ]
    return {
        "article": {
            "id": article_id,
            "company_slug": company_slug,
            "title": "Test article",
            "url": "https://example.com/test",
            "published_at": "2026-04-22T10:00:00+00:00",
        },
        "pipeline": {
            "tier": "HOME",
            "relevance": {"adjusted_total": 8.0},
            "themes": {"primary_pillar": "Environmental"},
            "frameworks": frameworks,
            "ontology_query_count": 5,
        },
        "insight": {
            "decision_summary": {
                "materiality": materiality,
                "key_risk": "Test bottom line",
            },
            "net_impact_summary": "Test net impact.",
            "impact_score": 7.5,
        },
        "perspectives": {
            "cfo": {"headline": "CFO view"},
            "ceo": {"headline": "CEO view", "board_paragraph": "Board action here."},
            "esg-analyst": {"headline": "Analyst view"},
        },
        "meta": {
            "schema_version": schema_version,
            "written_at": "2026-04-22T10:00:00+00:00",
        },
    }


@pytest.fixture
def _outputs_root():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture(autouse=True)
def _clean_store():
    campaign_store._truncate_all()
    yield
    campaign_store._truncate_all()


def _seed_insight_file(outputs_root: Path, company_slug: str, article_id: str, payload: dict) -> Path:
    insights_dir = outputs_root / company_slug / "insights"
    insights_dir.mkdir(parents=True, exist_ok=True)
    p = insights_dir / f"2026-04-22_{article_id}.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Successful run
# ---------------------------------------------------------------------------


def test_run_due_fires_one_recipient_and_logs_sent(_outputs_root):
    _seed_insight_file(
        _outputs_root, "test-company", "art1",
        _minimal_insight_payload("art1", company_slug="test-company"),
    )

    c = campaign_store.create_campaign(
        name="test", created_by="sales@snowkap.com",
        target_company="test-company",
        article_selection="specific", article_id="art1",
        cadence="once", next_send_at="2026-04-20T00:00:00+00:00",  # past → due
    )
    campaign_store.replace_recipients(c.id, [("target@example.com", None)])

    # Mock share_article_by_email so we don't actually hit Resend
    fake_result = ShareResult(
        status="sent", recipient="target@example.com",
        recipient_name="Target", subject="Test subject", html_length=500,
        article_id="art1", company_slug="test-company", company_name="Test",
        provider_id="email_abc", error="",
    )
    with patch("engine.output.campaign_runner.share_article_by_email", return_value=fake_result):
        summaries = run_due_campaigns(
            now="2026-04-27T09:00:00+00:00",
            outputs_root=_outputs_root,
        )

    assert len(summaries) == 1
    s = summaries[0]
    assert s["sent"] == 1
    assert s["failed"] == 0
    assert s["skipped_stale"] == 0

    log = campaign_store.list_send_log(c.id)
    assert len(log) == 1
    assert log[0].status == "sent"
    assert log[0].provider_id == "email_abc"

    # next_send_at should be cleared for once-fire (schedule done)
    updated = campaign_store.get_campaign(c.id)
    assert updated.next_send_at is None  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# latest_home with no HOME article
# ---------------------------------------------------------------------------


def test_no_home_article_logs_skipped_stale_and_does_not_advance(_outputs_root):
    # No insight file seeded
    c = campaign_store.create_campaign(
        name="weekly", created_by="sales@snowkap.com",
        target_company="empty-company",
        article_selection="latest_home",
        cadence="weekly", day_of_week=0, send_time_utc="09:00",
        next_send_at="2026-04-20T00:00:00+00:00",
    )
    campaign_store.replace_recipients(c.id, [("target@example.com", None)])

    summaries = run_due_campaigns(
        now="2026-04-27T09:00:00+00:00",
        outputs_root=_outputs_root,
    )
    s = summaries[0]
    assert s["skipped_stale"] == 1
    assert s["sent"] == 0

    # next_send_at must NOT have advanced — retry next cron tick
    updated = campaign_store.get_campaign(c.id)
    assert updated.next_send_at == "2026-04-20T00:00:00+00:00"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Accuracy check
# ---------------------------------------------------------------------------


def test_accuracy_check_rejects_insight_without_frameworks(_outputs_root):
    _seed_insight_file(
        _outputs_root, "test-company", "art-bad",
        _minimal_insight_payload("art-bad", with_frameworks=False),
    )
    c = campaign_store.create_campaign(
        name="x", created_by="a",
        target_company="test-company",
        article_selection="specific", article_id="art-bad",
        cadence="once", next_send_at="2026-04-20T00:00:00+00:00",
    )
    campaign_store.replace_recipients(c.id, [("target@example.com", None)])

    summaries = run_due_campaigns(
        now="2026-04-27T09:00:00+00:00", outputs_root=_outputs_root,
    )
    assert summaries[0]["skipped_stale"] == 1
    assert summaries[0]["sent"] == 0
    assert "framework" in summaries[0]["reason"].lower()

    log = campaign_store.list_send_log(c.id)
    assert log[0].status == "skipped_stale"


# ---------------------------------------------------------------------------
# Recipient iteration resilience
# ---------------------------------------------------------------------------


def test_one_recipient_failure_does_not_abort_batch(_outputs_root):
    _seed_insight_file(
        _outputs_root, "test-company", "art1",
        _minimal_insight_payload("art1"),
    )
    c = campaign_store.create_campaign(
        name="x", created_by="a", target_company="test-company",
        article_selection="specific", article_id="art1",
        cadence="once", next_send_at="2026-04-20T00:00:00+00:00",
    )
    campaign_store.replace_recipients(c.id, [
        ("ok1@example.com", None),
        ("boom@example.com", None),
        ("ok2@example.com", None),
    ])

    call_order = []
    def fake_share(*, article_id, company_slug, recipient_email, **_):
        call_order.append(recipient_email)
        if recipient_email == "boom@example.com":
            raise RuntimeError("Resend API exploded")
        return ShareResult(
            status="sent", recipient=recipient_email, recipient_name=None,
            subject="S", html_length=100, article_id=article_id,
            company_slug=company_slug, company_name="Test", provider_id="pid",
            error="",
        )

    with patch("engine.output.campaign_runner.share_article_by_email", side_effect=fake_share):
        summaries = run_due_campaigns(
            now="2026-04-27T09:00:00+00:00", outputs_root=_outputs_root,
        )

    s = summaries[0]
    assert s["sent"] == 2
    assert s["failed"] == 1
    assert len(call_order) == 3  # all three tried


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def test_dedup_skips_recent_duplicate_within_cadence_window(_outputs_root):
    _seed_insight_file(
        _outputs_root, "test-company", "art1",
        _minimal_insight_payload("art1"),
    )
    c = campaign_store.create_campaign(
        name="weekly", created_by="a", target_company="test-company",
        article_selection="specific", article_id="art1",
        cadence="weekly", day_of_week=0, send_time_utc="09:00",
        next_send_at="2026-04-20T00:00:00+00:00",
    )
    campaign_store.replace_recipients(c.id, [("target@example.com", None)])

    fake_result = ShareResult(
        status="sent", recipient="target@example.com", recipient_name=None,
        subject="S", html_length=100, article_id="art1",
        company_slug="test-company", company_name="Test", provider_id="p1", error="",
    )
    with patch("engine.output.campaign_runner.share_article_by_email", return_value=fake_result):
        # First run
        run_due_campaigns(now="2026-04-27T09:00:00+00:00", outputs_root=_outputs_root)
        # Reset next_send_at so campaign is due again
        campaign_store.update_campaign(c.id, next_send_at="2026-04-27T10:00:00+00:00")
        # Second run very soon after (within 3.5 days dedup window)
        summaries = run_due_campaigns(now="2026-04-27T11:00:00+00:00", outputs_root=_outputs_root)

    s = summaries[0]
    assert s["sent"] == 0
    assert s["skipped_dedup"] == 1

    log = campaign_store.list_send_log(c.id)
    statuses = [row.status for row in log]
    assert "skipped_dedup" in statuses
    # One real send + one dedup skip = 2 rows
    assert len(log) == 2


# ---------------------------------------------------------------------------
# force_send (send-now)
# ---------------------------------------------------------------------------


def test_force_send_does_not_advance_next_send_at(_outputs_root):
    _seed_insight_file(
        _outputs_root, "test-company", "art1",
        _minimal_insight_payload("art1"),
    )
    c = campaign_store.create_campaign(
        name="weekly", created_by="a", target_company="test-company",
        article_selection="specific", article_id="art1",
        cadence="weekly", day_of_week=0, send_time_utc="09:00",
        next_send_at="2026-05-04T09:00:00+00:00",  # future — not due
    )
    campaign_store.replace_recipients(c.id, [("a@example.com", None)])

    fake_result = ShareResult(
        status="sent", recipient="a@example.com", recipient_name=None,
        subject="S", html_length=100, article_id="art1",
        company_slug="test-company", company_name="T", provider_id="p", error="",
    )
    with patch("engine.output.campaign_runner.share_article_by_email", return_value=fake_result):
        # Without force: campaign is not due (next_send_at is future) → nothing happens
        result_no_force = run_due_campaigns(
            now="2026-04-27T09:00:00+00:00", outputs_root=_outputs_root,
        )
        assert result_no_force == []

        # With force + explicit campaign_id: fires immediately but leaves schedule alone
        run_due_campaigns(
            now="2026-04-27T09:00:00+00:00", outputs_root=_outputs_root,
            campaign_id=c.id, force=True,
        )

    updated = campaign_store.get_campaign(c.id)
    # next_send_at should still point at the original Monday — not advanced
    assert updated.next_send_at == "2026-05-04T09:00:00+00:00"  # type: ignore[union-attr]
    # But a send_log row should exist
    log = campaign_store.list_send_log(c.id)
    assert any(r.status == "sent" for r in log)


def test_paused_campaign_ignored_without_force(_outputs_root):
    _seed_insight_file(
        _outputs_root, "test-company", "art1",
        _minimal_insight_payload("art1"),
    )
    c = campaign_store.create_campaign(
        name="paused", created_by="a", target_company="test-company",
        article_selection="specific", article_id="art1",
        cadence="once", next_send_at="2026-04-20T00:00:00+00:00",  # past/due
    )
    campaign_store.set_status(c.id, "paused")
    campaign_store.replace_recipients(c.id, [("a@example.com", None)])

    summaries = run_due_campaigns(
        now="2026-04-27T09:00:00+00:00", outputs_root=_outputs_root,
    )
    # No paused campaigns in list_due_campaigns
    assert summaries == []
