"""Phase 10 access audit: sales@snowkap.com must have every role's access
and be able to see any analysis for any company.

Covers:
  * SUPER_ADMIN_PERMISSIONS is a superset of every Permission enum value
  * /api/admin/tenants lists targets + every onboarded company
  * For every tenant the switcher lists, /api/news/feed returns 200
  * All 3 perspectives (CFO, CEO, ESG Analyst) are retrievable for any article
  * Full insight payload is retrievable
  * Regression guard: regular client tokens still get 403 on admin + share
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import patch

# decode_bearer / mint_bearer require JWT_SECRET. Set a stable test value
# before importing api.auth_context so module-level import paths see it.
os.environ.setdefault("JWT_SECRET", "test-secret-xxxxxxxxxxxxxxxxxxxxxx")

import pytest
from fastapi.testclient import TestClient

from api.auth_context import SUPER_ADMIN_PERMISSIONS, mint_bearer
from api.main import app
from engine.config import get_data_path
from engine.index.sqlite_index import DB_PATH, upsert_article


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mint(claims: dict) -> str:
    """Mint a signed bearer-header value using the production helper."""
    return f"Bearer {mint_bearer(claims)}"


def _admin_token() -> str:
    return _mint({"sub": "sales@snowkap.com", "permissions": list(SUPER_ADMIN_PERMISSIONS)})


def _client_token() -> str:
    return _mint(
        {"sub": "ci@mintedit.com", "permissions": ["read", "view_news", "view_analysis", "chat"]}
    )


# ---------------------------------------------------------------------------
# 1. SUPER_ADMIN_PERMISSIONS covers every Permission enum value
# ---------------------------------------------------------------------------


def test_super_admin_has_every_permission_enum_value():
    """If a new Permission is added to backend/core/permissions.py, this test
    fails until it's also added to SUPER_ADMIN_PERMISSIONS. Locks the
    invariant that super_admin truly = 'every role's perms combined'."""
    from backend.core.permissions import Permission

    enum_values = {p.value for p in Permission}
    super_admin_set = set(SUPER_ADMIN_PERMISSIONS)
    missing = enum_values - super_admin_set
    assert not missing, (
        f"SUPER_ADMIN_PERMISSIONS is missing {len(missing)} enum value(s): {sorted(missing)}"
    )


def test_super_admin_permissions_has_no_duplicates():
    assert len(SUPER_ADMIN_PERMISSIONS) == len(set(SUPER_ADMIN_PERMISSIONS)), (
        f"SUPER_ADMIN_PERMISSIONS has duplicates: {SUPER_ADMIN_PERMISSIONS}"
    )


def test_super_admin_carries_phase10_additions():
    """Phase 10 added 3 new perms — they must all be on super-admin."""
    for required in ("super_admin", "override_tenant_context", "manage_drip_campaigns"):
        assert required in SUPER_ADMIN_PERMISSIONS


# ---------------------------------------------------------------------------
# 2. Cross-company access: super-admin can fetch feed for every tenant
# ---------------------------------------------------------------------------


def test_super_admin_can_fetch_feed_for_every_tenant_in_switcher():
    """Every slug /api/admin/tenants returns must be queryable on /news/feed.
    This is the UX guarantee: clicking any row in CompanySwitcher loads data."""
    client = TestClient(app)

    # Seed one extra onboarded tenant so we cover both 'target' and 'onboarded'
    with patch.dict("os.environ", {"SNOWKAP_INTERNAL_EMAILS": "sales@snowkap.com"}, clear=False):
        login = client.post(
            "/api/auth/login",
            json={
                "email": "cto@phase10-audit-prospect.com",
                "domain": "phase10-audit-prospect.com",
                "designation": "cto",
                "company_name": "Phase10 Audit Prospect",
                "name": "Test CTO",
            },
        )
        assert login.status_code == 200

    r = client.get("/api/admin/tenants", headers={"Authorization": _admin_token()})
    assert r.status_code == 200
    tenants = r.json()
    assert len(tenants) >= 8  # 7 targets + at least 1 onboarded

    for t in tenants:
        slug = t["slug"]
        feed = client.get(
            f"/api/news/feed?company_id={slug}&limit=5",
            headers={"Authorization": _admin_token()},
        )
        assert feed.status_code == 200, (
            f"Admin couldn't fetch feed for {slug} ({t.get('source')}): {feed.status_code} {feed.text}"
        )
        body = feed.json()
        # Response shape can be either {articles, total} or {items, count}
        assert isinstance(body, dict), f"feed for {slug} returned non-dict: {body}"


def test_super_admin_can_fetch_companies_list():
    client = TestClient(app)
    r = client.get("/api/companies/", headers={"Authorization": _admin_token()})
    assert r.status_code == 200
    body = r.json()
    # Response shape: {companies, total}
    assert "companies" in body
    assert body["total"] >= 7


# ---------------------------------------------------------------------------
# 3. All-perspectives access: super-admin can fetch CFO, CEO, ESG Analyst views
# ---------------------------------------------------------------------------


