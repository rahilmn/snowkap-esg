"""Phase 54 — story-level de-dup keeps the critical deck tier distinct.

ingestion's ``SemanticDedup`` (title+summary Jaccard 0.75) collapses true
syndicated copies, but it is money-blind: three outlets covering ONE ₹83cr
fraud case stay below its threshold (different specifics) and all survive into
the deck. Without a story guard they then fill all three CRITICAL slots,
burying a genuinely DISTINCT ₹661cr case down in the quick-reads. story_dedup
demotes same-case repeats so each headline slot is a different event.
"""
from __future__ import annotations

from types import SimpleNamespace

from engine.analysis import deck_builder
from engine.analysis import article_selector
from engine.analysis.story_dedup import (
    _money_amounts,
    is_story_dedup_enabled,
    same_story,
    story_signature,
)

_NAMES = ("IDFC First Bank Limited", "idfc-first-bank")


def _sig(title: str):
    return story_signature(title, *_NAMES)


# --------------------------------------------------------------------------- #
# signature
# --------------------------------------------------------------------------- #
def test_signature_extracts_money_and_strips_company_name():
    s = _sig("CBI chargesheets 13 accused in Rs 83-crore IDFC First Bank scam related to CREST fund")
    assert "83" in s.money
    assert "crest" in s.tokens
    # company-name + generic news words are stripped, leaving story vocabulary
    for noise in ("idfc", "bank", "scam"):
        assert noise not in s.tokens


def test_money_normalises_across_spellings():
    assert _sig("loss of Rs 83 crore").money == frozenset({"83"})
    assert _sig("a ₹83-crore hit").money == frozenset({"83"})


def test_money_requires_a_scale_word():
    # counts, percentages, units and years in a rupee-ish position are NOT money
    # (capturing them was a false positive that wrongly merged distinct stories)
    assert _money_amounts("Rs 13 accused in the case") == frozenset()
    assert _money_amounts("Rs 13% penalty levied") == frozenset()
    assert _money_amounts("Waaree 800 MW solar order") == frozenset()
    assert _money_amounts("Q4 FY2026 results; Rs 2026 figure") == frozenset()
    # real scale-anchored figures ARE money, across spellings
    assert _money_amounts("a Rs 83 crore fraud") == frozenset({"83"})
    assert _money_amounts("Rs 661 cr case") == frozenset({"661"})
    assert _money_amounts("₹600-crore matter") == frozenset({"600"})
    assert _money_amounts("Rs 1,200 crore deal") == frozenset({"1200"})


# --------------------------------------------------------------------------- #
# same_story
# --------------------------------------------------------------------------- #
def test_same_case_shared_amount_and_word():
    a = _sig("Chandigarh: CREST ex-project director denied bail in Rs 83 crore IDFC First Bank fraud case")
    b = _sig("CBI chargesheets 13 accused in Rs 83-crore IDFC First Bank scam related to CREST fund")
    assert same_story(a, b)  # shared ₹83 + shared 'crest'


def test_distinct_cases_different_amount_not_merged():
    crest = _sig("CREST ex-project director denied bail in Rs 83 crore IDFC fraud")
    au = _sig("CBI conducts searches in Rs 661 crore IDFC First Bank-AU Finance Bank fraud")
    assert not same_story(crest, au)


def test_same_amount_but_distinct_event_not_merged():
    # company name is stripped in practice, so it can't act as the shared word
    wn = ("Waaree Energies Limited", "waaree-energies")
    order = story_signature("Waaree wins Rs 500 crore solar module supply order", *wn)
    penalty = story_signature("Waaree hit with Rs 500 crore environmental penalty", *wn)
    # same figure (500) but no shared NON-amount word → must stay separate
    assert not same_story(order, penalty)


def test_money_free_retelling_caught_by_title_overlap():
    a = _sig("Chandigarh court denies bail to CREST ex-project director")
    b = _sig("Chandigarh court dismisses bail plea of CREST ex-project director")
    assert same_story(a, b)  # no money token; high title Jaccard carries it


