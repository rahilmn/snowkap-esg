"""Calibration metrics for autoresearcher experiments.

The Tier-0 composite metric in [0, 1] (higher = better):

  calibration_score =
      0.40 × F1(predicted_tier_band, gold_tier_band)
    + 0.30 × NDCG@10(predicted_priority_order, gold_priority_order)
    + 0.20 × (1 − hallucination_audit_fire_rate)
    + 0.10 × advisor_agreement_rate

Inputs are derived from `CorpusArticle` instances + the post-replay
prediction outputs. Pure-functional — no I/O, no side effects.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from engine.autoresearcher.corpus import CorpusArticle


@dataclass
class MetricBreakdown:
    """Sub-component scores so the ledger can show which axis moved."""
    f1: float
    ndcg: float
    audit_clean_rate: float
    advisor_agreement: float
    composite: float
    n_articles: int

    def to_dict(self) -> dict[str, float | int]:
        return {
            "f1": round(self.f1, 4),
            "ndcg": round(self.ndcg, 4),
            "audit_clean_rate": round(self.audit_clean_rate, 4),
            "advisor_agreement": round(self.advisor_agreement, 4),
            "composite": round(self.composite, 4),
            "n_articles": self.n_articles,
        }


# Band ordering (higher = more material). Used by F1 + NDCG.
_BAND_RANK = {
    "CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "MEDIUM": 2, "LOW": 1, "": 0,
}


def _binary_predicted_correct(predicted_band: str, gold_band: str) -> bool:
    """True if predicted-band aligns with gold-label semantics.

    CONFIRMED (gold) → predicted should be HOME/HIGH/CRITICAL
    OVER_STATED (gold) → predicted was wrong (it's settled lower)
    ENGINE_WRONG (gold) → predicted was wrong (audit fired)
    """
    pred_rank = _BAND_RANK.get((predicted_band or "").upper(), 0)
    if gold_band == "CONFIRMED":
        return pred_rank >= 3  # HIGH or CRITICAL → confirmed prediction
    if gold_band in ("OVER_STATED", "ENGINE_WRONG"):
        return pred_rank < 3   # the engine OVER-predicted; correct if we now predict lower
    return False


def _f1_score(corpus: list[CorpusArticle]) -> float:
    """Binary F1 across the corpus on the predicted_band vs gold_band."""
    if not corpus:
        return 0.0
    tp = fp = fn = tn = 0
    for a in corpus:
        predicted_positive = _BAND_RANK.get(a.predicted_band.upper(), 0) >= 3
        gold_positive = a.gold_tier_band == "CONFIRMED"
        if predicted_positive and gold_positive:
            tp += 1
        elif predicted_positive and not gold_positive:
            fp += 1
        elif not predicted_positive and gold_positive:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _ndcg_at_k(corpus: list[CorpusArticle], k: int = 10) -> float:
    """NDCG@k of predicted priority ordering against gold priority ordering.

    Priority = band_rank × (audit_clean ? 1 : 0.5). The "ideal" ranking
    sorts by gold priority; the "actual" ranking sorts by predicted band.
    """
    if not corpus:
        return 0.0

    def _gold_priority(a: CorpusArticle) -> float:
        # CONFIRMED articles deserve top ranking
        base = 3.0 if a.gold_tier_band == "CONFIRMED" else 1.0
        if not a.gold_audit_clean:
            base *= 0.5
        return base

    def _pred_priority(a: CorpusArticle) -> float:
        return float(_BAND_RANK.get(a.predicted_band.upper(), 0))

    # Actual ordering: by predicted priority desc
    actual_order = sorted(corpus, key=_pred_priority, reverse=True)[:k]
    # Ideal ordering: by gold priority desc
    ideal_order = sorted(corpus, key=_gold_priority, reverse=True)[:k]

    def _dcg(ranking: list[CorpusArticle]) -> float:
        total = 0.0
        for i, art in enumerate(ranking):
            rel = _gold_priority(art)
            total += (2 ** rel - 1) / math.log2(i + 2)
        return total

    dcg = _dcg(actual_order)
    idcg = _dcg(ideal_order)
    if idcg == 0:
        return 0.0
    return min(1.0, dcg / idcg)


def _audit_clean_rate(corpus: list[CorpusArticle]) -> float:
    if not corpus:
        return 0.0
    return sum(1 for a in corpus if a.gold_audit_clean) / len(corpus)


def _advisor_agreement_rate(corpus: list[CorpusArticle]) -> float:
    """For articles with an advisor verdict, what fraction align with
    the predicted band?

    approve + (HIGH|CRITICAL) → agree
    reject + (LOW|MODERATE) → agree
    Else → disagree

    Articles with no advisor verdict are excluded from the denominator.
    """
    with_verdict = [a for a in corpus if a.gold_advisor_verdict != "none"]
    if not with_verdict:
        return 1.0  # vacuously perfect when no advisor signal
    agree = 0
    for a in with_verdict:
        pred_high = _BAND_RANK.get(a.predicted_band.upper(), 0) >= 3
        if a.gold_advisor_verdict == "approve" and pred_high:
            agree += 1
        elif a.gold_advisor_verdict == "reject" and not pred_high:
            agree += 1
    return agree / len(with_verdict)


def calibration_score(corpus: list[CorpusArticle]) -> MetricBreakdown:
    """The Tier-0 composite scalar metric.

    Short-circuits to zero on empty corpus (no signal to score).
    """
    if not corpus:
        return MetricBreakdown(
            f1=0.0, ndcg=0.0, audit_clean_rate=0.0, advisor_agreement=0.0,
            composite=0.0, n_articles=0,
        )
    f1 = _f1_score(corpus)
    ndcg = _ndcg_at_k(corpus, k=10)
    audit = _audit_clean_rate(corpus)
    advisor = _advisor_agreement_rate(corpus)
    composite = 0.40 * f1 + 0.30 * ndcg + 0.20 * audit + 0.10 * advisor
    return MetricBreakdown(
        f1=f1, ndcg=ndcg, audit_clean_rate=audit, advisor_agreement=advisor,
        composite=composite, n_articles=len(corpus),
    )
