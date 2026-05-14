"""Phase 25 W6 — yfinance ticker disambiguator for ambiguous Deal Names.

The HubSpot CSV's Deal Names are commercial labels, not stock tickers
or registered legal names. Some are unambiguous ("Tagros Chemicals" →
TAGROS.NS) but many are not:

  * "JSW" — could be JSW Steel (JSWSTEEL.NS), JSW Energy (JSWENERGY.NS),
    JSW Cement (private), JSW Infrastructure (JSWINFRA.NS), JSW Holdings
  * "Sutherland" — Sutherland Global Services (private), Sutherland
    Mortgage (US), Sutherland Industries (UK)
  * "MAHLE GmbH" — could be MAHLE Behr, MAHLE Industries, MAHLE Filter
    Systems (different subsidiaries)
  * "DRT-Anthea" — joint venture, no single ticker

The disambiguator returns up to N candidate matches with confidence
scores; the batch admin UI surfaces these for manual selection rather
than letting the company onboarder silently pick the first hit (which
in JSW's case has historically resolved to JSW Steel even when the
deal was for JSW Energy).

A confidence ≥ 0.85 is treated as auto-resolvable (no manual step).
Below that threshold, the candidate list is surfaced for analyst
review via the batch onboarding UI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Known-ambiguous Deal Names — pre-curated for the 17 customer roster.
# Adding entries here is cheaper than relying on yfinance similarity heuristics.
# ---------------------------------------------------------------------------

# Deal-name token (lowercased, after CSV cleaning) → list of plausible ticker
# candidates with display names. The disambiguator surfaces these directly
# without hitting yfinance for known-ambiguous names.
KNOWN_AMBIGUOUS: dict[str, list[tuple[str, str, str]]] = {
    # tuple: (ticker, display_name, industry_hint)
    "jsw": [
        ("JSWSTEEL.NS", "JSW Steel Limited", "Steel"),
        ("JSWENERGY.NS", "JSW Energy Limited", "Power/Energy"),
        ("JSWINFRA.NS", "JSW Infrastructure Limited", "Infrastructure"),
        ("JSWHL.NS", "JSW Holdings Limited", "Diversified"),
        # JSW Cement is private — flag for manual entry without ticker
    ],
    "sutherland": [
        # Sutherland Global Services is private; primary likely entity
        ("PRIVATE:sutherland-global", "Sutherland Global Services (Private)", "Information Technology"),
    ],
    "schaeffler": [
        ("SCHAEFFLER.NS", "Schaeffler India Limited", "Auto Parts"),
        ("SHA.DE", "Schaeffler AG (Germany parent)", "Auto Parts"),
    ],
    "mahle": [
        # MAHLE GmbH is private German group; India sub is unlisted
        ("PRIVATE:mahle-gmbh", "MAHLE GmbH (Private German group)", "Auto Parts"),
        ("PRIVATE:mahle-india", "MAHLE Anand Filter Systems India Pvt Ltd", "Auto Parts"),
    ],
    "drt-anthea": [
        # DRT-Anthea is a joint venture between Anthea Aromatics + DRT
        ("ANTHEAARO.NS", "Anthea Aromatics (DRT-Anthea JV partner)", "Chemicals"),
    ],
    "catasynth": [
        # Catasynth is private — fertiliser / specialty chemicals
        ("PRIVATE:catasynth", "Catasynth Speciality Chemicals (Private)", "Chemicals"),
    ],
    "alembic real estate": [
        ("APLLTD.NS", "Alembic Pharmaceuticals Limited (real estate is a separate arm)", "Real Estate"),
    ],
    "mahapreit": [
        # MAHAPREIT = Maharashtra State Co-op Tribal Federation — government entity
        ("PRIVATE:mahapreit", "Maharashtra Tribal Co-op Federation (Government)", "Other / General"),
    ],
    "nrb bearings": [
        ("NRBBEARING.NS", "NRB Bearings Limited", "Auto Parts"),
    ],
    "nrb": [
        # When stripped to "NRB" via the suffix-stripper, prefer NRB Bearings
        ("NRBBEARING.NS", "NRB Bearings Limited", "Auto Parts"),
    ],
    "tata autocomp systems": [
        # Tata AutoComp is unlisted — part of Tata group
        ("PRIVATE:tata-autocomp", "Tata AutoComp Systems (Tata Group, Private)", "Auto Parts"),
    ],
    "anthem bioscience": [
        # Anthem Biosciences IPO listed September 2025
        ("ANTHEM.NS", "Anthem Biosciences Limited", "Pharmaceuticals"),
    ],
    "tagros chemicals": [
        # Tagros is a Chennai-based agrochemicals company — private
        ("PRIVATE:tagros", "Tagros Chemicals India Pvt Ltd (Private)", "Chemicals"),
    ],
    "rpg lifescience": [
        ("RPGLIFE.NS", "RPG Life Sciences Limited", "Pharmaceuticals"),
    ],
    "daimler india": [
        # Daimler India CV / PCF / ESG Reporting — all private subsidiary
        ("PRIVATE:daimler-india-cv", "Daimler India Commercial Vehicles (Private)", "Automotive"),
    ],
    "sud-chemie india": [
        # Süd-Chemie India = Clariant subsidiary; private in India
        ("PRIVATE:sud-chemie-india", "Sud-Chemie India Pvt Ltd (Clariant subsidiary)", "Chemicals"),
    ],
}


@dataclass
class TickerCandidate:
    """One possible match for an ambiguous Deal Name."""
    ticker: str           # e.g. "JSWSTEEL.NS" or "PRIVATE:foo" for unlisted
    display_name: str     # human-readable
    industry_hint: str    # one of the canonical industries (for materiality defaults)
    confidence: float     # 0.0-1.0 — similarity score
    is_private: bool      # True when ticker starts with "PRIVATE:" (no yfinance lookup possible)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "display_name": self.display_name,
            "industry_hint": self.industry_hint,
            "confidence": self.confidence,
            "is_private": self.is_private,
        }


def disambiguate(
    company_name: str,
    *,
    confidence_threshold: float = 0.85,
    max_candidates: int = 5,
) -> tuple[bool, list[TickerCandidate]]:
    """Return ``(needs_review, candidates)``.

    ``needs_review`` is True when:
      * The cleaned name appears in ``KNOWN_AMBIGUOUS`` (multiple
        candidates returned), OR
      * The top yfinance match's similarity score < ``confidence_threshold``

    The batch admin UI uses this to gate auto-onboarding: candidates
    with ``needs_review=False`` go straight through; the rest land in a
    review queue with the candidate list.

    NOTE: this resolver does NOT call yfinance (network-bound, slow).
    It returns the local KNOWN_AMBIGUOUS lookup OR a single best-guess
    candidate built from the input name. The actual yfinance ticker
    resolution happens later in ``engine.ingestion.company_onboarder``
    once the analyst has confirmed which ticker to use.
    """
    key = (company_name or "").strip().lower()

    # 1. Exact match in the known-ambiguous catalogue
    if key in KNOWN_AMBIGUOUS:
        candidates = [
            TickerCandidate(
                ticker=tk,
                display_name=name,
                industry_hint=industry,
                confidence=_similarity(company_name, name),
                is_private=tk.startswith("PRIVATE:"),
            )
            for tk, name, industry in KNOWN_AMBIGUOUS[key][:max_candidates]
        ]
        # Sort by confidence DESC
        candidates.sort(key=lambda c: -c.confidence)
        # Multiple candidates → needs review (analyst must pick).
        # Private companies always need review (no yfinance ticker to
        # verify; analyst confirms the unlisted entity matches).
        needs_review = len(candidates) > 1 or any(c.is_private for c in candidates)
        return needs_review, candidates

    # 2. Substring match — covers cases like "Tata AutoComp Systems" being
    #    looked up after the suffix stripper trimmed " - New Deal".
    for known_key, candidates_list in KNOWN_AMBIGUOUS.items():
        if known_key in key or key in known_key:
            candidates = [
                TickerCandidate(
                    ticker=tk,
                    display_name=name,
                    industry_hint=industry,
                    confidence=_similarity(company_name, name),
                    is_private=tk.startswith("PRIVATE:"),
                )
                for tk, name, industry in candidates_list[:max_candidates]
            ]
            candidates.sort(key=lambda c: -c.confidence)
            needs_review = len(candidates) > 1 or any(c.is_private for c in candidates)
            return needs_review, candidates

    # 3. Not in catalogue — return a low-confidence placeholder candidate
    #    so the analyst sees the name in the review queue and can either
    #    type a ticker manually or trigger the existing onboarder path.
    placeholder = TickerCandidate(
        ticker="UNKNOWN",
        display_name=company_name,
        industry_hint="Other / General",
        confidence=0.0,
        is_private=False,
    )
    return True, [placeholder]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _similarity(a: str, b: str) -> float:
    """Rough similarity score using Python's stdlib SequenceMatcher.
    Used as a tiebreaker between ambiguous candidates; not a replacement
    for the actual yfinance lookup."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def is_known_ambiguous(company_name: str) -> bool:
    """True if the name is in the curated ambiguity catalogue.
    Useful for the admin UI to flag rows that will need manual review."""
    key = (company_name or "").strip().lower()
    if key in KNOWN_AMBIGUOUS:
        return True
    for known_key in KNOWN_AMBIGUOUS:
        if known_key in key or key in known_key:
            return True
    return False
