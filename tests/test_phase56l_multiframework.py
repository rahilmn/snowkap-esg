"""Phase 56.L — "Other frameworks this impacts" dropdown.

The engine already matches multiple frameworks per story; this surfaces the
non-BRSR ones (GRI, CSRD/ESRS, IFRS S1/S2) as an article-level other_frameworks
array on what_it_triggers, rendered as a collapsible dropdown (app) + stacked
list (email). Curated cards can supply the list explicitly.
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

from engine.analysis import deck_builder as db
from engine.analysis import unified_analysis as ua


def _fm(framework_id, sections, mandatory=False):
    return SimpleNamespace(framework_id=framework_id, is_mandatory=mandatory,
                           triggered_sections=sections)


def test_other_framework_hits_surfaces_gri_csrd_ifrs():
    result = SimpleNamespace(
        themes=SimpleNamespace(primary_theme="Climate Change"),
        frameworks=[
            _fm("BRSR", [{"code": "BRSR:P6", "title": "Principle 6"}], mandatory=True),  # excluded (primary)
            _fm("GRI", ["GRI:305"]),
            _fm("CSRD", ["ESRS:E1"]),
            _fm("ISSB", ["TCFD:Gov"]),
            _fm("CDP", None),          # not in the curated set → excluded
            _fm("ESRS", ["ESRS:E1"]),  # dedup with CSRD (same label)
        ],
    )
    out = ua._other_framework_hits(result)
    labels = [h["framework"] for h in out]
    assert labels == ["GRI", "CSRD (ESRS)", "IFRS S1/S2"]      # order + dedup
    assert "BRSR" not in labels                                 # primary excluded
    by = {h["framework"]: h for h in out}
    assert by["GRI"]["section_code"] == "GRI 305"
    assert by["CSRD (ESRS)"]["section_code"] == "ESRS E1"
    assert by["IFRS S1/S2"]["section_code"] == "IFRS S2"
    assert all("Climate Change" in h["interpretation"] for h in out)


def test_other_framework_hits_empty_when_no_matches():
    result = SimpleNamespace(themes=SimpleNamespace(primary_theme="Energy"),
                             frameworks=[_fm("BRSR", [{"code": "BRSR:P6"}], mandatory=True),
                                         _fm("CDP", None)])
    assert ua._other_framework_hits(result) == []


def test_sanitize_other_frameworks_clamps():
    out = db._sanitize_other_frameworks([
        {"framework": "GRI", "section_code": "GRI 305", "interpretation": "x", "mandatory": False},
        {"framework": "", "section_code": "skip"},        # dropped (no framework)
        {"not": "a dict"},                                  # ignored
    ])
    assert len(out) == 1 and out[0]["framework"] == "GRI"


def test_stamp_curated_insight_writes_other_frameworks(monkeypatch):
    payload = {"company_slug": "maruti-suzuki-india",
               "insight": {"analysis": {"what_it_triggers": {
                   "framework_hit": {"framework": "BRSR", "principle_code": "BRSR:P6"},
                   "recommended_actions": []}}}}
    captured = {}
    monkeypatch.setattr("engine.models.insight_payload.get", lambda aid: payload)
    monkeypatch.setattr("engine.models.insight_payload.upsert",
                        lambda aid, slug, p: captured.update(payload=p))
    ok = db.stamp_curated_insight(
        "aid", "maruti-suzuki-india",
        other_frameworks=[{"framework": "GRI", "section_code": "GRI 305",
                           "interpretation": "Energy and emissions disclosure under GRI."}],
    )
    assert ok is True
    wit = captured["payload"]["insight"]["analysis"]["what_it_triggers"]
    assert wit["other_frameworks"][0]["framework"] == "GRI"
    assert wit["other_frameworks"][0]["section_code"] == "GRI 305"


def test_stamp_curated_card_writes_glossary_and_highlights(monkeypatch):
    """Phase 56.N — glossary term + highlight terms land on the deck card
    (personalised_analysis top-level) so NowPage merges them into analysis."""
    from types import SimpleNamespace
    existing = SimpleNamespace(
        personalised_analysis={"what_it_triggers": {}, "why_it_matters": {}},
        criticality_score=0.9, criticality_band="CRITICAL")
    captured = {}
    monkeypatch.setattr("engine.models.company_article_view.get", lambda aid, slug: existing)
    monkeypatch.setattr("engine.models.company_article_view.upsert",
                        lambda **kw: captured.update(kw))
    ok = db.stamp_curated_card(
        SimpleNamespace(slug="maruti-suzuki-india"), "aid",
        glossary={"term": "CAFE-3", "text": "India's corporate fuel-efficiency norms."},
        highlight_terms=["penalties", ""])
    assert ok is True
    pa = captured["personalised_analysis"]
    assert pa["glossary"] == {"term": "CAFE-3", "text": "India's corporate fuel-efficiency norms."}
    assert pa["highlight_terms"] == ["penalties"]   # blank dropped


def test_stamp_curated_insight_writes_glossary_and_highlights(monkeypatch):
    """Phase 56.N — same on the swipe-up store (insight.analysis), which the
    ArticleSheet reads."""
    payload = {"company_slug": "maruti-suzuki-india",
               "insight": {"analysis": {"why_it_matters": {}, "what_it_triggers": {}}}}
    captured = {}
    monkeypatch.setattr("engine.models.insight_payload.get", lambda aid: payload)
    monkeypatch.setattr("engine.models.insight_payload.upsert",
                        lambda aid, slug, p: captured.update(payload=p))
    ok = db.stamp_curated_insight(
        "aid", "maruti-suzuki-india",
        glossary={"term": "CAFE-3", "text": "Fuel-efficiency norms."},
        highlight_terms=["penalties"])
    assert ok is True
    a = captured["payload"]["insight"]["analysis"]
    assert a["glossary"]["term"] == "CAFE-3"
    assert a["highlight_terms"] == ["penalties"]


def test_stamp_curated_card_writes_card_teaser(monkeypatch):
    """Phase 56.M — the FOMO teaser is stamped onto the deck card's
    why_it_matters.card_teaser (the field SwipeCard reads to fill the blank)."""
    from types import SimpleNamespace
    existing = SimpleNamespace(
        personalised_analysis={"what_it_triggers": {}, "why_it_matters": {}},
        criticality_score=0.9, criticality_band="CRITICAL")
    captured = {}
    monkeypatch.setattr("engine.models.company_article_view.get", lambda aid, slug: existing)
    monkeypatch.setattr("engine.models.company_article_view.upsert",
                        lambda **kw: captured.update(kw))
    ok = db.stamp_curated_card(
        SimpleNamespace(slug="maruti-suzuki-india"), "aid",
        card_teaser="Draft norms could put hundreds of crores in play.")
    assert ok is True
    wim = captured["personalised_analysis"]["why_it_matters"]
    assert wim["card_teaser"].startswith("Draft norms")


def test_email_renders_other_frameworks_stacked():
    from engine.output.newsletter_morning_brew import render_article_morning_brew
    payload = {"article": {"title": "x", "company_slug": "s", "id": "a"},
               "insight": {"analysis": {
                   "what_changed": {"headline": "h"},
                   "why_it_matters": {"criticality_summary": "c"},
                   "what_it_triggers": {
                       "framework_hit": {"framework": "BRSR", "principle_code": "BRSR:P6",
                                         "principle_title": "Principle 6", "mandatory": True,
                                         "interpretation": "i"},
                       "other_frameworks": [
                           {"framework": "GRI", "section_code": "GRI 305", "interpretation": "GRI prose."},
                           {"framework": "IFRS S1/S2", "section_code": "IFRS S2", "interpretation": "ISSB prose."},
                       ],
                       "recommended_actions": []},
                   "what_to_watch": {}}}}
    html = render_article_morning_brew(payload=payload, company_name="Maruti")
    norm = re.sub(r"\s+", " ", html)
    assert "Other frameworks this impacts" in norm
    assert "GRI" in norm and "GRI 305" in norm
    assert "IFRS S1/S2" in norm and "IFRS S2" in norm
    assert "GRI prose." in norm and "ISSB prose." in norm
