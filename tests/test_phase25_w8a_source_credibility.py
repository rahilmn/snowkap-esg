"""Phase 25 W8a — source credibility whitelist regression tests."""

from __future__ import annotations

import pytest


class TestSourceCredibilityScore:
    @pytest.mark.parametrize("url,expected", [
        # Tier-1: +2
        ("https://www.bloomberg.com/news/articles/x", 2),
        ("https://reuters.com/business/sustainable-business/y", 2),
        ("https://www.ft.com/content/abc", 2),
        ("https://wsj.com/articles/z", 2),
        # Tier-2 Indian press: +1
        ("https://www.livemint.com/news/india/x", 1),
        ("https://economictimes.indiatimes.com/y", 1),
        ("https://www.business-standard.com/article/z", 1),
        ("https://www.moneycontrol.com/news/", 1),
        # Tier-2 ESG specialist: +1
        ("https://esgtoday.com/x", 1),
        ("https://www.greenbiz.com/article/y", 1),
        ("https://edie.net/news", 1),
        # Tier-2 regulator: +1
        ("https://www.sebi.gov.in/legal/circulars/x", 1),
        ("https://rbi.org.in/Scripts/y", 1),
        # Unknown / aggregator: 0
        ("https://news.google.com/articles/x", 0),
        ("https://www.somerandomsite.com/article", 0),
        ("https://medium.com/@user/post", 0),
    ])
    def test_known_domains(self, url, expected):
        from engine.ingestion.source_credibility import score
        assert score(url) == expected

    def test_subdomain_match(self):
        from engine.ingestion.source_credibility import score
        # news.bloomberg.com → bloomberg.com → tier-1
        assert score("https://news.bloomberg.com/article") == 2
        # subscriber.ft.com → ft.com → tier-1
        assert score("https://subscriber.ft.com/article") == 2

    def test_empty_url_returns_zero(self):
        from engine.ingestion.source_credibility import score
        assert score("") == 0
        assert score("   ") == 0
        assert score(None) == 0  # type: ignore[arg-type]

    def test_malformed_url_returns_zero(self):
        from engine.ingestion.source_credibility import score
        # Should not raise — should return 0
        assert score("not a url") == 0
        assert score("javascript:void(0)") == 0


class TestIsWhitelisted:
    def test_whitelisted_returns_true(self):
        from engine.ingestion.source_credibility import is_whitelisted
        assert is_whitelisted("https://reuters.com/x") is True
        assert is_whitelisted("https://www.livemint.com/y") is True

    def test_non_whitelisted_returns_false(self):
        from engine.ingestion.source_credibility import is_whitelisted
        assert is_whitelisted("https://medium.com/x") is False
        assert is_whitelisted("https://substack.com/y") is False

    def test_empty_returns_false(self):
        from engine.ingestion.source_credibility import is_whitelisted
        assert is_whitelisted("") is False


class TestTierLabel:
    def test_label_for_tier_1(self):
        from engine.ingestion.source_credibility import tier_label
        assert "tier-1" in tier_label(2).lower()

    def test_label_for_tier_2(self):
        from engine.ingestion.source_credibility import tier_label
        assert "tier-2" in tier_label(1).lower()

    def test_label_for_default(self):
        from engine.ingestion.source_credibility import tier_label
        assert "tier-3" in tier_label(0).lower() or "default" in tier_label(0).lower()


class TestListWhitelistedDomains:
    def test_groups_returned(self):
        from engine.ingestion.source_credibility import list_whitelisted_domains
        groups = list_whitelisted_domains()
        assert "tier_1_financial_press" in groups
        assert "tier_2_indian_press" in groups
        assert "tier_2_esg_specialist" in groups
        assert "tier_2_regulators" in groups

    def test_tier_1_has_canonical_publishers(self):
        from engine.ingestion.source_credibility import list_whitelisted_domains
        tier1 = set(list_whitelisted_domains()["tier_1_financial_press"])
        assert "bloomberg.com" in tier1
        assert "reuters.com" in tier1
        assert "ft.com" in tier1
        assert "wsj.com" in tier1

    def test_indian_press_has_mint_and_et(self):
        from engine.ingestion.source_credibility import list_whitelisted_domains
        ip = set(list_whitelisted_domains()["tier_2_indian_press"])
        assert "livemint.com" in ip
        assert "economictimes.indiatimes.com" in ip


class TestSelectorIntegration:
    """W7 article_selector imports W8a lazily — make sure the boost
    actually surfaces in the selector's score output."""

    def test_bloomberg_url_outranks_aggregator(self):
        from dataclasses import dataclass
        from engine.analysis.article_selector import select_top_n_for_pipeline

        @dataclass
        class _Art:
            id: str
            title: str
            content: str = ""
            summary: str = ""
            source: str = "google_news"
            url: str = ""
            published_at: str = "2026-05-01T00:00:00+00:00"
            company_slug: str = "test"

        bloomberg = _Art(
            id="bloomberg",
            title="climate water carbon SEBI",
            url="https://bloomberg.com/article",
        )
        aggregator = _Art(
            id="aggregator",
            title="climate water carbon SEBI",  # IDENTICAL keywords
            url="https://random-blog.com/article",
        )
        # With identical keyword density, Bloomberg should win on credibility
        result = select_top_n_for_pipeline([aggregator, bloomberg], n=1)
        assert result[0].id == "bloomberg"
