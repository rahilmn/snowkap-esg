"""Phase 55 — engine-source accuracy fixes.

Two defects the live IDFC deck exposed (and the ESG review panel confirmed):

  1. A ₹ figure that is REPORTED in the article was stamped "(engine estimate)"
     on the card — the engine failed to honour its own "(from article)" tag.
  2. A recommendation PRESUMED a SEBI LODR Reg 30 disclosure was owed for a
     sub-judice / third-party fraud, where the honest output is "assess
     materiality, disclose only if material".

These fix both at the source so future decks are correct without hand-patching.
"""
from __future__ import annotations

from types import SimpleNamespace

from engine.analysis.unified_analysis import _financial_exposure_block
from engine.analysis.recommendation_engine import (
    Recommendation,
    _soften_presumed_disclosure,
)


def _result(title: str, body: str):
    return SimpleNamespace(title=title, article_content=body, source="HT")


def _insight(fe: str):
    return SimpleNamespace(decision_summary={"financial_exposure": fe}, financial_timeline={})


# --------------------------------------------------------------------------- #
# Fix 1 — a reported ₹ figure is grounded, never "engine estimate"
# --------------------------------------------------------------------------- #
def test_reported_alleged_figure_is_grounded_not_estimate():
    b = _financial_exposure_block(
        _insight("₹83.0 Cr alleged diversion (from article); ~₹140.3 Cr total modeled exposure"),
        _result("CREST ex-project director denied bail in ₹83 crore IDFC First Bank case",
                "A Chandigarh court denied bail in the ₹83 crore alleged fraud at an IDFC branch."),
    )
    assert b["source"] == "from_article"
    assert b["label"] == "₹83 Cr (alleged)"          # sub-judice → alleged
    assert "engine estimate" not in b["label"].lower()


def test_reported_non_subjudice_figure_labelled_reported():
    b = _financial_exposure_block(
        _insight("₹120 Cr penalty (from article)"),
        _result("Company pays ₹120 crore penalty",
                "The regulator imposed a ₹120 crore penalty which the company paid this quarter."),
    )
    assert b["source"] == "from_article"
    assert b["label"] == "₹120 Cr (reported)"         # settled matter → reported


def test_modeled_only_figure_is_not_falsely_grounded():
    # the ₹ figure is the engine's cascade total and is NOT quoted in the
    # article body → it must stay an estimate, never grounded as reported.
    b = _financial_exposure_block(
        _insight("~₹50 Cr total modeled exposure (engine estimate)"),
        _result("Sector climate norms tighten",
                "New emission norms could raise compliance costs across the banking sector."),
    )
    assert b.get("source") != "from_article"
    label = (b.get("label") or "").lower()
    assert "(reported)" not in label and "(alleged)" not in label


def test_body_grounding_works_without_the_from_article_tag():
    # gpt-5 does not always emit "(from article)". A headline figure that IS in
    # the body must still be grounded — this is the deterministic backstop.
    b = _financial_exposure_block(
        _insight("₹83 crore fraud diversion"),  # NO "(from article)" tag
        _result("Court denies bail in ₹83 crore alleged CREST fraud at IDFC branch",
                "The ₹83 crore alleged fraud at the IDFC First Bank branch is sub-judice."),
    )
    assert b["source"] == "from_article"
    assert b["label"] == "₹83 Cr (alleged)"


# --------------------------------------------------------------------------- #
# Fix 3 — a presumed Reg 30 disclosure is softened to a materiality assessment
# --------------------------------------------------------------------------- #
def _rec(title: str, desc: str = "") -> Recommendation:
    return Recommendation(
        title=title, description=desc, type="compliance",
        responsible_party="Company Secretary with Head of Compliance",
        framework_section="SEBI:LODR", deadline="2026-07-05",
        estimated_budget="₹20 lakh", profitability_link="", priority="HIGH",
        urgency="short_term", estimated_impact="High", risk_of_inaction=6,
    )


def test_presumed_reg30_filing_softened_to_assessment():
    r = _rec("File Reg 30 fraud case update")
    _soften_presumed_disclosure(r)
    assert r.title == "Assess SEBI LODR Reg 30 materiality; disclose only if material"
    assert "materiality assessment" in r.description.lower()
    assert "disclose only if" in r.description.lower()


def test_disclose_under_lodr_reg30_softened():
    r = _rec("Disclose fraud under SEBI LODR Reg 30")
    _soften_presumed_disclosure(r)
    assert r.title.startswith("Assess SEBI LODR Reg 30 materiality")


def test_routine_brsr_disclosure_left_untouched():
    r = _rec("File supplementary BRSR Principle 6 disclosure")
    _soften_presumed_disclosure(r)
    assert r.title == "File supplementary BRSR Principle 6 disclosure"  # not a Reg 30 presumption


def test_non_disclosure_action_left_untouched():
    r = _rec("Run a forensic branch-control review")
    _soften_presumed_disclosure(r)
    assert r.title == "Run a forensic branch-control review"


def test_issue_lodr_disclosure_softened():
    # the variant that slipped the first guard ("issue … LODR disclosure")
    r = _rec("Issue sector-risk LODR disclosure")
    _soften_presumed_disclosure(r)
    assert r.title == "Assess SEBI LODR Reg 30 materiality; disclose only if material"


def test_already_assessment_not_reprocessed():
    r = _rec("Assess SEBI LODR Reg 30 materiality; disclose only if material")
    _soften_presumed_disclosure(r)
    assert r.title == "Assess SEBI LODR Reg 30 materiality; disclose only if material"
