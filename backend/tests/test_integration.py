"""Comprehensive integration, edge-case, and API-contract tests for Snowkap ESG.

Covers QA Plan Phases:
  2.2 — API contract tests (every endpoint, valid/invalid inputs)
  2.3 — Edge case tests (boundary inputs, Unicode, filters)
  2.4 — Error resilience (graceful degradation, concurrency)
  2.6 — Response shape verification (JSON structure contracts)
  2.7 — Tenant isolation + performance smoke tests

Run:
  cd snowkap-esg && python -m pytest backend/tests/test_integration.py -v

Requires: live server at http://localhost:8000
"""

import asyncio
import time

import httpx
import pytest

BASE_URL = "http://localhost:8000"
TIMEOUT = 15

# --- Tenant login payloads ---

ADANI_LOGIN = {
    "email": "integration@adanipower.com",
    "name": "Integration Tester",
    "designation": "CEO",
    "company_name": "Adani Power",
    "domain": "adanipower.com",
}

ICICI_LOGIN = {
    "email": "integration@icicibank.com",
    "name": "ICICI Tester",
    "designation": "CEO",
    "company_name": "ICICI Bank",
    "domain": "icicibank.com",
}


# ================================================================
# Fixtures
# ================================================================


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def adani_token(event_loop):
    """Obtain an Adani Power tenant JWT for authenticated tests."""

    async def _get():
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post("/api/auth/login", json=ADANI_LOGIN)
            assert r.status_code == 200, f"Adani login failed: {r.text[:300]}"
            return r.json()["token"]

    return event_loop.run_until_complete(_get())


@pytest.fixture(scope="module")
def adani_headers(adani_token):
    return {"Authorization": f"Bearer {adani_token}"}


@pytest.fixture(scope="module")
def adani_login_data(event_loop):
    """Full login response payload for Adani Power."""

    async def _get():
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post("/api/auth/login", json=ADANI_LOGIN)
            assert r.status_code == 200
            return r.json()

    return event_loop.run_until_complete(_get())


@pytest.fixture(scope="module")
def icici_token(event_loop):
    """Obtain an ICICI Bank tenant JWT."""

    async def _get():
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post("/api/auth/login", json=ICICI_LOGIN)
            assert r.status_code == 200, f"ICICI login failed: {r.text[:300]}"
            return r.json()["token"]

    return event_loop.run_until_complete(_get())


@pytest.fixture(scope="module")
def icici_headers(icici_token):
    return {"Authorization": f"Bearer {icici_token}"}


@pytest.fixture(scope="module")
def analyst_headers(event_loop):
    """Non-admin analyst user — should be denied admin endpoints."""

    async def _get():
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post(
                "/api/auth/login",
                json={
                    "email": "analyst-int@adanipower.com",
                    "name": "Analyst Int",
                    "designation": "ESG Analyst",
                    "company_name": "Adani Power",
                    "domain": "adanipower.com",
                },
            )
            assert r.status_code == 200
            return {"Authorization": f"Bearer {r.json()['token']}"}

    return event_loop.run_until_complete(_get())


# ================================================================
# Class 1: TestAPIContracts
# ================================================================


