"""Phase 10 — campaign_store CRUD tests.

Covers:
  * Campaign create/read/update/delete
  * Recipient replace + append dedupes by (campaign_id, email) case-insensitive
  * ON DELETE CASCADE removes recipients when campaign is deleted
  * Send-log dedup probe respects the `since_iso` cutoff
  * Invalid inputs (bad cadence, bad day_of_month, mismatched article_selection)
    raise ValueError

Tests run against the real `data/snowkap.db` — the `_cleanup` fixture
truncates Phase 10 tables before each test so runs are deterministic.
"""

from __future__ import annotations

import pytest

from engine.models import campaign_store


@pytest.fixture(autouse=True)
def _cleanup():
    campaign_store._truncate_all()
    yield
    campaign_store._truncate_all()


# ---------------------------------------------------------------------------
# Campaign CRUD
# ---------------------------------------------------------------------------


def test_create_campaign_round_trips():
    c = campaign_store.create_campaign(
        name="Weekly Tata",
        created_by="sales@snowkap.com",
        target_company="tata-power",
        article_selection="latest_home",
        cadence="weekly",
        day_of_week=0,
        send_time_utc="09:00",
        next_send_at="2026-04-27T09:00:00+00:00",
        cta_url="https://snowkap.com/contact-us/",
        cta_label="Book a demo",
    )
    assert c.name == "Weekly Tata"
    assert c.status == "active"
    assert c.next_send_at == "2026-04-27T09:00:00+00:00"
    assert c.template_type == "share_single"  # V1 default

    fetched = campaign_store.get_campaign(c.id)
    assert fetched is not None
    assert fetched.id == c.id
    assert fetched.day_of_week == 0


def test_create_rejects_bad_cadence():
    with pytest.raises(ValueError, match="cadence"):
        campaign_store.create_campaign(
            name="x", created_by="a", target_company="t",
            article_selection="latest_home", cadence="daily",  # type: ignore[arg-type]
            next_send_at=None,
        )


def test_create_weekly_requires_day_of_week():
    with pytest.raises(ValueError, match="day_of_week"):
        campaign_store.create_campaign(
            name="x", created_by="a", target_company="t",
            article_selection="latest_home", cadence="weekly",
            next_send_at=None,
        )


def test_create_monthly_requires_day_of_month():
    with pytest.raises(ValueError, match="day_of_month"):
        campaign_store.create_campaign(
            name="x", created_by="a", target_company="t",
            article_selection="latest_home", cadence="monthly",
            next_send_at=None,
        )


def test_create_monthly_rejects_day_29plus():
    with pytest.raises(ValueError, match="1..28"):
        campaign_store.create_campaign(
            name="x", created_by="a", target_company="t",
            article_selection="latest_home", cadence="monthly",
            day_of_month=29, next_send_at=None,
        )


def test_create_specific_requires_article_id():
    with pytest.raises(ValueError, match="article_id"):
        campaign_store.create_campaign(
            name="x", created_by="a", target_company="t",
            article_selection="specific", cadence="once",
            next_send_at=None,
        )


def test_update_campaign_bumps_updated_at():
    c = campaign_store.create_campaign(
        name="orig", created_by="a", target_company="t",
        article_selection="latest_home", cadence="once",
        next_send_at="2026-04-27T09:00:00+00:00",
    )
    original_updated = c.updated_at
    updated = campaign_store.update_campaign(c.id, name="renamed")
    assert updated is not None
    assert updated.name == "renamed"
    assert updated.updated_at >= original_updated


def test_set_status_pause_resume():
    c = campaign_store.create_campaign(
        name="x", created_by="a", target_company="t",
        article_selection="latest_home", cadence="once",
        next_send_at="2026-04-27T09:00:00+00:00",
    )
    campaign_store.set_status(c.id, "paused")
    assert campaign_store.get_campaign(c.id).status == "paused"  # type: ignore[union-attr]
    campaign_store.set_status(c.id, "active")
    assert campaign_store.get_campaign(c.id).status == "active"  # type: ignore[union-attr]