def test_shared_amount_plus_only_generic_word_not_merged():
    # two DISTINCT events that share a ₹ figure and only a GENERIC business word
    # ("module", "order") must stay separate — the shared word isn't a case id.
    wn = ("Waaree Energies Limited", "waaree-energies")
    order = story_signature("Waaree wins Rs 800 crore solar module supply order", *wn)
    credit = story_signature("Waaree extends Rs 800 crore credit line for module orders", *wn)
    assert not same_story(order, credit)


def test_shared_amount_plus_only_topic_word_not_merged():
    # opposite-valence ₹-equal events sharing only a broad topic word stay separate
    wn = ("Waaree Energies Limited", "waaree-energies")
    fined = story_signature("Waaree fined Rs 500 crore for regulatory violations", *wn)
    invests = story_signature("Waaree invests Rs 500 crore in regulatory compliance expansion", *wn)
    assert not same_story(fined, invests)


def test_malformed_jaccard_env_falls_back_instead_of_crashing(monkeypatch):
    # a typo'd threshold must not raise ValueError on the deck hot path
    monkeypatch.setenv("SNOWKAP_STORY_JACCARD", "not-a-number")
    a = _sig("CBI conducts searches in Rs 661 crore IDFC AU Finance fraud")
    b = _sig("Rs 600 crore Haryana power firm fraud; ex-director gets bail")
    assert same_story(a, b) is False  # distinct cases; no crash, default 0.45 used


def test_dedup_enabled_default_and_toggle(monkeypatch):
    monkeypatch.delenv("SNOWKAP_DECK_STORY_DEDUP", raising=False)
    assert is_story_dedup_enabled()
    monkeypatch.setenv("SNOWKAP_DECK_STORY_DEDUP", "0")
    assert not is_story_dedup_enabled()


# --------------------------------------------------------------------------- #
# integration with build_company_deck
# --------------------------------------------------------------------------- #
# 3 articles on the SAME ₹83cr CREST case + 1 distinct ₹661cr AU-Finance case
# + 1 distinct ₹600cr Haryana case. DISTINCT scores → deterministic ranking
# (no threadpool tie), so the cluster representative is predictable.
_C0 = "Chandigarh: CREST ex-project director denied bail in Rs 83 crore IDFC First Bank fraud case"
_C1 = "IDFC First Bank fraud: Chandigarh court dismisses bail plea of CREST ex-project director"  # no ₹
_C2 = "CBI chargesheets 13 accused in Rs 83-crore IDFC First Bank scam related to CREST fund"
_AU = "CBI conducts searches in Rs 661 crore IDFC First Bank-AU Finance Bank fraud"
_HR = "Rs 600-crore IDFC First Bank fraud case: Ex-finance director of Haryana power firm gets bail"


def _result(aid: str, title: str, score: float):
    return SimpleNamespace(
        article_id=aid, title=title, rejected=False,
        criticality={"band": "CRITICAL", "score": score},
        nlp=SimpleNamespace(sentiment=-1),
        event=SimpleNamespace(score_floor=8),
    )


def _wire(monkeypatch, *, crit_ids, light_ids):
    company = SimpleNamespace(slug="idfc-first-bank", name="IDFC First Bank Limited", industry="Banking")
    # select_top_n_for_pipeline is imported LOCALLY inside build_company_deck —
    # patch it at its source module so the local import picks up the stub.
    monkeypatch.setattr(article_selector, "select_top_n_for_pipeline",
                        lambda cands, n=10, **kw: list(cands)[:n])
    monkeypatch.setattr(deck_builder, "_run_stages_1_to_9", lambda a, c: a)

    monkeypatch.setattr(deck_builder, "_publish_critical",
                        lambda r, c: (crit_ids.append(r.article_id), "published")[1])
    monkeypatch.setattr(deck_builder, "_publish_light",
                        lambda r: (light_ids.append(r.article_id), "published")[1])
    return company