class TestAPIContracts:
    """Phase 2.2 — Every endpoint with valid/invalid inputs."""

    # --- POST /api/auth/resolve-domain ---

    @pytest.mark.asyncio
    async def test_resolve_domain_valid(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post("/api/auth/resolve-domain", json={"domain": "adanipower.com"})
            assert r.status_code == 200
            data = r.json()
            assert data["domain"] == "adanipower.com"
            assert "is_existing" in data

    @pytest.mark.asyncio
    async def test_resolve_domain_empty_body(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post("/api/auth/resolve-domain", json={})
            assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_resolve_domain_missing_domain_key(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post("/api/auth/resolve-domain", json={"name": "test"})
            assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_resolve_domain_extra_fields_ignored(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post(
                "/api/auth/resolve-domain",
                json={"domain": "adanipower.com", "extra_field": "should_be_ignored"},
            )
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_resolve_domain_personal_email_rejected(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post("/api/auth/resolve-domain", json={"domain": "gmail.com"})
            assert r.status_code == 400
            assert "personal" in r.json()["detail"].lower()

    # --- POST /api/auth/login ---

    @pytest.mark.asyncio
    async def test_login_valid(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post("/api/auth/login", json=ADANI_LOGIN)
            assert r.status_code == 200
            data = r.json()
            assert "token" in data
            assert "user_id" in data
            assert "tenant_id" in data

    @pytest.mark.asyncio
    async def test_login_missing_email(self):
        payload = {k: v for k, v in ADANI_LOGIN.items() if k != "email"}
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post("/api/auth/login", json=payload)
            assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_login_missing_domain(self):
        payload = {k: v for k, v in ADANI_LOGIN.items() if k != "domain"}
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post("/api/auth/login", json=payload)
            assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_login_personal_email_domain_rejected(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post(
                "/api/auth/login",
                json={
                    "email": "user@gmail.com",
                    "name": "Test",
                    "designation": "CEO",
                    "company_name": "Gmail Co",
                    "domain": "gmail.com",
                },
            )
            assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_login_email_domain_mismatch_rejected(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post(
                "/api/auth/login",
                json={
                    "email": "user@other.com",
                    "name": "Test",
                    "designation": "CEO",
                    "company_name": "Adani Power",
                    "domain": "adanipower.com",
                },
            )
            assert r.status_code == 400
            assert "domain" in r.json()["detail"].lower()

    # --- POST /api/auth/returning-user ---

    @pytest.mark.asyncio
    async def test_returning_user_valid(self):
        """Returning user login for an email that has already been created."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            # First ensure the user exists via full login
            await c.post("/api/auth/login", json=ADANI_LOGIN)
            # Then returning-user login
            r = await c.post(
                "/api/auth/returning-user",
                json={"email": ADANI_LOGIN["email"]},
            )
            assert r.status_code == 200
            assert "token" in r.json()

    @pytest.mark.asyncio
    async def test_returning_user_nonexistent_email(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post(
                "/api/auth/returning-user",
                json={"email": "nonexistent-xyz-99@unknowndomain.com"},
            )
            assert r.status_code == 400
            assert "no account" in r.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_returning_user_empty_email(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post("/api/auth/returning-user", json={"email": ""})
            assert r.status_code == 422

    # --- GET /api/news/home ---

    @pytest.mark.asyncio
    async def test_news_home_valid(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/home", headers=adani_headers)
            assert r.status_code == 200
            data = r.json()
            assert "articles" in data
            assert "total" in data

    @pytest.mark.asyncio
    async def test_news_home_no_auth(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/home")
            assert r.status_code == 401

    # --- GET /api/news/feed ---

    @pytest.mark.asyncio
    async def test_news_feed_valid(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/feed", headers=adani_headers)
            assert r.status_code == 200
            data = r.json()
            assert "articles" in data
            assert "total" in data

    @pytest.mark.asyncio
    async def test_news_feed_filter_pillar_E(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/feed?pillar=E", headers=adani_headers)
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_news_feed_sort_by_priority(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/feed?sort_by=priority", headers=adani_headers)
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_news_feed_sort_by_recency(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/feed?sort_by=recency", headers=adani_headers)
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_news_feed_content_type_regulatory(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get(
                "/api/news/feed?content_type=regulatory", headers=adani_headers
            )
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_news_feed_no_auth(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/feed")
            assert r.status_code == 401

    # --- GET /api/news/stats ---

    @pytest.mark.asyncio
    async def test_news_stats_valid(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/stats", headers=adani_headers)
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_news_stats_no_auth(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/stats")
            assert r.status_code == 401

    # --- GET /api/companies ---

    @pytest.mark.asyncio
    async def test_companies_valid(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/companies", headers=adani_headers)
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_companies_no_auth(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/companies")
            assert r.status_code == 401

    # --- GET /api/predictions/stats ---

    @pytest.mark.asyncio
    async def test_predictions_stats_valid(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/predictions/stats", headers=adani_headers)
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_predictions_stats_no_auth(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/predictions/stats")
            assert r.status_code == 401

    # --- GET /api/preferences ---

    @pytest.mark.asyncio
    async def test_preferences_get_valid(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/preferences", headers=adani_headers)
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_preferences_get_no_auth(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/preferences")
            assert r.status_code == 401

    # --- PUT /api/preferences ---

    @pytest.mark.asyncio
    async def test_preferences_put_valid_update(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.put(
                "/api/preferences",
                headers=adani_headers,
                json={
                    "preferred_frameworks": ["BRSR", "GRI"],
                    "preferred_pillars": ["E"],
                    "alert_threshold": 80,
                },
            )
            assert r.status_code == 200
            data = r.json()
            assert "BRSR" in data["preferred_frameworks"]

    @pytest.mark.asyncio
    async def test_preferences_put_invalid_types(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.put(
                "/api/preferences",
                headers=adani_headers,
                json={"preferred_frameworks": "not_a_list"},
            )
            assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_preferences_put_oversized_lists(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.put(
                "/api/preferences",
                headers=adani_headers,
                json={"preferred_pillars": [f"pillar_{i}" for i in range(100)]},
            )
            # Should reject: max_length=10 on preferred_pillars
            assert r.status_code == 422

    # --- GET /api/ontology/stats ---

    @pytest.mark.asyncio
    async def test_ontology_stats_valid(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/ontology/stats", headers=adani_headers)
            assert r.status_code == 200

    # --- GET /api/admin/tenants ---

    @pytest.mark.asyncio
    async def test_admin_tenants_as_non_admin_returns_403(self, analyst_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/admin/tenants", headers=analyst_headers)
            assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_tenants_no_auth_returns_401(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/admin/tenants")
            assert r.status_code == 401

    # --- GET /api/auth/me ---

    @pytest.mark.asyncio
    async def test_auth_me_valid(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/auth/me", headers=adani_headers)
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_auth_me_no_auth(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/auth/me")
            assert r.status_code == 401

    # --- GET /api/ftux/state ---

    @pytest.mark.asyncio
    async def test_ftux_state_valid(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/ftux/state", headers=adani_headers)
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_ftux_state_no_auth(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/ftux/state")
            assert r.status_code == 401

    # --- POST /api/news/{article_id}/bookmark ---

    @pytest.mark.asyncio
    async def test_bookmark_nonexistent_article(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post(
                "/api/news/nonexistent-article-id-xyz/bookmark",
                headers=adani_headers,
                json={"bookmarked": True},
            )
            assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_bookmark_valid_article(self, adani_headers):
        """Bookmark an actual article if one exists; otherwise verify 404 handling."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            feed = await c.get("/api/news/feed?limit=1", headers=adani_headers)
            articles = feed.json().get("articles", [])
            if articles:
                article_id = articles[0]["id"]
                r = await c.post(
                    f"/api/news/{article_id}/bookmark",
                    headers=adani_headers,
                    json={"bookmarked": True},
                )
                assert r.status_code == 200
                assert r.json()["bookmarked"] is True
            else:
                # No articles to bookmark — just verify the 404 case works
                r = await c.post(
                    "/api/news/no-article/bookmark",
                    headers=adani_headers,
                    json={"bookmarked": True},
                )
                assert r.status_code == 404

    # --- Wrong HTTP methods ---

    @pytest.mark.asyncio
    async def test_get_to_login_endpoint_returns_error(self):
        """GET to POST-only endpoint returns 404 or 405 (FastAPI routing behavior)."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/auth/login")
            assert r.status_code in (404, 405)

    @pytest.mark.asyncio
    async def test_post_to_feed_endpoint_returns_405(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post(
                "/api/news/feed", headers=adani_headers, json={}
            )
            assert r.status_code == 405

    @pytest.mark.asyncio
    async def test_delete_to_feed_endpoint_returns_405(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.delete("/api/news/feed", headers=adani_headers)
            assert r.status_code == 405


# ================================================================
# Class 2: TestEdgeCases
# ================================================================


class TestEdgeCases:
    """Phase 2.3 — Boundary and unusual inputs."""

    @pytest.mark.asyncio
    async def test_login_with_maximum_length_name(self):
        """Very long name should be truncated, not crash the server."""
        long_name = "A" * 500
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post(
                "/api/auth/login",
                json={**ADANI_LOGIN, "email": "longname@adanipower.com", "name": long_name},
            )
            assert r.status_code == 200
            # Name should be stored (possibly truncated but no crash)
            assert "token" in r.json()

    @pytest.mark.asyncio
    async def test_login_with_unicode_emoji_name(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post(
                "/api/auth/login",
                json={**ADANI_LOGIN, "email": "emoji@adanipower.com", "name": "Test User \U0001F600\U0001F680"},
            )
            assert r.status_code == 200
            assert "token" in r.json()

    @pytest.mark.asyncio
    async def test_login_with_special_chars_in_designation(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post(
                "/api/auth/login",
                json={
                    **ADANI_LOGIN,
                    "email": "special-desig@adanipower.com",
                    "designation": "VP, Strategy & Operations (Global)",
                },
            )
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_feed_with_limit_1_minimum(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/feed?limit=1", headers=adani_headers)
            assert r.status_code == 200
            articles = r.json()["articles"]
            assert len(articles) <= 1

    @pytest.mark.asyncio
    async def test_feed_with_limit_200_maximum(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/feed?limit=200", headers=adani_headers)
            assert r.status_code == 200
            assert len(r.json()["articles"]) <= 200

    @pytest.mark.asyncio
    async def test_feed_with_offset_beyond_all_articles(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/feed?offset=99999", headers=adani_headers)
            assert r.status_code == 200
            # Should return empty articles list, not an error
            assert r.json()["articles"] == []

    @pytest.mark.asyncio
    async def test_feed_with_all_filter_combinations(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get(
                "/api/news/feed?pillar=E&sort_by=recency&content_type=regulatory",
                headers=adani_headers,
            )
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_resolve_domain_with_subdomain(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post(
                "/api/auth/resolve-domain",
                json={"domain": "sub.domain.com"},
            )
            # Subdomain format is valid per regex
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_resolve_domain_with_hyphenated_domain(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post(
                "/api/auth/resolve-domain",
                json={"domain": "my-company.co.uk"},
            )
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_login_with_plus_alias_email(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post(
                "/api/auth/login",
                json={
                    **ADANI_LOGIN,
                    "email": "user+test@adanipower.com",
                },
            )
            assert r.status_code == 200
            assert "token" in r.json()

    @pytest.mark.asyncio
    async def test_stats_returns_correct_counts(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/stats", headers=adani_headers)
            assert r.status_code == 200
            data = r.json()
            assert isinstance(data["total"], int)
            assert data["total"] >= 0
            assert isinstance(data["high_impact_count"], int)
            assert isinstance(data["new_last_24h"], int)
            assert isinstance(data["predictions_count"], int)

    @pytest.mark.asyncio
    async def test_feed_limit_exceeds_maximum_returns_422(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/feed?limit=999", headers=adani_headers)
            assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_feed_negative_offset_returns_422(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/feed?offset=-1", headers=adani_headers)
            assert r.status_code == 422


# ================================================================
# Class 3: TestErrorResilience
# ================================================================


class TestErrorResilience:
    """Phase 2.4 — Graceful degradation."""

    @pytest.mark.asyncio
    async def test_health_check_always_returns_200(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/health")
            assert r.status_code == 200
            assert r.json()["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_multiple_rapid_requests_no_crash(self, adani_headers):
        """Fire 20 rapid requests and verify none return 500."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            tasks = [c.get("/api/news/feed?limit=5", headers=adani_headers) for _ in range(20)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    # Connection errors from rate limiting are acceptable
                    continue
                assert r.status_code != 500, f"Server crashed: {r.text[:200]}"

    @pytest.mark.asyncio
    async def test_concurrent_logins_different_tenants(self):
        """Concurrent logins from Adani and ICICI should both succeed."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r1, r2 = await asyncio.gather(
                c.post("/api/auth/login", json=ADANI_LOGIN),
                c.post("/api/auth/login", json=ICICI_LOGIN),
            )
            assert r1.status_code == 200
            assert r2.status_code == 200
            # Tokens should be different
            assert r1.json()["token"] != r2.json()["token"]

    @pytest.mark.asyncio
    async def test_valid_request_after_invalid_request_still_works(self, adani_headers):
        """Server state should not be corrupted by a bad request."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            # Bad request
            await c.get("/api/news/feed?offset=-1", headers=adani_headers)
            # Good request should still work
            r = await c.get("/api/news/feed?limit=5", headers=adani_headers)
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_options_request_returns_cors_headers(self):
        """OPTIONS preflight should return CORS headers."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.options(
                "/api/news/feed",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "Authorization",
                },
            )
            # CORS middleware should handle this
            assert r.status_code in (200, 204)

    @pytest.mark.asyncio
    async def test_post_to_get_endpoint_returns_405_not_500(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post("/api/news/stats", headers=adani_headers, json={})
            assert r.status_code in (405, 422), f"Expected 405 or 422, got {r.status_code}"

    @pytest.mark.asyncio
    async def test_get_to_post_endpoint_returns_appropriate_error(self):
        """GET to POST-only endpoint returns 404 or 405."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/auth/resolve-domain")
            assert r.status_code in (404, 405)

    @pytest.mark.asyncio
    async def test_invalid_json_body_returns_422(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post(
                "/api/auth/login",
                content="not valid json",
                headers={"Content-Type": "application/json"},
            )
            assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_authorization_header_returns_401(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get(
                "/api/news/feed",
                headers={"Authorization": ""},
            )
            # Empty auth header should be treated as missing
            assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_bearer_token_returns_401(self):
        """Malformed Bearer token (garbage string) should return 401."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get(
                "/api/news/feed",
                headers={"Authorization": "Bearer garbage-not-jwt"},
            )
            assert r.status_code == 401


# ================================================================
# Class 4: TestResponseShapes
# ================================================================


class TestResponseShapes:
    """Phase 2.6 — Verify response JSON structure contracts."""

    @pytest.mark.asyncio
    async def test_login_response_shape(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post("/api/auth/login", json=ADANI_LOGIN)
            assert r.status_code == 200
            data = r.json()
            required_keys = {
                "token", "user_id", "tenant_id", "designation",
                "permissions", "domain",
            }
            for key in required_keys:
                assert key in data, f"Missing key '{key}' in login response"
            assert isinstance(data["token"], str)
            assert isinstance(data["user_id"], str)
            assert isinstance(data["tenant_id"], str)
            assert isinstance(data["permissions"], list)
            assert isinstance(data["domain"], str)

    @pytest.mark.asyncio
    async def test_news_feed_response_shape(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/feed?limit=5", headers=adani_headers)
            assert r.status_code == 200
            data = r.json()
            assert "articles" in data
            assert "total" in data
            assert isinstance(data["articles"], list)
            assert isinstance(data["total"], int)

    @pytest.mark.asyncio
    async def test_article_shape_in_feed(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/feed?limit=5", headers=adani_headers)
            articles = r.json().get("articles", [])
            if articles:
                article = articles[0]
                required_keys = {"id", "title", "source", "url"}
                for key in required_keys:
                    assert key in article, f"Missing '{key}' in article"
                # Optional but expected keys
                optional_keys = {
                    "priority_score", "sentiment_score", "esg_themes",
                    "summary", "esg_pillar", "sentiment",
                }
                for key in optional_keys:
                    assert key in article, f"Missing optional key '{key}' in article"

    @pytest.mark.asyncio
    async def test_news_stats_response_shape(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/stats", headers=adani_headers)
            assert r.status_code == 200
            data = r.json()
            required = {"total", "high_impact_count", "new_last_24h", "predictions_count"}
            for key in required:
                assert key in data, f"Missing '{key}' in stats response"
                assert isinstance(data[key], int), f"'{key}' should be int"

    @pytest.mark.asyncio
    async def test_me_response_shape(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/auth/me", headers=adani_headers)
            assert r.status_code == 200
            data = r.json()
            required = {"user_id", "email", "domain", "tenant_id"}
            for key in required:
                assert key in data, f"Missing '{key}' in /me response"
            # name and designation may be null but must be present
            assert "name" in data
            assert "designation" in data

    @pytest.mark.asyncio
    async def test_companies_response_shape(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/companies", headers=adani_headers)
            assert r.status_code == 200
            data = r.json()
            assert "companies" in data
            assert "total" in data
            assert isinstance(data["companies"], list)
            assert isinstance(data["total"], int)
            if data["companies"]:
                company = data["companies"][0]
                for key in ("id", "name", "slug", "status"):
                    assert key in company, f"Missing '{key}' in company"

    @pytest.mark.asyncio
    async def test_preferences_response_shape(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/preferences", headers=adani_headers)
            assert r.status_code == 200
            data = r.json()
            expected_keys = {
                "preferred_frameworks", "preferred_pillars",
                "preferred_topics", "alert_threshold",
                "content_depth", "companies_of_interest",
                "dismissed_topics",
            }
            for key in expected_keys:
                assert key in data, f"Missing '{key}' in preferences response"

    @pytest.mark.asyncio
    async def test_ontology_stats_response_shape(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/ontology/stats", headers=adani_headers)
            assert r.status_code == 200
            data = r.json()
            expected = {
                "companies", "facilities", "suppliers", "commodities",
                "material_issues", "frameworks", "regulations", "causal_chains",
            }
            for key in expected:
                assert key in data, f"Missing '{key}' in ontology stats"
                assert isinstance(data[key], int)

    @pytest.mark.asyncio
    async def test_predictions_stats_response_shape(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/predictions/stats", headers=adani_headers)
            assert r.status_code == 200
            data = r.json()
            for key in (
                "total_predictions", "avg_confidence",
                "high_risk_count", "completed_count", "pending_count",
            ):
                assert key in data, f"Missing '{key}' in prediction stats"

    @pytest.mark.asyncio
    async def test_ftux_state_response_shape(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/ftux/state", headers=adani_headers)
            assert r.status_code == 200
            data = r.json()
            for key in ("is_active", "completed_steps", "current_step", "total_steps"):
                assert key in data, f"Missing '{key}' in FTUX state"

    @pytest.mark.asyncio
    async def test_health_response_shape(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/health")
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "healthy"
            assert "service" in data
            assert "version" in data

    @pytest.mark.asyncio
    async def test_resolve_domain_response_shape(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post("/api/auth/resolve-domain", json={"domain": "adanipower.com"})
            data = r.json()
            for key in ("domain", "is_existing"):
                assert key in data, f"Missing '{key}' in resolve-domain response"


# ================================================================
# Class 5: TestTenantIsolation
# ================================================================


class TestTenantIsolation:
    """Phase 2.7a — Cross-tenant security."""

    @pytest.mark.asyncio
    async def test_adani_user_cannot_see_icici_articles(self, adani_headers):
        """Adani tenant feed should contain no ICICI-specific articles."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/feed?limit=100", headers=adani_headers)
            assert r.status_code == 200
            articles = r.json()["articles"]
            for article in articles:
                title = (article.get("title") or "").lower()
                # An article mentioning ICICI should not appear in Adani's feed
                # unless it also mentions Adani (cross-industry news)
                if "icici" in title:
                    assert "adani" in title, (
                        f"ICICI article leaked to Adani feed: {article['title']}"
                    )

    @pytest.mark.asyncio
    async def test_icici_user_cannot_see_adani_articles(self, icici_headers):
        """ICICI tenant feed should contain no Adani-specific articles."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.get("/api/news/feed?limit=100", headers=icici_headers)
            assert r.status_code == 200
            articles = r.json()["articles"]
            for article in articles:
                title = (article.get("title") or "").lower()
                if "adani" in title:
                    assert "icici" in title, (
                        f"Adani article leaked to ICICI feed: {article['title']}"
                    )

    @pytest.mark.asyncio
    async def test_each_tenant_companies_returns_only_own_company(
        self, adani_headers, icici_headers
    ):
        """Each tenant's /api/companies should return only their own companies."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            adani_r = await c.get("/api/companies", headers=adani_headers)
            icici_r = await c.get("/api/companies", headers=icici_headers)

            assert adani_r.status_code == 200
            assert icici_r.status_code == 200

            adani_ids = {co["id"] for co in adani_r.json()["companies"]}
            icici_ids = {co["id"] for co in icici_r.json()["companies"]}

            # No overlap in company IDs
            overlap = adani_ids & icici_ids
            assert not overlap, f"Company IDs shared across tenants: {overlap}"

    @pytest.mark.asyncio
    async def test_article_ids_not_accessible_cross_tenant(
        self, adani_headers, icici_headers
    ):
        """Article IDs from one tenant should not be bookmarkable by another."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            # Get an Adani article ID
            adani_feed = await c.get("/api/news/feed?limit=1", headers=adani_headers)
            adani_articles = adani_feed.json().get("articles", [])
            if adani_articles:
                adani_article_id = adani_articles[0]["id"]
                # Try to bookmark it with ICICI token
                r = await c.post(
                    f"/api/news/{adani_article_id}/bookmark",
                    headers=icici_headers,
                    json={"bookmarked": True},
                )
                # Should be 404 because tenant filtering prevents access
                assert r.status_code == 404, (
                    f"ICICI was able to access Adani article {adani_article_id}"
                )

    @pytest.mark.asyncio
    async def test_adani_and_icici_stats_are_independent(
        self, adani_headers, icici_headers
    ):
        """Stats for each tenant should be independently computed."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            adani_stats = await c.get("/api/news/stats", headers=adani_headers)
            icici_stats = await c.get("/api/news/stats", headers=icici_headers)

            assert adani_stats.status_code == 200
            assert icici_stats.status_code == 200

            # Both should return valid stats (may differ)
            assert isinstance(adani_stats.json()["total"], int)
            assert isinstance(icici_stats.json()["total"], int)


# ================================================================
# Class 6: TestPerformance
# ================================================================


class TestPerformance:
    """Phase 2.7b — Basic performance checks."""

    @pytest.mark.asyncio
    async def test_feed_endpoint_responds_under_2_seconds(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            start = time.monotonic()
            r = await c.get("/api/news/feed?limit=50", headers=adani_headers)
            elapsed = time.monotonic() - start
            assert r.status_code == 200
            assert elapsed < 2.0, f"Feed took {elapsed:.2f}s (limit: 2s)"

    @pytest.mark.asyncio
    async def test_login_endpoint_responds_under_3_seconds(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            start = time.monotonic()
            r = await c.post("/api/auth/login", json=ADANI_LOGIN)
            elapsed = time.monotonic() - start
            assert r.status_code == 200
            assert elapsed < 3.0, f"Login took {elapsed:.2f}s (limit: 3s)"

    @pytest.mark.asyncio
    async def test_stats_endpoint_responds_under_1_second(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            start = time.monotonic()
            r = await c.get("/api/news/stats", headers=adani_headers)
            elapsed = time.monotonic() - start
            assert r.status_code == 200
            assert elapsed < 1.0, f"Stats took {elapsed:.2f}s (limit: 1s)"

    @pytest.mark.asyncio
    async def test_10_concurrent_feed_requests_all_succeed(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            tasks = [
                c.get("/api/news/feed?limit=10", headers=adani_headers)
                for _ in range(10)
            ]
            start = time.monotonic()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            elapsed = time.monotonic() - start

            success_count = 0
            for r in results:
                if isinstance(r, Exception):
                    continue
                if r.status_code == 200:
                    success_count += 1
                # Rate limiting (429) is acceptable for concurrent requests
                assert r.status_code in (200, 429), f"Unexpected status: {r.status_code}"

            # At least half should succeed (accounting for rate limiting)
            assert success_count >= 5, (
                f"Only {success_count}/10 concurrent feed requests succeeded"
            )

    @pytest.mark.asyncio
    async def test_home_endpoint_responds_under_2_seconds(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            start = time.monotonic()
            r = await c.get("/api/news/home", headers=adani_headers)
            elapsed = time.monotonic() - start
            assert r.status_code == 200
            assert elapsed < 2.0, f"Home took {elapsed:.2f}s (limit: 2s)"

    @pytest.mark.asyncio
    async def test_health_check_is_fast(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            start = time.monotonic()
            r = await c.get("/api/health")
            elapsed = time.monotonic() - start
            assert r.status_code == 200
            assert elapsed < 0.5, f"Health check took {elapsed:.2f}s (limit: 0.5s)"

    @pytest.mark.asyncio
    async def test_companies_endpoint_responds_under_1_second(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            start = time.monotonic()
            r = await c.get("/api/companies", headers=adani_headers)
            elapsed = time.monotonic() - start
            assert r.status_code == 200
            assert elapsed < 1.0, f"Companies took {elapsed:.2f}s (limit: 1s)"

    @pytest.mark.asyncio
    async def test_preferences_endpoint_responds_under_1_second(self, adani_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            start = time.monotonic()
            r = await c.get("/api/preferences", headers=adani_headers)
            elapsed = time.monotonic() - start
            assert r.status_code == 200
            assert elapsed < 1.0, f"Preferences took {elapsed:.2f}s (limit: 1s)"