def test_list_due_campaigns_filters_by_next_send_at_and_status():
    campaign_store.create_campaign(
        name="past-active", created_by="a", target_company="t1",
        article_selection="latest_home", cadence="once",
        next_send_at="2026-04-20T00:00:00+00:00",  # past
    )
    campaign_store.create_campaign(
        name="future-active", created_by="a", target_company="t2",
        article_selection="latest_home", cadence="once",
        next_send_at="2026-05-01T00:00:00+00:00",  # future
    )
    paused = campaign_store.create_campaign(
        name="past-paused", created_by="a", target_company="t3",
        article_selection="latest_home", cadence="once",
        next_send_at="2026-04-20T00:00:00+00:00",
    )
    campaign_store.set_status(paused.id, "paused")

    due = campaign_store.list_due_campaigns(now="2026-04-27T09:00:00+00:00")
    names = {c.name for c in due}
    assert names == {"past-active"}


def test_delete_campaign_cascades_recipients():
    c = campaign_store.create_campaign(
        name="x", created_by="a", target_company="t",
        article_selection="latest_home", cadence="once",
        next_send_at="2026-04-27T09:00:00+00:00",
    )
    campaign_store.replace_recipients(c.id, [("a@x.com", None), ("b@x.com", None)])
    assert campaign_store.count_recipients(c.id) == 2

    ok = campaign_store.delete_campaign(c.id)
    assert ok is True
    assert campaign_store.get_campaign(c.id) is None
    assert campaign_store.count_recipients(c.id) == 0


# ---------------------------------------------------------------------------
# Recipients
# ---------------------------------------------------------------------------


def test_replace_recipients_normalises_and_dedupes():
    c = campaign_store.create_campaign(
        name="x", created_by="a", target_company="t",
        article_selection="latest_home", cadence="once",
        next_send_at="2026-04-27T09:00:00+00:00",
    )
    entries = [
        (" Alice@X.COM ", None),
        ("alice@x.com", None),      # dup (case + space)
        ("bob@x.com", "Robert"),
        ("", None),                 # empty skipped
    ]
    out = campaign_store.replace_recipients(c.id, entries)
    emails = sorted(r.email for r in out)
    assert emails == ["alice@x.com", "bob@x.com"]
    bob = next(r for r in out if r.email == "bob@x.com")
    assert bob.name_override == "Robert"


def test_replace_recipients_replaces_not_appends():
    c = campaign_store.create_campaign(
        name="x", created_by="a", target_company="t",
        article_selection="latest_home", cadence="once",
        next_send_at="2026-04-27T09:00:00+00:00",
    )
    campaign_store.replace_recipients(c.id, [("a@x.com", None)])
    campaign_store.replace_recipients(c.id, [("b@x.com", None)])
    rs = campaign_store.list_recipients(c.id)
    assert [r.email for r in rs] == ["b@x.com"]


def test_add_recipients_idempotent_on_duplicates():
    c = campaign_store.create_campaign(
        name="x", created_by="a", target_company="t",
        article_selection="latest_home", cadence="once",
        next_send_at="2026-04-27T09:00:00+00:00",
    )
    campaign_store.add_recipients(c.id, [("a@x.com", None)])
    campaign_store.add_recipients(c.id, [("A@X.COM", None)])  # same but different case
    assert campaign_store.count_recipients(c.id) == 1


def test_touch_recipient_last_sent_updates_timestamp():
    c = campaign_store.create_campaign(
        name="x", created_by="a", target_company="t",
        article_selection="latest_home", cadence="once",
        next_send_at="2026-04-27T09:00:00+00:00",
    )
    campaign_store.replace_recipients(c.id, [("a@x.com", None)])
    campaign_store.touch_recipient_last_sent(c.id, "a@x.com", "2026-04-27T09:05:00+00:00")
    r = campaign_store.list_recipients(c.id)[0]
    assert r.last_sent_at == "2026-04-27T09:05:00+00:00"