def test_build_deck_critical_tier_is_distinct_stories(monkeypatch):
    monkeypatch.setenv("SNOWKAP_DECK_STORY_DEDUP", "1")
    crit_ids: list[str] = []
    light_ids: list[str] = []
    company = _wire(monkeypatch, crit_ids=crit_ids, light_ids=light_ids)
    pool = [_result("c0", _C0, 0.65), _result("c1", _C1, 0.63), _result("c2", _C2, 0.62),
            _result("au", _AU, 0.60), _result("hr", _HR, 0.58)]

    summary = deck_builder.build_company_deck(company, pool, n_critical=3, n_total=10)

    assert summary.critical_published == 3
    # 3 DISTINCT cases: CREST (top-ranked c0) + AU-Finance + Haryana
    assert set(crit_ids) == {"c0", "au", "hr"}
    # the two CREST near-dups were demoted to the light tier, not promoted
    assert {"c1", "c2"} <= set(light_ids)


def test_cluster_merge_catches_dup_after_money_free_lead(monkeypatch):
    # The money-FREE CREST headline (c1) ranks highest. The ₹83cr CREST
    # chargesheet (c2) shares no money with c1 and only the word 'crest' — it
    # would slip the guard if we matched only the lead's own signature. The
    # cluster-merge (c0 demoted into c1's cluster, contributing ₹83) must still
    # catch c2. Result: CREST is represented ONCE (by c1), c0+c2 demoted.
    monkeypatch.setenv("SNOWKAP_DECK_STORY_DEDUP", "1")
    crit_ids: list[str] = []
    light_ids: list[str] = []
    company = _wire(monkeypatch, crit_ids=crit_ids, light_ids=light_ids)
    pool = [_result("c1", _C1, 0.65), _result("c0", _C0, 0.63), _result("c2", _C2, 0.62),
            _result("au", _AU, 0.60), _result("hr", _HR, 0.58)]

    summary = deck_builder.build_company_deck(company, pool, n_critical=3, n_total=10)

    assert summary.critical_published == 3
    assert set(crit_ids) == {"c1", "au", "hr"}
    assert {"c0", "c2"} <= set(light_ids)  # both CREST repeats demoted


def test_disabling_dedup_reproduces_same_story_pileup(monkeypatch):
    monkeypatch.setenv("SNOWKAP_DECK_STORY_DEDUP", "0")
    crit_ids: list[str] = []
    light_ids: list[str] = []
    company = _wire(monkeypatch, crit_ids=crit_ids, light_ids=light_ids)
    pool = [_result("c0", _C0, 0.65), _result("c1", _C1, 0.63), _result("c2", _C2, 0.62),
            _result("au", _AU, 0.60), _result("hr", _HR, 0.58)]

    summary = deck_builder.build_company_deck(company, pool, n_critical=3, n_total=10)

    # dedup OFF → all 3 critical slots are the ₹83cr CREST case; the distinct
    # ₹661cr / ₹600cr cases get crowded out (the bug the guard fixes).
    assert summary.critical_published == 3
    assert set(crit_ids) == {"c0", "c1", "c2"}


def test_empty_signature_critical_does_not_break_the_loop(monkeypatch):
    # A degenerate top-ranked title that strips to an EMPTY signature (only the
    # company name) must publish without crashing and without anchoring a useless
    # cluster — the genuinely-distinct cases still fill the remaining slots.
    monkeypatch.setenv("SNOWKAP_DECK_STORY_DEDUP", "1")
    crit_ids: list[str] = []
    light_ids: list[str] = []
    company = _wire(monkeypatch, crit_ids=crit_ids, light_ids=light_ids)
    pool = [_result("blank", "IDFC First Bank", 0.66),  # strips to empty signature
            _result("c0", _C0, 0.64), _result("au", _AU, 0.62), _result("hr", _HR, 0.60)]

    summary = deck_builder.build_company_deck(company, pool, n_critical=3, n_total=10)

    assert summary.critical_published == 3
    assert "blank" in crit_ids               # empty-sig article still publishes
    assert {"c0", "au"} <= set(crit_ids)     # distinct material cases still land
