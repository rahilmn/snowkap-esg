"""Phase 22 — Login auto-onboarding + super-admin-only cross-tenant view.

Covers:
  * Any corporate login returns the prospect's own company_id (not null)
  * Snowkap-internal logins (super-admins) get company_id=null
  * Snowkap-internal logins do NOT pollute tenant_registry
  * /api/news/feed with company_id omitted → 403 for non-admins
  * /api/news/stats with company_id omitted → 403 for non-admins
  * Both endpoints with explicit company_id → 200 for everyone
  * Both endpoints with company_id omitted → 200 for super-admins
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.auth_context import SUPER_ADMIN_PERMISSIONS, mint_bearer
from api.main import app
from engine.index import tenant_registry
from engine.index.sqlite_index import DB_PATH


@pytest.fixture(autouse=True)
def _jwt_env():
    # Phase 22.3 — clear the in-memory LOGIN_LIMITER between tests so the
    # 5/min cap from a prior test in the same process doesn't trip a
    # later /auth/login with 429.
    from api.rate_limit import LOGIN_LIMITER
    LOGIN_LIMITER.reset()
    with patch.dict(
        "os.environ",
        {
            "JWT_SECRET": "test-secret-xxxxxxxxxxxxxxxxxxxxxx",
            "SNOWKAP_INTERNAL_EMAILS": "sales@snowkap.co.in",
        },
        clear=False,
    ):
        yield
    LOGIN_LIMITER.reset()


def _admin_token() -> str:
    return mint_bearer({
        "sub": "sales@snowkap.co.in",
        "permissions": list(SUPER_ADMIN_PERMISSIONS),
        "company_id": None,
    })


def _client_token(company_id: str = "icici-bank") -> str:
    """Mint a regular-user token bound to the given tenant."""
    return mint_bearer({
        "sub": f"user@{company_id}.test",
        "permissions": ["read", "view_news"],
        "company_id": company_id,
    })


def _purge(domain: str) -> None:
    try:
        slug = tenant_registry._slug_from_domain(domain)
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("DELETE FROM tenant_registry WHERE domain = ?", (domain,))
            try:
                conn.execute("DELETE FROM onboarding_status WHERE slug = ?", (slug,))
            except sqlite3.OperationalError:
                pass  # table not yet created
            conn.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /auth/login — every corporate login lands on its own company
# ---------------------------------------------------------------------------


def test_corporate_login_assigns_own_company_id():
    """A brand-new prospect logging in must get back their own company_id
    so the dashboard auto-scopes to their company on Home."""
    domain = "phase22-prospect-a.test"
    _purge(domain)

    client = TestClient(app)
    with patch("api.routes.admin_onboard._background_onboard"):
        r = client.post(
            "/api/auth/login",
            json={
                "email": f"ceo@{domain}",
                "domain": domain,
                "designation": "ceo",
                "company_name": "Phase 22 Prospect A",
                "name": "Test CEO",
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()

    expected_slug = tenant_registry._slug_from_domain(domain)
    assert body["company_id"] == expected_slug
    assert "super_admin" not in body["permissions"]
    assert tenant_registry.get_tenant(expected_slug) is not None


def test_returning_user_login_also_assigns_own_company_id():
    """Returning-user flow must also populate company_id from the email
    domain — otherwise sign-in via the 'Already have an account' path
    drops the user back into the empty cross-tenant view."""
    domain = "phase22-prospect-b.test"
    _purge(domain)

    client = TestClient(app)
    with patch("api.routes.admin_onboard._background_onboard"):
        r = client.post(
            "/api/auth/returning-user",
            json={"email": f"user@{domain}"},
        )
    assert r.status_code == 200, r.text
    body = r.json()

    expected_slug = tenant_registry._slug_from_domain(domain)
    assert body["company_id"] == expected_slug


def test_target_company_login_uses_curated_slug():
    """If the prospect happens to be one of the 7 hardcoded targets, the
    target slug wins (no need to register, no need to onboard)."""
    client = TestClient(app)
    r = client.post(
        "/api/auth/login",
        json={
            "email": "analyst@icicibank.com",
            "domain": "icicibank.com",
            "designation": "analyst",
            "company_name": "ICICI Bank",
            "name": "Analyst",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["company_id"] == "icici-bank"


def test_super_admin_login_returns_null_company_id():
    """sales@snowkap.co.in is the only allowlisted super-admin; their
    login returns company_id=None so the dashboard defaults to the
    cross-tenant view."""
    client = TestClient(app)
    r = client.post(
        "/api/auth/login",
        json={
            "email": "sales@snowkap.co.in",
            "domain": "snowkap.co.in",
            "designation": "sales",
            "company_name": "Snowkap",
            "name": "Sales Team",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["company_id"] is None
    assert "super_admin" in body["permissions"]


def test_non_allowlisted_snowkap_email_lands_on_own_tenant():
    """Architect-flagged regression: a Snowkap-domain login that is NOT
    on SNOWKAP_INTERNAL_EMAILS must land on its own concrete company —
    NOT the cross-tenant view. Otherwise any @snowkap.co.in employee
    bypasses the super_admin gate just by sharing the company domain."""
    client = TestClient(app)
    with patch("api.routes.admin_onboard._background_onboard"):
        r = client.post(
            "/api/auth/login",
            json={
                "email": "engineer@snowkap.co.in",  # NOT on the allowlist
                "domain": "snowkap.co.in",
                "designation": "engineer",
                "company_name": "Snowkap",
                "name": "Random Engineer",
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["company_id"] is not None, (
        "Non-allowlisted Snowkap user must NOT get the cross-tenant view"
    )
    assert "super_admin" not in body["permissions"]


def test_login_kicks_off_background_onboarding_for_new_prospects():
    """New-prospect login must enqueue the onboarding pipeline so the
    dashboard isn't empty by the time the user reaches Home."""
    domain = "phase22-prospect-c.test"
    _purge(domain)

    client = TestClient(app)
    with patch("api.routes.admin_onboard._background_onboard") as mock_bg:
        r = client.post(
            "/api/auth/login",
            json={
                "email": f"ceo@{domain}",
                "domain": domain,
                "designation": "ceo",
                "company_name": "Phase 22 Prospect C",
                "name": "Test CEO",
            },
        )
    assert r.status_code == 200

    # FastAPI runs BackgroundTasks AFTER the response is sent. With
    # TestClient that happens in the same thread before .post() returns —
    # so by here the patched function has been invoked exactly once.
    assert mock_bg.called, "Background onboarding task was not scheduled"
    call_kwargs = mock_bg.call_args.kwargs
    assert call_kwargs["slug"] == tenant_registry._slug_from_domain(domain)
    assert call_kwargs["domain"] == domain


