"""Phase 56.D — anchored ``framework_hit`` on recommendations.

The swipe-up "how this hits your framework" block must be ANCHORED: the
framework / principle / mandatory come from the deterministic ontology layer,
and the LLM writes ONLY the interpretation prose. The model must never be able
to change the framework, the principle, or flip the mandatory flag — even if it
says something contradictory in its prose. These tests pin that contract on the
three building blocks (``_framework_hit_anchor``,
``_generate_framework_interpretation``, ``_stamp_framework_hit``).
"""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace

from engine.analysis.framework_matcher import FrameworkMatch
from engine.analysis.recommendation_engine import (
    Recommendation,
    _framework_hit_anchor,
    _stamp_framework_hit,
)


# ---------------------------------------------------------------------------
# Fixtures / stubs
# ---------------------------------------------------------------------------


def _rec(**over) -> Recommendation:
    vals: dict = {}
    for f in dataclasses.fields(Recommendation):
        t = str(f.type)
        if "dict" in t and "None" in t:        # framework_hit
            vals[f.name] = None
        elif "str" in t:
            vals[f.name] = ""
        elif "int" in t or "float" in t:
            vals[f.name] = 0
        else:
            vals[f.name] = []
    vals.update(over)
    return Recommendation(**vals)


def _result(*, frameworks=None, primary_theme="Emissions", title="Maruti emission norms"):
    return SimpleNamespace(
        frameworks=frameworks or [],
        themes=SimpleNamespace(primary_theme=primary_theme),
        title=title,
        article_id="phase56d-test",
    )


def _company(*, region="INDIA", cap="Large Cap"):
    return SimpleNamespace(
        name="Maruti Suzuki", industry="Automobiles",
        framework_region=region, market_cap=cap,
    )


def _insight():
    return SimpleNamespace(
        headline="Tighter CAFE-III emission norms hit Maruti's ICE fleet",
        decision_summary={"key_risk": "Fleet-average CO2 compliance gap"},
    )


def _brsr_match(mandatory=True, sections=None):
    return FrameworkMatch(
        framework_id="BRSR",
        framework_label="BRSR",
        relevance=0.8,
        is_mandatory=mandatory,
        profitability_link="",
        triggered_sections=sections if sections is not None
        else [{"code": "BRSR:P6", "title": "Principle 6 — Environmental Protection"}],
    )


class _StubLLM:
    """Records every create() call so tests can assert no wasted LLM calls."""

    def __init__(self, content: str = "Maruti must disclose its fleet CO2 gap.") -> None:
        self.calls: list[dict] = []
        self._content = content
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                msg = SimpleNamespace(content=outer._content)
                return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)

        self.sync = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))

    def model_for(self) -> str:
        return "stub-model"


# ---------------------------------------------------------------------------
# _framework_hit_anchor — deterministic facts
# ---------------------------------------------------------------------------


def test_anchor_from_stage6_brsr_match() -> None:
    anchor = _framework_hit_anchor(_result(frameworks=[_brsr_match()]), _company())
    assert anchor is not None
    assert anchor["framework"] == "BRSR"
    assert anchor["principle_code"] == "BRSR:P6"
    assert "Environmental" in anchor["principle_title"]
    assert anchor["mandatory"] is True
    assert anchor["region"] == "INDIA"


def test_anchor_fallback_direct_lookup_when_no_stage6_match() -> None:
    """No BRSR in result.frameworks, but the India theme maps to a principle —
    the direct ontology fallback must still resolve it (and read mandatory)."""
    anchor = _framework_hit_anchor(
        _result(frameworks=[], primary_theme="Emissions"),
        _company(cap="Mid Cap"),
    )
    assert anchor is not None
    assert anchor["framework"] == "BRSR"
    assert anchor["principle_code"] == "BRSR:P6"
    assert anchor["mandatory"] is True  # broadened mandate => Mid Cap mandatory


def test_anchor_none_for_non_india() -> None:
    """Principle-level mapping is BRSR/India-only this phase — an EU company
    gets no framework_hit anchor (framework-level mandatory shown elsewhere)."""
    assert _framework_hit_anchor(
        _result(frameworks=[], primary_theme="Emissions"),
        _company(region="EU"),
    ) is None


def test_anchor_none_for_unmapped_theme() -> None:
    assert _framework_hit_anchor(
        _result(frameworks=[], primary_theme="Totally Unknown Theme XYZ"),
        _company(),
    ) is None


# ---------------------------------------------------------------------------
# _stamp_framework_hit — anchor wins, LLM only writes prose
# ---------------------------------------------------------------------------


def test_stamp_anchors_facts_llm_only_writes_prose() -> None:
    """Even when the LLM prose tries to claim a different framework / voluntary,
    the stamped facts stay ontology-anchored; only `interpretation` is the LLM
    text."""
    recs = [_rec(title="Close fleet CO2 gap")]
    llm = _StubLLM(content="Actually this maps to GRI 305 and is voluntary.")
    _stamp_framework_hit(recs, _insight(), _result(frameworks=[_brsr_match()]), _company(), llm)

    fh = recs[0].framework_hit
    assert fh is not None
    assert fh["framework"] == "BRSR"            # not "GRI" from the prose
    assert fh["principle_code"] == "BRSR:P6"
    assert fh["mandatory"] is True              # NOT flipped to voluntary
    assert fh["region"] == "INDIA"
    assert fh["interpretation"] == "Actually this maps to GRI 305 and is voluntary."
    assert len(llm.calls) == 1                  # exactly one interpretation call


def test_stamp_shares_one_hit_across_all_recs() -> None:
    recs = [_rec(title="A"), _rec(title="B"), _rec(title="C")]
    llm = _StubLLM()
    _stamp_framework_hit(recs, _insight(), _result(frameworks=[_brsr_match()]), _company(), llm)
    assert all(r.framework_hit is not None for r in recs)
    # Article-level: same facts on every rec, and only ONE LLM call total.
    assert {r.framework_hit["principle_code"] for r in recs} == {"BRSR:P6"}
    assert len(llm.calls) == 1


def test_stamp_noop_and_no_llm_call_when_no_principle() -> None:
    """Unmapped theme => no anchor => recs keep framework_hit None and NO LLM
    call is made (no wasted spend on do-nothing-framework articles)."""
    recs = [_rec(title="A")]
    llm = _StubLLM()
    _stamp_framework_hit(
        recs, _insight(),
        _result(frameworks=[], primary_theme="Totally Unknown Theme XYZ"),
        _company(), llm,
    )
    assert recs[0].framework_hit is None
    assert llm.calls == []


def test_stamp_noop_on_empty_recs() -> None:
    llm = _StubLLM()
    _stamp_framework_hit([], _insight(), _result(frameworks=[_brsr_match()]), _company(), llm)
    assert llm.calls == []


def test_interpretation_falls_back_when_llm_empty() -> None:
    """An empty LLM reply (seen on monitor-only contexts) must NOT leave the
    framework chip with blank prose — a deterministic fallback fills it."""
    from engine.analysis.recommendation_engine import _generate_framework_interpretation
    anchor = {
        "framework": "BRSR", "principle_code": "BRSR:P6",
        "principle_title": "Principle 6 — Environmental Protection",
        "mandatory": True, "region": "INDIA",
    }
    llm = _StubLLM(content="")  # empty reply → fallback
    prose = _generate_framework_interpretation(
        anchor, _insight(), _result(frameworks=[_brsr_match()]), _company(), llm,
    )
    assert len(prose) >= 25
    assert "BRSR:P6" in prose and "BRSR" in prose