@pytest.fixture
def _seeded_article():
    """Seed a minimal insight JSON with all 3 perspectives, index it, and
    yield the article_id. Cleanup removes the temp file + DB row."""
    article_id = f"phase10_audit_{uuid.uuid4().hex[:10]}"
    audit_dir = get_data_path("outputs") / "__phase10_audit__" / "insights"
    audit_dir.mkdir(parents=True, exist_ok=True)
    json_path = audit_dir / f"{article_id}.json"

    payload = {
        "article": {
            "id": article_id,
            "company_slug": "adani-power",
            "title": "Phase 10 audit article",
            "source": "test",
            "url": "https://example.com/phase10-audit",
            "published_at": "2026-04-23T00:00:00+00:00",
        },
        "pipeline": {
            "tier": "HOME",
            "relevance": {"adjusted_total": 8.0},
            "themes": {"primary_pillar": "Environmental", "primary_theme": "emissions"},
            "nlp": {"content_type": "news"},
            "frameworks": [{"id": "BRSR", "section": "P6", "rationale": "test"}],
            "ontology_query_count": 7,
        },
        "insight": {
            "decision_summary": {
                "materiality": "HIGH",
                "key_risk": "Test risk for Phase 10 audit",
                "top_opportunity": "Test opportunity",
            },
            "net_impact_summary": "Audit impact summary",
            "impact_score": 8.0,
        },
        "perspectives": {
            "cfo": {
                "headline": "CFO view of audit article",
                "bottom_line": "₹500 Cr exposure (engine estimate)",
                "what_matters": ["a", "b", "c"],
                "do_nothing": False,
            },
            "ceo": {
                "headline": "CEO view of audit article",
                "board_paragraph": "Board should approve remediation within 4 weeks.",
                "what_matters": ["strategic", "brand", "growth"],
                "do_nothing": False,
            },
            "esg-analyst": {
                "headline": "ESG Analyst view of audit article",
                "framework_alignment": {"BRSR": "P6 applies"},
                "what_matters": ["disclosure", "verification", "mitigation"],
                "do_nothing": False,
            },
        },
        "recommendations": {"recommendations": []},
        "meta": {"written_at": "2026-04-23T00:00:00+00:00"},
    }

    json_path.write_text(json.dumps(payload), encoding="utf-8")
    upsert_article(payload, json_path)

    yield article_id

    # Cleanup
    try:
        json_path.unlink()
    except FileNotFoundError:
        pass
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("DELETE FROM article_index WHERE id = ?", (article_id,))
    except Exception:
        pass


def test_super_admin_can_fetch_cfo_perspective(_seeded_article):
    client = TestClient(app)
    r = client.get(
        f"/api/insights/{_seeded_article}?perspective=cfo",
        headers={"Authorization": _admin_token()},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["perspective"]["headline"] == "CFO view of audit article"


def test_super_admin_can_fetch_ceo_perspective(_seeded_article):
    client = TestClient(app)
    r = client.get(
        f"/api/insights/{_seeded_article}?perspective=ceo",
        headers={"Authorization": _admin_token()},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["perspective"]["headline"] == "CEO view of audit article"
    assert "Board" in body["perspective"]["board_paragraph"]


def test_super_admin_can_fetch_esg_analyst_perspective(_seeded_article):
    client = TestClient(app)
    r = client.get(
        f"/api/insights/{_seeded_article}?perspective=esg-analyst",
        headers={"Authorization": _admin_token()},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["perspective"]["headline"] == "ESG Analyst view of audit article"


def test_super_admin_can_fetch_full_insight_payload(_seeded_article):
    """Without the perspective query param, the full payload should come back
    so the ESG Analyst-style detailed view has everything available."""
    client = TestClient(app)
    r = client.get(
        f"/api/insights/{_seeded_article}",
        headers={"Authorization": _admin_token()},
    )
    assert r.status_code == 200
    body = r.json()
    assert "index" in body
    assert "payload" in body
    assert body["payload"]["article"]["id"] == _seeded_article
    # All 3 perspectives are in the payload
    assert set(body["payload"]["perspectives"].keys()) == {"cfo", "ceo", "esg-analyst"}


# ---------------------------------------------------------------------------
# 4. Regression guards: client tokens still blocked from admin/share
# ---------------------------------------------------------------------------


def test_client_user_blocked_from_admin_tenants_regression():
    client = TestClient(app)
    r = client.get("/api/admin/tenants", headers={"Authorization": _client_token()})
    assert r.status_code == 403


def test_client_user_blocked_from_share_regression():
    client = TestClient(app)
    r = client.post(
        "/api/news/any-article/share",
        headers={"Authorization": _client_token()},
        json={"recipient_email": "target@example.com"},
    )
    assert r.status_code == 403


def test_super_admin_not_blocked_from_share():
    """Sales admin MUST be able to hit share (even if the article doesn't
    exist — they get 404, not 403. That's the signal that auth passed)."""
    client = TestClient(app)
    r = client.post(
        "/api/news/nonexistent_article/share",
        headers={"Authorization": _admin_token()},
        json={"recipient_email": "target@example.com"},
    )
    # 404 (article not found) — means we got through auth
    # (403 would mean blocked, which is the regression we're guarding against)
    assert r.status_code in (400, 404), (
        f"Expected 404/400 from admin hitting share with bad article_id, got {r.status_code}: {r.text}"
    )
