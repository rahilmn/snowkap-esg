"""Ask cold-path grounding — a general company question (no article pinned) is
answered from the company's current deck instead of being deflected with
"I don't have that in the current context".

Before: the cold path injected only the peer set, so a question like "what are
IDFC's ESG risks right now?" hit the grounding-discipline wall. Now
_load_company_deck_context pulls the deck's critical items into the prompt.
"""
from __future__ import annotations

from unittest.mock import patch

from api.routes.chat import _load_company_deck_context, _build_system_prompt


_FAKE_ROWS = [
    {
        "tier": "critical",
        "title": "CBI chargesheets 13 accused in ₹83-crore IDFC First Bank scam",
        "personalised_analysis": {
            "why_it_matters": {"criticality_summary": "CBI chargesheet over ~₹83 Cr CREST fund fraud raises governance risk."},
            "what_it_triggers": {"recommended_actions": [{"title": "File SEBI Reg 30 fraud disclosure update"}]},
        },
    },
    {"tier": "light", "title": "Some lighter item", "personalised_analysis": {}},
]


class _Co:
    slug = "idfc-first-bank"
    industry = "Financials/Banking"


def test_deck_context_built_from_critical_items():
    with patch("engine.config.load_companies", return_value=[_Co()]), \
         patch("engine.models.company_article_view.deck_for_company", return_value=(_FAKE_ROWS, {})):
        ctx = _load_company_deck_context("idfc-first-bank")
    assert ctx is not None
    assert "COMPANY DECK CONTEXT" in ctx
    assert "CBI chargesheets" in ctx
    assert "₹83 Cr" in ctx
    assert "File SEBI Reg 30" in ctx  # lead action included


def test_deck_context_none_when_no_rows():
    with patch("engine.config.load_companies", return_value=[_Co()]), \
         patch("engine.models.company_article_view.deck_for_company", return_value=([], {})):
        assert _load_company_deck_context("idfc-first-bank") is None


def test_deck_context_none_for_unknown_company():
    with patch("engine.config.load_companies", return_value=[]):
        assert _load_company_deck_context("nope") is None


def test_system_prompt_includes_deck_block_and_allowlist():
    deck = "COMPANY DECK CONTEXT (idfc-first-bank — ...):\n  - [critical] X"
    sp = _build_system_prompt([], "idfc-first-bank", "ci@snowkap.com", deck_context=deck)
    assert deck in sp
    assert "COMPANY DECK" in sp  # named in the grounding-discipline allowlist
