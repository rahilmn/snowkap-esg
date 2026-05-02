"""Phase 22.2 — alias mirroring helper, analysed-counter honesty,
and on-demand sentiment-trajectory alias awareness.

Covers (per session plan T4):
  * `sqlite_index.mirror_to_slug(canonical, alias)` makes canonical
    rows visible when queried by alias and returns the row count.
  * `mirror_to_slug` is a no-op when alias == canonical or args are empty.
  * `_background_onboard` only bumps `analysed` for non-rejected
    articles; rejections inflate `attempted` (via `fetched`) but not
    `analysed` — pre-fix a German prospect whose 2 articles were both
    relevance-rejected showed "ready 2/2 analysed" but feed was empty.
  * On-demand sentiment trajectory honours alias mapping (the raw SQL
    in `engine/analysis/on_demand.py` previously bypassed `resolve_slug`).

These pin Phase 22.2 fixes that follow PR #1 (Phase 23 globalise
hosting). Existing Phase 22.1 coverage in
`tests/test_phase22_onboarding_and_gating.py` covers `register_alias`,
`resolve_slug`, and the /api/news/onboarding-status endpoint.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from engine.index import sqlite_index
from engine.index.sqlite_index import DB_PATH


# ---------------------------------------------------------------------------
# Test helpers — direct SQL so we don't drag in the real insight schema.
# ---------------------------------------------------------------------------


def _purge_alias(slug: str) -> None:
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("DELETE FROM slug_aliases WHERE alias = ?", (slug,))
            conn.commit()
    except Exception:
        pass


def _purge_articles(*ids: str) -> None:
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(
            f"DELETE FROM article_index WHERE id IN ({placeholders})", ids
        )
        conn.commit()


def _seed_article(article_id: str, slug: str, title: str = "Seed", json_path: str = "data/outputs/dummy.json") -> None:
    sqlite_index.ensure_schema()
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO article_index (
                id, company_slug, title, source, url, published_at,
                tier, materiality, action, relevance_score, impact_score,
                esg_pillar, primary_theme, content_type, framework_count,
                do_nothing, recommendations_count, json_path, written_at,
                ontology_queries
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article_id, slug, title,
                "test", "https://example.test/x", "2026-04-30T00:00:00Z",
                "HOME", "HIGH", "monitor", 8.0, 7.0,
                "Environment", "Climate", "news", 0,
                0, 0, json_path, "2026-04-30T00:00:00Z",
                0,
            ),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# T1 — mirror_to_slug
# ---------------------------------------------------------------------------


def test_mirror_to_slug_delegates_to_register_alias_and_returns_count():
    """After mirror_to_slug(canonical, alias):
        * Querying the alias slug returns canonical's rows
        * Return value equals the count of canonical rows
    """
    alias = "phase222-mirror-alias"
    canonical = "phase222-mirror-canonical"
    art_a = "phase222-mirror-art-a"
    art_b = "phase222-mirror-art-b"

    _purge_alias(alias)
    _purge_articles(art_a, art_b)

    try:
        # Pre-state: nothing under alias, two rows under canonical.
        _seed_article(art_a, canonical)
        _seed_article(art_b, canonical, title="Second")
        assert sqlite_index.count(company_slug=alias) == 0

        n = sqlite_index.mirror_to_slug(canonical, alias)
        assert n == 2

        # After mirroring: alias-scoped read returns both canonical rows.
        assert sqlite_index.count(company_slug=alias) == 2
        rows = sqlite_index.query_feed(company_slug=alias, limit=10)
        assert {r["id"] for r in rows} == {art_a, art_b}
        # Rows still physically belong to canonical (no duplication).
        assert all(r["company_slug"] == canonical for r in rows)
    finally:
        _purge_alias(alias)
        _purge_articles(art_a, art_b)


def test_mirror_to_slug_noop_for_self_and_empty():
    """Defensive: mirror_to_slug must be a no-op when alias == canonical
    or either arg is empty/None, returning 0 in all cases."""
    assert sqlite_index.mirror_to_slug("same-slug", "same-slug") == 0
    assert sqlite_index.mirror_to_slug("", "alias") == 0
    assert sqlite_index.mirror_to_slug("canonical", "") == 0
    assert sqlite_index.mirror_to_slug(None, "alias") == 0  # type: ignore[arg-type]
    assert sqlite_index.mirror_to_slug("canonical", None) == 0  # type: ignore[arg-type]


def test_mirror_to_slug_idempotent():
    """Calling mirror_to_slug twice with the same args must not raise
    or duplicate anything; the second call is a refresh that should
    return the current canonical count."""
    alias = "phase222-idem-alias"
    canonical = "phase222-idem-canonical"
    art = "phase222-idem-art"
    _purge_alias(alias)
    _purge_articles(art)
    try:
        _seed_article(art, canonical)
        assert sqlite_index.mirror_to_slug(canonical, alias) == 1
        assert sqlite_index.mirror_to_slug(canonical, alias) == 1
        assert sqlite_index.count(company_slug=alias) == 1
    finally:
        _purge_alias(alias)
        _purge_articles(art)


# ---------------------------------------------------------------------------
# T1 — analysed-counter honesty in _background_onboard
# ---------------------------------------------------------------------------


def _stub_article(idx: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"phase222-bg-art-{idx}",
        title=f"Article {idx}",
        content="body",
        summary="summary",
        source="test",
        url=f"https://example.test/{idx}",
        published_at="2026-05-01T00:00:00Z",
        metadata={},
    )


def test_background_onboard_does_not_count_rejected_in_analysed():
    """When _run_article returns rejected=True, `analysed` must NOT
    increment. Pre-fix prospects with all-rejected articles saw
    "2/2 analysed" but an empty feed."""
    from api.routes import admin_onboard
    from engine.models import onboarding_status as os_model

    slug = "phase222-counter-honesty"
    canonical_company = SimpleNamespace(
        slug=slug, name="Phase222 Counter", industry="Test", domain=f"{slug}.example",
    )
    onboard_result = SimpleNamespace(slug=slug, name="Phase222 Counter", industry="Test")

    # Three articles: two rejected, one accepted (HOME-tier).
    fresh = [_stub_article(1), _stub_article(2), _stub_article(3)]
    summaries = [
        SimpleNamespace(rejected=True, tier="REJECTED"),
        SimpleNamespace(rejected=True, tier="REJECTED"),
        SimpleNamespace(rejected=False, tier="HOME"),
    ]
    run_mock = MagicMock(side_effect=summaries)

    # Wipe any pre-existing row.
    with sqlite3.connect(str(DB_PATH)) as conn:
        try:
            conn.execute("DELETE FROM onboarding_status WHERE slug = ?", (slug,))
            conn.commit()
        except sqlite3.OperationalError:
            pass

    try:
        with patch("engine.ingestion.company_onboarder.onboard_company",
                   return_value=onboard_result), \
             patch("engine.config.load_companies",
                   return_value=[canonical_company]), \
             patch("engine.index.tenant_registry.register_tenant"), \
             patch("engine.ingestion.news_fetcher.fetch_for_company",
                   return_value=fresh), \
             patch("engine.main._run_article", run_mock):
            admin_onboard._background_onboard(
                slug=slug,
                name="Phase222 Counter",
                ticker_hint=None,
                domain=None,
                limit=10,
            )

        row = os_model.get(slug)
        assert row is not None, "onboarding_status row should be present"
        d = row.to_dict()
        assert d["state"] == "ready"
        assert d["fetched"] == 3, "fetched counts every article from the fetcher"
        assert d["analysed"] == 1, (
            "analysed must only count non-rejected articles "
            "(was inflating counter pre-Phase-22.1 fix)"
        )
        assert d["home_count"] == 1
        # All three articles were attempted — verifies we didn't short-circuit.
        assert run_mock.call_count == 3
    finally:
        with sqlite3.connect(str(DB_PATH)) as conn:
            try:
                conn.execute("DELETE FROM onboarding_status WHERE slug = ?", (slug,))
                conn.commit()
            except sqlite3.OperationalError:
                pass


# ---------------------------------------------------------------------------
# T1 — _background_onboard must invoke mirror_to_slug on the alias path
# ---------------------------------------------------------------------------


def test_background_onboard_calls_mirror_to_slug_when_slug_changes():
    """When the canonical slug returned by yfinance differs from the
    seed slug ("puma" → "puma-se"), `_background_onboard` MUST invoke
    `sqlite_index.mirror_to_slug(canonical, alias)` so the user's
    alias-bound session sees the canonical's article_index rows.

    Pre-Phase-22.2 the code called `register_alias` directly. The
    behavioural contract — alias session sees canonical rows — is
    identical, but the explicit `mirror_to_slug` call site is what
    the Phase 22.2 plan named, so we pin it directly.
    """
    from api.routes import admin_onboard
    from engine.models import onboarding_status as os_model

    alias_slug = "phase222-mirror-call-alias"
    canonical_slug = "phase222-mirror-call-canonical"
    company = SimpleNamespace(
        slug=canonical_slug, name="Phase222 Mirror Call",
        industry="Test", domain=f"{canonical_slug}.example",
    )
    onboard_result = SimpleNamespace(
        slug=canonical_slug, name="Phase222 Mirror Call", industry="Test",
    )
    fresh = [_stub_article(99)]
    summary = SimpleNamespace(rejected=False, tier="HOME")

    # Wipe any pre-existing rows from prior runs.
    with sqlite3.connect(str(DB_PATH)) as conn:
        try:
            conn.execute("DELETE FROM onboarding_status WHERE slug IN (?, ?)",
                         (alias_slug, canonical_slug))
            conn.commit()
        except sqlite3.OperationalError:
            pass

    mirror_calls = []
    real_mirror = sqlite_index.mirror_to_slug

    def _spy_mirror(canonical: str, alias: str) -> int:
        mirror_calls.append((canonical, alias))
        return real_mirror(canonical, alias)

    try:
        with patch("engine.ingestion.company_onboarder.onboard_company",
                   return_value=onboard_result), \
             patch("engine.config.load_companies", return_value=[company]), \
             patch("engine.index.tenant_registry.register_tenant"), \
             patch("engine.ingestion.news_fetcher.fetch_for_company",
                   return_value=fresh), \
             patch("engine.main._run_article", return_value=summary), \
             patch("engine.index.sqlite_index.mirror_to_slug",
                   side_effect=_spy_mirror):
            admin_onboard._background_onboard(
                slug=alias_slug,
                name="Phase222 Mirror Call",
                ticker_hint=None,
                domain=None,
                limit=10,
            )

        # The single mirror call must use the canonical→alias signature.
        assert mirror_calls == [(canonical_slug, alias_slug)], (
            f"mirror_to_slug call missing or wrong args: {mirror_calls}"
        )
        # Both slugs end in `ready` with mirrored counters so the
        # frontend (polling either) sees the same numbers.
        for s in (alias_slug, canonical_slug):
            row = os_model.get(s)
            assert row is not None and row.to_dict()["state"] == "ready", (
                f"slug={s} should be ready, got {row}"
            )
    finally:
        with sqlite3.connect(str(DB_PATH)) as conn:
            try:
                conn.execute(
                    "DELETE FROM onboarding_status WHERE slug IN (?, ?)",
                    (alias_slug, canonical_slug),
                )
                conn.execute(
                    "DELETE FROM slug_aliases WHERE alias = ?", (alias_slug,)
                )
                conn.commit()
            except sqlite3.OperationalError:
                pass


# ---------------------------------------------------------------------------
# Bonus — on_demand sentiment trajectory must honour alias mapping
# ---------------------------------------------------------------------------


def test_on_demand_imports_resolve_slug_at_module_level():
    """Phase 22.2 — `engine/analysis/on_demand.py` must import
    `resolve_slug` at module scope so it (a) participates in the
    sqlite_index alias contract and (b) is patchable for testing.

    Pre-fix the I6 sentiment-trajectory block bound `company.slug`
    directly to its raw `WHERE company_slug = ?` SQL, silently
    bypassing the alias rewrite for any session whose JWT slug
    differs from the canonical (e.g. "puma" vs "puma-se").
    """
    import engine.analysis.on_demand as od

    assert hasattr(od, "resolve_slug"), (
        "on_demand.py must import resolve_slug at module level; "
        "function-local import bypasses both patchability and the "
        "alias contract for raw SQL callsites."
    )
    # The imported symbol must be the real one from sqlite_index,
    # not a shadowed local reimplementation.
    assert od.resolve_slug is sqlite_index.resolve_slug


def test_on_demand_sentiment_block_binds_resolved_slug_in_source():
    """Backstop: the SQL bind site for the I6 sentiment-trajectory
    block must reference `resolve_slug(company.slug)`, not the bare
    `company.slug`. Catches a future copy-paste that drops the
    rewrite and silently restores the alias-bypass regression.
    """
    import inspect

    import engine.analysis.on_demand as od

    src = inspect.getsource(od)
    assert "resolve_slug(company.slug)" in src, (
        "Phase 22.2 regression: on_demand.py I6 sentiment-trajectory "
        "SQL must bind resolve_slug(company.slug), not company.slug."
    )
    # And the bare `(company.slug,)` bind tuple must NOT appear in the
    # I6 block — that's the regression shape we're guarding against.
    # We allow `company.slug` elsewhere in the file (it's used in many
    # other contexts), but the specific bind tuple form is the smoking
    # gun for the bypass.
    assert "(company.slug,)" not in src.split("# I6: Sentiment trajectory")[1].split("# I7")[0] \
        if "# I6: Sentiment trajectory" in src else True, (
        "Phase 22.2 regression: bare `(company.slug,)` bind appeared "
        "inside the I6 block — should be `(resolve_slug(company.slug),)`."
    )


def test_on_demand_resolve_slug_actually_rewrites_alias_to_canonical():
    """End-to-end: with an alias registered, calling
    `on_demand.resolve_slug(alias)` (the same module-level reference
    used by the I6 SQL bind) returns the canonical slug. Combined
    with the source-level pin above, this guarantees the bypass
    cannot re-emerge silently.
    """
    import engine.analysis.on_demand as od

    alias = "phase222-od-rewrite-alias"
    canonical = "phase222-od-rewrite-canonical"
    _purge_alias(alias)
    try:
        # Pre-state: alias passes through unchanged (no mapping yet).
        assert od.resolve_slug(alias) == alias
        sqlite_index.register_alias(alias, canonical)
        assert od.resolve_slug(alias) == canonical
    finally:
        _purge_alias(alias)
