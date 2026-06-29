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