# ---------------------------------------------------------------------------
# Send log + dedup probe
# ---------------------------------------------------------------------------


def test_append_send_log_then_list_descending():
    c = campaign_store.create_campaign(
        name="x", created_by="a", target_company="t",
        article_selection="latest_home", cadence="once",
        next_send_at="2026-04-27T09:00:00+00:00",
    )
    campaign_store.append_send_log(
        campaign_id=c.id, recipient_email="a@x.com",
        article_id="art1", subject="S", html_length=100,
        status="sent", provider_id="pid1",
        sent_at="2026-04-27T09:00:00+00:00",
    )
    campaign_store.append_send_log(
        campaign_id=c.id, recipient_email="a@x.com",
        article_id="art1", subject="S",
        status="sent", provider_id="pid2",
        sent_at="2026-04-27T10:00:00+00:00",
    )
    log = campaign_store.list_send_log(c.id)
    assert len(log) == 2
    # Newest first
    assert log[0].provider_id == "pid2"
    assert log[1].provider_id == "pid1"


def test_find_recent_send_respects_since_cutoff():
    c = campaign_store.create_campaign(
        name="x", created_by="a", target_company="t",
        article_selection="latest_home", cadence="once",
        next_send_at="2026-04-27T09:00:00+00:00",
    )
    campaign_store.append_send_log(
        campaign_id=c.id, recipient_email="a@x.com", article_id="art1",
        status="sent", sent_at="2026-04-27T09:00:00+00:00", provider_id="p1",
    )
    # Cutoff before the send → found
    found = campaign_store.find_recent_send(
        c.id, "a@x.com", "art1", since_iso="2026-04-20T00:00:00+00:00",
    )
    assert found is not None
    assert found.provider_id == "p1"

    # Cutoff after the send → not found
    found2 = campaign_store.find_recent_send(
        c.id, "a@x.com", "art1", since_iso="2026-04-28T00:00:00+00:00",
    )
    assert found2 is None


def test_find_recent_send_skips_failed_and_skipped_statuses():
    """Dedup should only consider successful-ish sends (sent/preview).
    Failed or skipped rows should NOT block a retry."""
    c = campaign_store.create_campaign(
        name="x", created_by="a", target_company="t",
        article_selection="latest_home", cadence="once",
        next_send_at="2026-04-27T09:00:00+00:00",
    )
    campaign_store.append_send_log(
        campaign_id=c.id, recipient_email="a@x.com", article_id="art1",
        status="failed", error="boom", sent_at="2026-04-27T09:00:00+00:00",
    )
    campaign_store.append_send_log(
        campaign_id=c.id, recipient_email="a@x.com", article_id="art1",
        status="skipped_stale", sent_at="2026-04-27T09:05:00+00:00",
    )
    found = campaign_store.find_recent_send(
        c.id, "a@x.com", "art1", since_iso="2026-04-20T00:00:00+00:00",
    )
    assert found is None  # failed/skipped should NOT dedup the next attempt


def test_send_log_survives_campaign_delete():
    """Audit trail must persist even after campaign is hard-deleted."""
    c = campaign_store.create_campaign(
        name="x", created_by="a", target_company="t",
        article_selection="latest_home", cadence="once",
        next_send_at="2026-04-27T09:00:00+00:00",
    )
    campaign_store.append_send_log(
        campaign_id=c.id, recipient_email="a@x.com", status="sent",
        sent_at="2026-04-27T09:00:00+00:00",
    )
    campaign_store.delete_campaign(c.id)

    # Campaign gone
    assert campaign_store.get_campaign(c.id) is None
    # But send log rows survive (no FK)
    log = campaign_store.list_send_log(c.id)
    assert len(log) == 1
