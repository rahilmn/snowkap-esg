"""Phase 25 W6 — CSV batch onboarder regression tests.

Three layers:

  A. ``engine.ingestion.csv_batch_onboarder`` — CSV parse, filter,
     slugify, country normalisation.
  B. ``engine.ingestion.industry_materiality_defaults`` — per-industry
     overrides + TTL fragment generator.
  C. ``engine.ingestion.ticker_disambiguator`` — ambiguous-name catalogue
     + confidence + needs-review gating.

Critical invariant: parsing the REAL HubSpot CSV at the repo root
returns exactly 17 rows (12 Won + 5 Negotiation, all Active). This is
the user-confirmed Phase 25 onboarding scope. If a future CSV export
changes the row count, this test catches it before the batch run.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest


# Real CSV path — committed to repo root. The test is skipped when the
# file is missing (e.g. in a sandboxed CI without the customer data).
REAL_CSV = Path(__file__).resolve().parent.parent.parent / "hubspot-crm-exports-all-deals-2026-05-01.csv"


# ---------------------------------------------------------------------------
# A. csv_batch_onboarder — parse + filter + slugify
# ---------------------------------------------------------------------------


class TestCsvParse:
    @pytest.mark.skipif(not REAL_CSV.exists(),
                        reason=f"real HubSpot CSV not present at {REAL_CSV}")
    def test_real_csv_returns_exactly_17_eligible_rows(self):
        """The user-confirmed Phase 25 scope: 17 customer tenants
        (12 Won + 5 Negotiation, all Active Status='Active').

        If this number changes, the cost projection in Section 6.3 of
        the plan needs revisiting AND the batch UI's progress bar
        denominator needs updating. Either way it's a catch-it-early
        regression."""
        from engine.ingestion.csv_batch_onboarder import parse_csv
        roster = parse_csv(REAL_CSV)
        won = [r for r in roster if r.deal_stage == "Won"]
        negotiation = [r for r in roster if r.deal_stage == "Negotiation"]
        assert len(roster) == 17, (
            f"Phase 25 onboarding scope changed: expected 17 active customer "
            f"deals (12 Won + 5 Negotiation), got {len(roster)} "
            f"({len(won)} Won + {len(negotiation)} Negotiation)"
        )
        assert len(won) == 12
        assert len(negotiation) == 5

    def test_synthetic_csv_with_required_columns(self, tmp_path):
        from engine.ingestion.csv_batch_onboarder import parse_csv
        csv_path = tmp_path / "test.csv"
        csv_path.write_text(
            'Record ID,Deal Name,Region,Deal Stage,Solution,SOW,Active Status,Deal owner,Channel,Amount,Associated Note,Deal Associate,Is closed lost,Associated Note IDs\n'
            '1,Test Won,India,Won,Tech,SOW1,Active,owner@x.com,Direct,100000,note,assoc,false,1\n'
            '2,Test Lost,India,Closed Lost,Tech,SOW2,Active,owner@x.com,Direct,200000,note,assoc,true,2\n'
            '3,Test Inactive,India,Won,Tech,SOW3,Inactive,owner@x.com,Direct,300000,note,assoc,false,3\n'
            '4,Test Negotiation,Mumbai,Negotiation,Tech,SOW4,Active,owner@x.com,Direct,400000,note,assoc,false,4\n',
            encoding="utf-8"
        )
        roster = parse_csv(csv_path)
        # Only Test Won + Test Negotiation pass the filter
        assert len(roster) == 2
        names = {r.deal_name for r in roster}
        assert names == {"Test Won", "Test Negotiation"}

    def test_missing_required_column_raises(self, tmp_path):
        from engine.ingestion.csv_batch_onboarder import parse_csv
        csv_path = tmp_path / "bad.csv"
        # Missing 'Deal Stage' column
        csv_path.write_text(
            'Record ID,Deal Name,Region,Active Status,Amount,Deal owner\n'
            '1,X,India,Active,100,a@x.com\n',
            encoding="utf-8"
        )
        with pytest.raises(ValueError, match="missing required columns"):
            parse_csv(csv_path)

    def test_missing_csv_file_raises(self, tmp_path):
        from engine.ingestion.csv_batch_onboarder import parse_csv
        with pytest.raises(FileNotFoundError):
            parse_csv(tmp_path / "does_not_exist.csv")

    def test_empty_deal_name_skipped(self, tmp_path):
        from engine.ingestion.csv_batch_onboarder import parse_csv
        csv_path = tmp_path / "x.csv"
        csv_path.write_text(
            'Record ID,Deal Name,Region,Deal Stage,Solution,SOW,Active Status,Deal owner,Channel,Amount,Associated Note,Deal Associate,Is closed lost,Associated Note IDs\n'
            '1,,India,Won,T,S,Active,a@x.com,D,100,n,a,false,1\n'
            '2,Real Deal,India,Won,T,S,Active,a@x.com,D,100,n,a,false,1\n',
            encoding="utf-8"
        )
        roster = parse_csv(csv_path)
        assert len(roster) == 1
        assert roster[0].deal_name == "Real Deal"


class TestCleanCompanyName:
    @pytest.mark.parametrize("raw,expected", [
        ("TATA AutoComp Systems - New Deal", "TATA AutoComp Systems"),
        ("MAHLE GmbH - New Deal", "MAHLE GmbH"),
        ("Sajjan - cohizon - New", "Sajjan"),
        ("NRB GHG (Phase 2)", "NRB"),
        ("Daimler India ESG Reporting", "Daimler India"),
        ("Daimler India PCF", "Daimler India PCF"),  # PCF retained per docstring
        ("Süd-Chemie India", "Süd-Chemie India"),
        ("plain name", "plain name"),
    ])
    def test_strip_known_suffixes(self, raw, expected):
        from engine.ingestion.csv_batch_onboarder import clean_company_name
        assert clean_company_name(raw) == expected

    def test_iterative_stripping(self):
        from engine.ingestion.csv_batch_onboarder import clean_company_name
        # Two suffixes back-to-back
        assert clean_company_name("Foo - cohizon - New") == "Foo"


class TestSlugify:
    @pytest.mark.parametrize("name,expected", [
        ("Tata AutoComp Systems", "tata-autocomp-systems"),
        ("MAHLE GmbH", "mahle-gmbh"),
        ("Süd-Chemie India", "sud-chemie-india"),  # diacritic stripped
        ("RPG Lifescience", "rpg-lifescience"),
        ("MMTC-PAMP", "mmtc-pamp"),
        ("Welspun Group - BAPL", "welspun-group-bapl"),
        ("DRT-Anthea", "drt-anthea"),
        ("", ""),
        ("   leading-trailing   ", "leading-trailing"),
        ("multiple   spaces", "multiple-spaces"),
        ("special!@#$%chars", "special-chars"),
    ])
    def test_slugify_cases(self, name, expected):
        from engine.ingestion.csv_batch_onboarder import slugify
        assert slugify(name) == expected

    def test_slug_capped_at_50_chars(self):
        from engine.ingestion.csv_batch_onboarder import slugify
        long_name = "a" * 100
        assert len(slugify(long_name)) <= 50


class TestNormaliseCountry:
    @pytest.mark.parametrize("region,country", [
        ("India", "India"),
        ("Mumbai", "India"),
        ("Gurugram", "India"),
        ("Bengaluru", "India"),
        ("Gujarat", "India"),
        ("Maharashtra", "India"),
        ("Kuwait", "Kuwait"),
        ("Germany", "Germany"),
        ("United States", "United States"),
        ("", "India"),  # CSV default
    ])
    def test_country_normalisation(self, region, country):
        from engine.ingestion.csv_batch_onboarder import normalise_country
        assert normalise_country(region) == country


class TestSummariseRoster:
    def test_summary_breakdown(self):
        from engine.ingestion.csv_batch_onboarder import (
            CustomerRoster, summarise_roster,
        )
        roster = [
            CustomerRoster("1", "A", "A", "a", "Won", "India", "India", 1.0, "x@x"),
            CustomerRoster("2", "B", "B", "b", "Won", "Mumbai", "India", 2.0, "x@x"),
            CustomerRoster("3", "C", "C", "c", "Negotiation", "Kuwait", "Kuwait", 3.0, "x@x"),
        ]
        s = summarise_roster(roster)
        assert s["total"] == 3
        assert s["won"] == 2
        assert s["negotiation"] == 1
        assert "India:2" in s["countries"]
        assert "Kuwait:1" in s["countries"]
        assert "a" in s["slugs"]
        assert "b" in s["slugs"]


# ---------------------------------------------------------------------------
# B. industry_materiality_defaults — overrides + TTL generator
# ---------------------------------------------------------------------------


class TestIndustryMaterialityDefaults:
    def test_known_industries_have_overrides(self):
        from engine.ingestion.industry_materiality_defaults import (
            INDUSTRY_THEME_DEFAULTS, get_overrides_for_industry,
        )
        # 11 industries with non-trivial overrides + 1 catch-all
        for industry in (
            "Cement", "Automotive", "Auto Parts", "Chemicals", "Pharmaceuticals",
            "Information Technology", "Steel", "Power/Energy", "Renewable Energy",
            "Logistics", "Real Estate",
        ):
            overrides = get_overrides_for_industry(industry)
            assert len(overrides) >= 2, f"{industry} has too few overrides"
            for topic, weight, rationale in overrides:
                assert topic.startswith("topic_")
                assert 0.0 <= weight <= 1.0
                assert len(rationale) >= 30, "rationale too short to be useful"

    def test_unknown_industry_returns_empty(self):
        from engine.ingestion.industry_materiality_defaults import (
            get_overrides_for_industry,
        )
        assert get_overrides_for_industry("Made Up Industry") == []
        assert get_overrides_for_industry("Other / General") == []

    def test_list_supported_industries(self):
        from engine.ingestion.industry_materiality_defaults import (
            list_supported_industries,
        )
        industries = list_supported_industries()
        assert "Other / General" not in industries
        assert "Cement" in industries
        assert len(industries) >= 10


class TestExtensionTtlGenerator:
    def test_known_industry_produces_valid_ttl(self):
        from engine.ingestion.industry_materiality_defaults import (
            build_extension_ttl,
        )
        ttl = build_extension_ttl("test-cement-co", "Cement")
        # Has prefix block
        assert "@prefix snowkap:" in ttl
        # Has at least one MaterialityWeight instance
        assert "snowkap:MaterialityWeight" in ttl
        # Cement-specific override surfaces
        assert "topic_water" in ttl
        assert "0.95" in ttl  # cement water weight
        # Tenant slug embedded in URI
        assert "tenant_test-cement-co_water_weight" in ttl

    def test_unknown_industry_produces_empty_extension(self):
        from engine.ingestion.industry_materiality_defaults import (
            build_extension_ttl,
        )
        ttl = build_extension_ttl("acme", "Other / General")
        assert "@prefix snowkap:" in ttl
        assert "No industry-specific overrides" in ttl
        # No actual MaterialityWeight instances — inherits Layer 1
        assert "snowkap:MaterialityWeight" not in ttl

    def test_extension_parses_via_rdflib(self, tmp_path):
        """The generated TTL must be valid Turtle that rdflib can parse."""
        from engine.ingestion.industry_materiality_defaults import (
            build_extension_ttl,
        )
        from rdflib import Graph
        ttl = build_extension_ttl("rdflib-test-tenant", "Pharmaceuticals")
        ttl_path = tmp_path / "ext.ttl"
        ttl_path.write_text(ttl, encoding="utf-8")
        g = Graph()
        g.parse(ttl_path, format="turtle")
        # At least 4 MaterialityWeight overrides for Pharmaceuticals
        assert len(g) >= 4 * 5, f"got only {len(g)} triples, expected ≥20"

    def test_extra_overrides_appended(self):
        from engine.ingestion.industry_materiality_defaults import (
            build_extension_ttl,
        )
        ttl = build_extension_ttl(
            "acme", "Other / General",
            extra_overrides=[("topic_water", 0.5, "Custom override for testing")],
        )
        assert "topic_water" in ttl
        assert "0.50" in ttl


# ---------------------------------------------------------------------------
# C. ticker_disambiguator — ambiguous-name catalogue
# ---------------------------------------------------------------------------


class TestTickerDisambiguator:
    def test_jsw_returns_multiple_candidates(self):
        from engine.ingestion.ticker_disambiguator import disambiguate
        needs_review, candidates = disambiguate("JSW")
        assert needs_review is True, "JSW must surface for manual review"
        assert len(candidates) >= 4
        tickers = {c.ticker for c in candidates}
        assert "JSWSTEEL.NS" in tickers
        assert "JSWENERGY.NS" in tickers

    def test_unambiguous_known_returns_single_high_conf(self):
        from engine.ingestion.ticker_disambiguator import disambiguate
        needs_review, candidates = disambiguate("RPG Lifescience")
        # Single candidate
        assert len(candidates) == 1
        assert candidates[0].ticker == "RPGLIFE.NS"
        # Single candidate → no review needed
        assert needs_review is False

    def test_unknown_name_returns_placeholder(self):
        from engine.ingestion.ticker_disambiguator import disambiguate
        needs_review, candidates = disambiguate("Made Up Company XYZ")
        assert needs_review is True
        assert len(candidates) == 1
        assert candidates[0].ticker == "UNKNOWN"
        assert candidates[0].confidence == 0.0

    def test_substring_match_works(self):
        from engine.ingestion.ticker_disambiguator import disambiguate
        # "Tata AutoComp Systems" should match the "tata autocomp systems" key
        needs_review, candidates = disambiguate("Tata AutoComp Systems")
        assert candidates[0].ticker.startswith("PRIVATE:tata-autocomp")

    def test_private_companies_marked(self):
        from engine.ingestion.ticker_disambiguator import disambiguate
        _, candidates = disambiguate("MAHLE")
        # All MAHLE candidates are private (German GmbH + India sub)
        assert all(c.is_private for c in candidates)

    def test_is_known_ambiguous_helper(self):
        from engine.ingestion.ticker_disambiguator import is_known_ambiguous
        assert is_known_ambiguous("JSW") is True
        assert is_known_ambiguous("Schaeffler") is True
        assert is_known_ambiguous("Random Brand X") is False

    def test_real_csv_disambiguation_coverage(self):
        """For the 17 real customer tenants, count how many surface
        for manual review. Surfaces are expected for: JSW, MAHLE,
        Schaeffler, Tata AutoComp, Daimler, Süd-Chemie India,
        DRT-Anthea, Catasynth, MAHAPREIT, Sutherland, Tagros,
        Anthem Bioscience, Alembic Real Estate, NRB GHG, NRB Bearings.
        That's most of the roster — a real-world signal that the
        disambiguator catches the right cases."""
        if not REAL_CSV.exists():
            pytest.skip(f"real CSV not present at {REAL_CSV}")
        from engine.ingestion.csv_batch_onboarder import parse_csv
        from engine.ingestion.ticker_disambiguator import disambiguate
        roster = parse_csv(REAL_CSV)
        review_needed = 0
        for r in roster:
            needs_review, _ = disambiguate(r.company_name)
            if needs_review:
                review_needed += 1
        # Most rows surface for review — the catalogue is correctly tuned
        assert review_needed >= 10, (
            f"only {review_needed}/17 surface for review — disambiguator may be "
            f"under-flagging ambiguous names"
        )