# ---------------------------------------------------------------------------
# /news/feed + /news/stats — super-admin gate on cross-tenant view
# ---------------------------------------------------------------------------


def test_news_feed_without_company_id_rejects_regular_user():
    """A non-admin token MUST NOT be able to fetch the cross-tenant feed
    by simply omitting company_id. This is the gate that keeps client A
    from seeing client B's analysis."""
    client = TestClient(app)
    r = client.get(
        "/api/news/feed",
        headers={"Authorization": f"Bearer {_client_token()}"},
    )
    assert r.status_code == 403, r.text
    assert "super_admin" in r.json()["detail"].lower()


def test_news_stats_without_company_id_rejects_regular_user():
    client = TestClient(app)
    r = client.get(
        "/api/news/stats",
        headers={"Authorization": f"Bearer {_client_token()}"},
    )
    assert r.status_code == 403, r.text


def test_news_feed_with_company_id_allows_regular_user():
    """Regular users must still be able to see their own company's feed."""
    client = TestClient(app)
    r = client.get(
        "/api/news/feed?company_id=icici-bank",
        headers={"Authorization": f"Bearer {_client_token()}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "articles" in body
    assert "total" in body


def test_news_stats_with_company_id_allows_regular_user():
    client = TestClient(app)
    r = client.get(
        "/api/news/stats?company_id=icici-bank",
        headers={"Authorization": f"Bearer {_client_token()}"},
    )
    assert r.status_code == 200, r.text


def test_news_feed_without_company_id_allows_super_admin():
    """Super-admins ARE allowed to see the cross-tenant feed."""
    client = TestClient(app)
    r = client.get(
        "/api/news/feed",
        headers={"Authorization": f"Bearer {_admin_token()}"},
    )
    assert r.status_code == 200, r.text


def test_news_stats_without_company_id_allows_super_admin():
    client = TestClient(app)
    r = client.get(
        "/api/news/stats",
        headers={"Authorization": f"Bearer {_admin_token()}"},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Slug-enumeration gate — non-admin can only see their own tenant
# ---------------------------------------------------------------------------


def test_news_feed_rejects_other_tenants_slug_enumeration():
    """Architect-flagged hole: a non-admin must NOT be able to read another
    tenant's feed by simply passing `company_id=icici-bank` when their
    own JWT is bound to e.g. `yes-bank`. This is the broken access
    control fix — the JWT carries the user's own slug and the API
    rejects mismatches."""
    client = TestClient(app)
    yes_bank_token = _client_token("yes-bank")
    r = client.get(
        "/api/news/feed?company_id=icici-bank",
        headers={"Authorization": f"Bearer {yes_bank_token}"},
    )
    assert r.status_code == 403, r.text
    assert "cross-tenant" in r.json()["detail"].lower()


def test_news_stats_rejects_other_tenants_slug_enumeration():
    client = TestClient(app)
    yes_bank_token = _client_token("yes-bank")
    r = client.get(
        "/api/news/stats?company_id=icici-bank",
        headers={"Authorization": f"Bearer {yes_bank_token}"},
    )
    assert r.status_code == 403, r.text


def test_news_feed_allows_user_to_see_own_tenant():
    """Sanity: the slug-enumeration gate must not block users from
    reading their OWN tenant's data."""
    client = TestClient(app)
    icici_token = _client_token("icici-bank")
    r = client.get(
        "/api/news/feed?company_id=icici-bank",
        headers={"Authorization": f"Bearer {icici_token}"},
    )
    assert r.status_code == 200, r.text


def test_forged_unsigned_token_cannot_bypass_tenant_scope():
    """Architect-flagged hole: when REQUIRE_SIGNED_JWT=1, an attacker
    cannot forge an unsigned token claiming super_admin or another
    tenant's company_id. The decoder rejects the unsigned token, the
    request reaches the gate with empty claims, and the gate rejects.

    Without strict mode the unsigned-decode fallback in
    `api/auth_context.decode_bearer` would happily accept the forged
    token — production must run with `REQUIRE_SIGNED_JWT=1`."""
    import base64 as _b64
    import json as _json

    with patch.dict("os.environ", {"REQUIRE_SIGNED_JWT": "1"}, clear=False):
        # Build an `alg:none` JWT claiming super_admin + cross-tenant scope
        header = _b64.urlsafe_b64encode(_json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
        payload = _b64.urlsafe_b64encode(_json.dumps({
            "sub": "attacker@evil.test",
            "permissions": ["super_admin"],
            "company_id": None,
            "exp": 9_999_999_999,
        }).encode()).rstrip(b"=").decode()
        forged = f"{header}.{payload}."

        client = TestClient(app)
        # Cross-tenant: must be rejected (unsigned token → empty claims → no super_admin)
        r = client.get(
            "/api/news/feed",
            headers={"Authorization": f"Bearer {forged}"},
        )
        assert r.status_code in (401, 403), r.text
        # Slug enumeration with forged token: also rejected
        r2 = client.get(
            "/api/news/feed?company_id=icici-bank",
            headers={"Authorization": f"Bearer {forged}"},
        )
        assert r2.status_code in (401, 403), r2.text


def test_super_admin_can_query_any_tenant():
    """Super-admins are exempt from the slug-binding check — they can
    scope to any tenant."""
    client = TestClient(app)
    r = client.get(
        "/api/news/feed?company_id=icici-bank",
        headers={"Authorization": f"Bearer {_admin_token()}"},
    )
    assert r.status_code == 200
    r2 = client.get(
        "/api/news/feed?company_id=yes-bank",
        headers={"Authorization": f"Bearer {_admin_token()}"},
    )
    assert r2.status_code == 200


# ---------------------------------------------------------------------------
# Phase 22.1 — alias mirroring + self-service onboarding-status endpoint
# ---------------------------------------------------------------------------


def _purge_alias(slug: str) -> None:
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            try:
                conn.execute("DELETE FROM slug_aliases WHERE alias = ?", (slug,))
            except sqlite3.OperationalError:
                pass
            conn.commit()
    except Exception:
        pass


def test_resolve_slug_unifies_alias_to_canonical():
    """Phase 22.1 — sqlite_index.resolve_slug() must transparently rewrite
    a registered alias to its canonical slug so that user sessions bound
    to the login-time slug ("puma") see articles indexed under the
    canonical slug ("puma-se")."""
    from engine.index import sqlite_index

    alias, canonical = "phase22-alias-test", "phase22-canonical-test"
    _purge_alias(alias)

    # Before registration: alias passes through unchanged
    assert sqlite_index.resolve_slug(alias) == alias

    sqlite_index.register_alias(alias, canonical)
    try:
        assert sqlite_index.resolve_slug(alias) == canonical
        # Canonical itself is unchanged (not a recursion target)
        assert sqlite_index.resolve_slug(canonical) == canonical
        # None passes through
        assert sqlite_index.resolve_slug(None) is None
        # Same-slug self-alias is a no-op (defensive against onboarder edge cases)
        sqlite_index.register_alias(canonical, canonical)
        assert sqlite_index.resolve_slug(canonical) == canonical
    finally:
        _purge_alias(alias)


def test_count_and_query_feed_use_alias_resolution():
    """A query for the alias slug must return rows physically stored under
    the canonical slug — this is the actual user-visible bug fix."""
    from engine.index import sqlite_index

    alias, canonical = "phase22-aliasq", "phase22-canonq"
    article_id = "phase22-aliasq-art1"
    _purge_alias(alias)
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("DELETE FROM article_index WHERE id = ?", (article_id,))
        conn.commit()

    try:
        # Seed an article under canonical slug only. Use direct SQL so
        # the test isn't coupled to the exact insight-payload schema.
        sqlite_index.ensure_schema()
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute(
                """
                INSERT INTO article_index (
                    id, company_slug, title, source, url, published_at,
                    tier, materiality, action, relevance_score, impact_score,
                    esg_pillar, primary_theme, content_type, framework_count,
                    do_nothing, recommendations_count, json_path, written_at,
                    ontology_queries
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article_id, canonical, "Canonical-only article",
                    "test", "https://example.test/x", "2026-04-30T00:00:00Z",
                    "HOME", "HIGH", "monitor", 8.0, 7.0,
                    "Environment", "Climate", "news", 0,
                    0, 0, "data/outputs/dummy.json", "2026-04-30T00:00:00Z",
                    0,
                ),
            )
            conn.commit()

        # Without alias: querying the alias slug returns nothing
        assert sqlite_index.count(company_slug=alias) == 0
        assert sqlite_index.query_feed(company_slug=alias, limit=10) == []

        # After alias registration: query rewrites and returns the canonical row
        sqlite_index.register_alias(alias, canonical)
        assert sqlite_index.count(company_slug=alias) == 1
        rows = sqlite_index.query_feed(company_slug=alias, limit=10)
        assert len(rows) == 1
        assert rows[0]["id"] == article_id
        assert rows[0]["company_slug"] == canonical
    finally:
        _purge_alias(alias)
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("DELETE FROM article_index WHERE id = ?", (article_id,))
            conn.commit()


def test_news_onboarding_status_self_returns_state():
    """A regular user can ask `/api/news/onboarding-status` for their own
    slug and receives the live row from `onboarding_status`. The
    endpoint is NOT super-admin gated (unlike /api/admin/onboard/.../status)."""
    from engine.models import onboarding_status as os_model

    slug = "phase22-status-self"
    os_model.upsert(slug, state="analysing", fetched=5, analysed=2, home_count=1)
    try:
        client = TestClient(app)
        r = client.get(
            f"/api/news/onboarding-status?company_id={slug}",
            headers={"Authorization": f"Bearer {_client_token(slug)}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["slug"] == slug
        assert body["state"] == "analysing"
        assert body["fetched"] == 5
        assert body["analysed"] == 2
        assert body["home_count"] == 1
    finally:
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("DELETE FROM onboarding_status WHERE slug = ?", (slug,))
            conn.commit()


def test_news_onboarding_status_cross_tenant_denied():
    """A regular user MUST NOT be able to read another tenant's onboarding
    progress (slug enumeration). Same gate as /news/feed."""
    client = TestClient(app)
    r = client.get(
        "/api/news/onboarding-status?company_id=icici-bank",
        headers={"Authorization": f"Bearer {_client_token('yes-bank')}"},
    )
    assert r.status_code == 403, r.text


def test_news_onboarding_status_falls_back_to_token_slug():
    """When the caller omits `company_id`, the endpoint should default
    to the JWT's `company_id` claim. A curated tenant with no
    `onboarding_status` row returns `state='ready'` so the frontend
    can treat absence-of-row identically to ready."""
    client = TestClient(app)
    r = client.get(
        "/api/news/onboarding-status",
        headers={"Authorization": f"Bearer {_client_token('icici-bank')}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "icici-bank"
    assert body["state"] == "ready"


# ---------------------------------------------------------------------------
# Task 3 — `tenant_scoped` dependency + `_require_article_in_scope` helper
# applied across legacy_adapter (article detail, agent chat, predictions,
# ontology snapshots, etc.). Phase 22 only gated /news/feed and /news/stats;
# this set of tests pins the regression for the rest of the surface.
# ---------------------------------------------------------------------------


def _seed_article(article_id: str, company_slug: str) -> None:
    """Insert a minimal article_index row owned by `company_slug` so the
    tenant-scope helper has something to resolve."""
    from engine.index import sqlite_index as si

    si.ensure_schema()
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("DELETE FROM article_index WHERE id = ?", (article_id,))
        conn.execute(
            """
            INSERT INTO article_index (
                id, company_slug, title, source, url, published_at,
                tier, materiality, action, relevance_score, impact_score,
                esg_pillar, primary_theme, content_type, framework_count,
                do_nothing, recommendations_count, json_path, written_at,
                ontology_queries
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article_id, company_slug, "Task3 fixture article",
                "test", "https://example.test/task3", "2026-04-30T00:00:00Z",
                "HOME", "HIGH", "monitor", 8.0, 7.0,
                "Environment", "Climate", "news", 0,
                0, 0, "data/outputs/dummy.json", "2026-04-30T00:00:00Z",
                0,
            ),
        )
        conn.commit()


def _purge_article(article_id: str) -> None:
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("DELETE FROM article_index WHERE id = ?", (article_id,))
        conn.commit()


def test_news_analysis_rejects_cross_tenant_article():
    """`/api/news/{id}/analysis` is the highest-value leak surface — it
    returns the full deep-insight payload. A user bound to yes-bank must
    NOT be able to read an article owned by icici-bank by guessing the id."""
    article_id = "task3-analysis-art"
    _seed_article(article_id, "icici-bank")
    try:
        client = TestClient(app)
        r = client.get(
            f"/api/news/{article_id}/analysis",
            headers={"Authorization": f"Bearer {_client_token('yes-bank')}"},
        )
        assert r.status_code == 403, r.text
        # Sanity: the rightful tenant is still let through.
        r_ok = client.get(
            f"/api/news/{article_id}/analysis",
            headers={"Authorization": f"Bearer {_client_token('icici-bank')}"},
        )
        assert r_ok.status_code == 200, r_ok.text
    finally:
        _purge_article(article_id)


def test_news_analysis_status_rejects_cross_tenant_article():
    """The status poller leaks existence-of-article info via the
    pending/unknown distinction — gate it the same way."""
    article_id = "task3-status-art"
    _seed_article(article_id, "icici-bank")
    try:
        client = TestClient(app)
        r = client.get(
            f"/api/news/{article_id}/analysis-status",
            headers={"Authorization": f"Bearer {_client_token('yes-bank')}"},
        )
        assert r.status_code == 403, r.text
    finally:
        _purge_article(article_id)


def test_news_trigger_analysis_rejects_cross_tenant_article():
    """An attacker shouldn't be able to fire (and bill) on-demand OpenAI
    enrichment against another tenant's article."""
    article_id = "task3-trigger-art"
    _seed_article(article_id, "icici-bank")
    try:
        client = TestClient(app)
        r = client.post(
            f"/api/news/{article_id}/trigger-analysis",
            headers={"Authorization": f"Bearer {_client_token('yes-bank')}"},
        )
        assert r.status_code == 403, r.text
    finally:
        _purge_article(article_id)


def test_news_bookmark_rejects_cross_tenant_article():
    """The bookmark endpoint is a no-op on the server, but it still
    accepts an article_id — gate it so the article-id space isn't a
    cross-tenant probe surface."""
    article_id = "task3-bookmark-art"
    _seed_article(article_id, "icici-bank")
    try:
        client = TestClient(app)
        r = client.post(
            f"/api/news/{article_id}/bookmark",
            headers={"Authorization": f"Bearer {_client_token('yes-bank')}"},
        )
        assert r.status_code == 403, r.text
    finally:
        _purge_article(article_id)


def test_news_chat_rejects_cross_tenant_article_and_body_slug():
    """`/news/{id}/chat` carries BOTH an article_id (path) and a
    company_id (body). Either one pointing at another tenant must 403."""
    article_id = "task3-chat-art"
    _seed_article(article_id, "icici-bank")
    try:
        client = TestClient(app)
        # Cross-tenant path-param article → 403 even when body claims own slug.
        r = client.post(
            f"/api/news/{article_id}/chat",
            json={"company_id": "yes-bank", "message": "hi"},
            headers={"Authorization": f"Bearer {_client_token('yes-bank')}"},
        )
        assert r.status_code == 403, r.text
        # Cross-tenant body-param company_id → 403 even when path is own slug.
        own_article = "task3-chat-art-own"
        _seed_article(own_article, "yes-bank")
        try:
            r2 = client.post(
                f"/api/news/{own_article}/chat",
                json={"company_id": "icici-bank", "message": "hi"},
                headers={"Authorization": f"Bearer {_client_token('yes-bank')}"},
            )
            assert r2.status_code == 403, r2.text
        finally:
            _purge_article(own_article)
    finally:
        _purge_article(article_id)


def test_predictions_list_rejects_cross_tenant_slug_enumeration():
    """`/predictions/` accepts a `company_id` query param. Even though
    it currently returns an empty list (Hybrid scope), the gate must be
    in place so the future non-stub implementation inherits it."""
    client = TestClient(app)
    r = client.get(
        "/api/predictions/?company_id=icici-bank",
        headers={"Authorization": f"Bearer {_client_token('yes-bank')}"},
    )
    assert r.status_code == 403, r.text


def test_predictions_list_rejects_omitted_company_id_for_regular_user():
    """Same cross-tenant gate as /news/feed: a non-admin can't fall into
    the cross-tenant view by simply omitting `company_id`."""
    client = TestClient(app)
    r = client.get(
        "/api/predictions/",
        headers={"Authorization": f"Bearer {_client_token('yes-bank')}"},
    )
    assert r.status_code == 403, r.text


def test_predictions_trigger_rejects_cross_tenant_body():
    """`/predictions/trigger` carries company_id + article_id in the body.
    Both must be in the caller's tenant scope."""
    article_id = "task3-pred-trigger-art"
    _seed_article(article_id, "icici-bank")
    try:
        client = TestClient(app)
        r = client.post(
            "/api/predictions/trigger",
            json={"article_id": article_id, "company_id": "icici-bank"},
            headers={"Authorization": f"Bearer {_client_token('yes-bank')}"},
        )
        assert r.status_code == 403, r.text
    finally:
        _purge_article(article_id)


def test_agent_ask_about_news_rejects_cross_tenant_article():
    """The agent endpoint emits the article's causal chains + summary in
    its response — gate so a yes-bank user can't read icici-bank chains."""
    article_id = "task3-agent-ask-art"
    _seed_article(article_id, "icici-bank")
    try:
        client = TestClient(app)
        r = client.post(
            "/api/agent/ask-about-news",
            json={"article_id": article_id},
            headers={"Authorization": f"Bearer {_client_token('yes-bank')}"},
        )
        assert r.status_code == 403, r.text
    finally:
        _purge_article(article_id)


def test_agent_chat_rejects_cross_tenant_article_when_pinned():
    """`/agent/chat` only enforces the article gate when `article_id` is
    set (the field is optional). Pin to another tenant's article → 403;
    omit it → 200 (free-form chat with no tenant context)."""
    article_id = "task3-agent-chat-art"
    _seed_article(article_id, "icici-bank")
    try:
        client = TestClient(app)
        r_blocked = client.post(
            "/api/agent/chat",
            json={"question": "hi", "agent_id": "executive", "article_id": article_id},
            headers={"Authorization": f"Bearer {_client_token('yes-bank')}"},
        )
        assert r_blocked.status_code == 403, r_blocked.text
    finally:
        _purge_article(article_id)


def test_ontology_explore_rejects_cross_tenant_company_slug():
    """`/ontology/explore` returns causal chains anchored on a target
    company. A yes-bank user must not be able to anchor on icici-bank."""
    client = TestClient(app)
    r = client.post(
        "/api/ontology/explore",
        json={"entity_text": "scope3", "company_slug": "icici-bank"},
        headers={"Authorization": f"Bearer {_client_token('yes-bank')}"},
    )
    assert r.status_code == 403, r.text


def test_ontology_explore_defaults_to_token_slug_when_omitted():
    """When the caller omits `company_slug`, the endpoint must default to
    the JWT's tenant — NOT silently fall back to the first hardcoded
    target company (which would have leaked icici-bank to every user)."""
    client = TestClient(app)
    r = client.post(
        "/api/ontology/explore",
        json={"entity_text": "scope3"},
        headers={"Authorization": f"Bearer {_client_token('icici-bank')}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Either the engine returns chains for icici-bank or the empty
    # fallback — but the target field MUST be the caller's own slug.
    assert body.get("target") == "icici-bank"


def test_unknown_article_id_returns_404_not_403():
    """`_require_article_in_scope` must mirror `_load_row_and_payload`'s
    404-on-missing behaviour so existence info isn't leaked through a
    different status code."""
    client = TestClient(app)
    r = client.get(
        "/api/news/task3-does-not-exist/analysis",
        headers={"Authorization": f"Bearer {_client_token('icici-bank')}"},
    )
    assert r.status_code == 404, r.text


def test_super_admin_can_read_any_tenants_article_detail():
    """Super-admins remain exempt from the slug-binding check on
    article-id endpoints, just like on /news/feed."""
    article_id = "task3-admin-art"
    _seed_article(article_id, "icici-bank")
    try:
        client = TestClient(app)
        r = client.get(
            f"/api/news/{article_id}/analysis",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert r.status_code == 200, r.text
    finally:
        _purge_article(article_id)
