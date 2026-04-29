"""Phase 10 — campaigns REST API tests.

Covers:
  * Permission gate: client tokens → 403 on every endpoint
  * Create round-trips: POST → GET → list visible
  * Recipients textarea parsed: N emails → N rows
  * Pause/resume/archive cycle
  * Send-now returns 202
  * Send-log returns audit entries
  * Invalid cadence / missing schedule fields → 400
"""

from __future__ import annotations

import base64
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.auth_context import SUPER_ADMIN_PERMISSIONS
from api.main import app
from engine.models import campaign_store


def _mint(claims: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"Bearer {header}.{payload}."


def _admin_token() -> str:
    return _mint({"sub": "sales@snowkap.com", "permissions": list(SUPER_ADMIN_PERMISSIONS)})


def _client_token() -> str:
    return _mint({"sub": "ci@mintedit.com", "permissions": ["read", "view_news"]})


@pytest.fixture(autouse=True)
def _clean_store():
    campaign_store._truncate_all()
    yield
    campaign_store._truncate_all()


@pytest.fixture
def _client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Permission gate
# ---------------------------------------------------------------------------


def test_client_token_blocked_from_list(_client):
    r = _client.get("/api/campaigns", headers={"Authorization": _client_token()})
    assert r.status_code == 403


def test_client_token_blocked_from_create(_client):
    r = _client.post(
        "/api/campaigns",
        headers={"Authorization": _client_token()},
        json={"name": "x", "target_company": "t", "article_selection": "latest_home",
              "cadence": "once", "recipients": [{"email": "a@x.com"}]},
    )
    assert r.status_code == 403


def test_missing_token_blocked(_client):
    r = _client.get("/api/campaigns")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_campaign_round_trips(_client):
    body = {
        "name": "Weekly Adani Brief",
        "target_company": "adani-power",
        "article_selection": "latest_home",
        "cadence": "weekly",
        "day_of_week": 0,
        "send_time_utc": "09:00",
        "cta_url": "https://snowkap.com/contact-us/",
        "cta_label": "Book a demo",
        "sender_note": "Test note",
        "recipients": [
            {"email": "a@mintedit.com"},
            {"email": "b@et.com", "name_override": "Bharath"},
        ],
    }
    r = _client.post("/api/campaigns", headers={"Authorization": _admin_token()}, json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["name"] == "Weekly Adani Brief"
    assert data["recipient_count"] == 2
    assert data["next_send_at"]  # computed by cadence
    assert data["created_by"] == "sales@snowkap.com"
    campaign_id = data["id"]

    # GET round trip
    r2 = _client.get(f"/api/campaigns/{campaign_id}", headers={"Authorization": _admin_token()})
    assert r2.status_code == 200
    assert r2.json()["id"] == campaign_id

    # List shows it
    r3 = _client.get("/api/campaigns", headers={"Authorization": _admin_token()})
    assert r3.status_code == 200
    assert any(c["id"] == campaign_id for c in r3.json()["campaigns"])


def test_create_rejects_empty_recipients(_client):
    body = {
        "name": "No one", "target_company": "t",
        "article_selection": "latest_home", "cadence": "once",
        "recipients": [],
    }
    r = _client.post("/api/campaigns", headers={"Authorization": _admin_token()}, json=body)
    assert r.status_code == 422


def test_create_rejects_invalid_cadence(_client):
    body = {
        "name": "Bad cadence", "target_company": "t",
        "article_selection": "latest_home", "cadence": "daily",
        "recipients": [{"email": "a@x.com"}],
    }
    r = _client.post("/api/campaigns", headers={"Authorization": _admin_token()}, json=body)
    assert r.status_code == 422


def test_create_weekly_without_day_of_week_returns_400(_client):
    body = {
        "name": "Weekly no day", "target_company": "t",
        "article_selection": "latest_home", "cadence": "weekly",
        "send_time_utc": "09:00",
        "recipients": [{"email": "a@x.com"}],
    }
    r = _client.post("/api/campaigns", headers={"Authorization": _admin_token()}, json=body)
    assert r.status_code == 400
    assert "day_of_week" in r.json()["detail"].lower()


def test_create_specific_without_article_id_returns_400(_client):
    body = {
        "name": "Specific no article", "target_company": "t",
        "article_selection": "specific", "cadence": "once",
        "recipients": [{"email": "a@x.com"}],
    }
    r = _client.post("/api/campaigns", headers={"Authorization": _admin_token()}, json=body)
    assert r.status_code == 400
    assert "article_id" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Recipients
# ---------------------------------------------------------------------------


def test_bulk_replace_recipients(_client):
    # seed campaign
    c = campaign_store.create_campaign(
        name="x", created_by="sales@snowkap.com",
        target_company="t", article_selection="latest_home",
        cadence="once", next_send_at="2026-05-01T09:00:00+00:00",
    )
    campaign_store.replace_recipients(c.id, [("old@x.com", None)])

    body = {"recipients": [
        {"email": "a@x.com"},
        {"email": "b@x.com", "name_override": "Bob"},
        {"email": "c@x.com"},
    ]}
    r = _client.post(
        f"/api/campaigns/{c.id}/recipients",
        headers={"Authorization": _admin_token()},
        json=body,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 3
    emails = sorted([r["email"] for r in data["recipients"]])
    assert emails == ["a@x.com", "b@x.com", "c@x.com"]


# ---------------------------------------------------------------------------
# Lifecycle: pause / resume / archive / delete
# ---------------------------------------------------------------------------


def test_pause_resume_archive_cycle(_client):
    body = {
        "name": "Cycle", "target_company": "t",
        "article_selection": "latest_home", "cadence": "once",
        "recipients": [{"email": "a@x.com"}],
    }
    create = _client.post("/api/campaigns", headers={"Authorization": _admin_token()}, json=body)
    cid = create.json()["id"]

    # pause
    r = _client.post(f"/api/campaigns/{cid}/pause", headers={"Authorization": _admin_token()})
    assert r.status_code == 200
    assert r.json()["status"] == "paused"

    # resume
    r = _client.post(f"/api/campaigns/{cid}/resume", headers={"Authorization": _admin_token()})
    assert r.json()["status"] == "active"

    # archive
    r = _client.post(f"/api/campaigns/{cid}/archive", headers={"Authorization": _admin_token()})
    assert r.json()["status"] == "archived"

    # delete
    r = _client.delete(f"/api/campaigns/{cid}", headers={"Authorization": _admin_token()})
    assert r.status_code == 204

    # 404 on re-get
    r = _client.get(f"/api/campaigns/{cid}", headers={"Authorization": _admin_token()})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# send-now + send-log
# ---------------------------------------------------------------------------


def test_send_now_returns_202_and_queues(_client):
    body = {
        "name": "Now", "target_company": "t",
        "article_selection": "specific", "article_id": "some_id",
        "cadence": "once", "recipients": [{"email": "a@x.com"}],
    }
    create = _client.post("/api/campaigns", headers={"Authorization": _admin_token()}, json=body)
    cid = create.json()["id"]

    with patch("api.routes.campaigns.run_due_campaigns") as mock_run:
        r = _client.post(f"/api/campaigns/{cid}/send-now", headers={"Authorization": _admin_token()})
    assert r.status_code == 202
    data = r.json()
    assert data["status"] == "queued"
    assert data["campaign_id"] == cid


def test_send_log_returns_entries(_client):
    body = {
        "name": "LogTest", "target_company": "t",
        "article_selection": "specific", "article_id": "art",
        "cadence": "once", "recipients": [{"email": "a@x.com"}],
    }
    create = _client.post("/api/campaigns", headers={"Authorization": _admin_token()}, json=body)
    cid = create.json()["id"]

    # Seed a log row directly
    campaign_store.append_send_log(
        campaign_id=cid, recipient_email="a@x.com",
        article_id="art", subject="S", status="sent",
        provider_id="pid", sent_at="2026-04-27T09:00:00+00:00",
    )

    r = _client.get(f"/api/campaigns/{cid}/send-log", headers={"Authorization": _admin_token()})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["entries"][0]["provider_id"] == "pid"


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------


def test_patch_recomputes_next_send_at_when_schedule_changes(_client):
    # Start with once cadence
    body = {
        "name": "Patch test", "target_company": "t",
        "article_selection": "latest_home", "cadence": "once",
        "send_time_utc": "09:00",
        "recipients": [{"email": "a@x.com"}],
    }
    create = _client.post("/api/campaigns", headers={"Authorization": _admin_token()}, json=body)
    cid = create.json()["id"]
    original_next = create.json()["next_send_at"]

    # Change to weekly — next_send_at should recompute
    patch_body = {"cadence": "weekly", "day_of_week": 0, "send_time_utc": "10:00"}
    r = _client.patch(f"/api/campaigns/{cid}", headers={"Authorization": _admin_token()}, json=patch_body)
    assert r.status_code == 200
    updated = r.json()
    assert updated["cadence"] == "weekly"
    assert updated["day_of_week"] == 0
    assert updated["send_time_utc"] == "10:00"
    # next_send_at should have been recomputed; different from the original
    assert updated["next_send_at"] != original_next
