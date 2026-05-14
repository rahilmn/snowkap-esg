"""Held-out corpus + gold labels for autoresearcher metric evaluation.

Gold labels are derived from existing Snowkap audit artefacts:

  - Articles with HOME/SECONDARY tier + no hallucination_audit_fired
    + no materiality_downgrade since publication → "predicted_correctly"
  - Articles with hallucination_audit_fired → "engine_was_wrong"
  - Articles with materiality_downgrade → "over_stated"
  - Articles with advisor_resolutions: approve=+1, reject=-1

Corpus shape:
  CorpusArticle(
    article_id, tenant_slug, url, title, published_at,
    predicted_tier, predicted_band, themes,
    gold_tier_band, gold_advisor_verdict, gold_audit_clean,
    raw_insight  # full nested dict for replay
  )

The autoresearcher's evaluator iterates the corpus, runs the on-demand
pipeline replay under each knob change, and compares predicted vs
gold to compute the calibration metric.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass
class CorpusArticle:
    article_id: str
    tenant_slug: str
    url: str
    title: str
    published_at: str
    predicted_tier: str
    predicted_band: str
    themes: list[str]
    gold_tier_band: str         # derived gold label: HOME-clean | SECONDARY | OVER_STATED | UNKNOWN
    gold_advisor_verdict: str   # approve | reject | none
    gold_audit_clean: bool      # True if no hallucination_audit_fired on this article
    raw_insight: dict[str, Any] = field(default_factory=dict)


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        s = ts.rstrip("Z")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _flatten_insight_basic(raw: dict, fallback_id: str) -> dict:
    """Lift commonly-needed fields to the top level of the raw JSON."""
    article = raw.get("article") or {}
    pipeline = raw.get("pipeline") or {}
    insight = raw.get("insight") or {}
    themes_block = pipeline.get("themes") or {}
    themes: list[str] = []
    if isinstance(themes_block, dict):
        primary = themes_block.get("primary_theme")
        if primary:
            themes.append(str(primary))
    return {
        "article_id": str(article.get("id") or pipeline.get("article_id") or fallback_id),
        "url": article.get("url") or pipeline.get("url") or "",
        "title": article.get("title") or "",
        "published_at": article.get("published_at") or "",
        "tier": pipeline.get("tier") or "",
        "materiality": (insight.get("decision_summary") or {}).get("materiality") or "",
        "themes": themes,
    }


def _gold_label_from_audit(
    article_id: str,
    audit_events_by_article: dict[str, list[dict]],
    advisor_resolutions_by_article: dict[str, list[dict]],
) -> tuple[str, str, bool]:
    """Derive `(gold_tier_band, gold_advisor_verdict, gold_audit_clean)`."""
    events = audit_events_by_article.get(article_id, [])
    audit_clean = True
    over_stated = False
    for e in events:
        dt = e.get("decision_type")
        if dt == "hallucination_audit_fired":
            audit_clean = False
        elif dt == "materiality_downgrade":
            over_stated = True

    if over_stated:
        gold_tier = "OVER_STATED"
    elif not audit_clean:
        gold_tier = "ENGINE_WRONG"
    else:
        gold_tier = "CONFIRMED"

    res = advisor_resolutions_by_article.get(article_id, [])
    verdict = "none"
    if res:
        # Most-recent wins
        latest = max(res, key=lambda r: r.get("ts") or "")
        verdict = str(latest.get("resolution") or "none")

    return gold_tier, verdict, audit_clean


def _load_audit_index() -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Read decision_log + advisor_resolutions and index by article_id.

    Returns (audit_by_article, resolutions_by_article).
    """
    audit: dict[str, list[dict]] = {}
    res: dict[str, list[dict]] = {}

    try:
        from engine.audit import read_advisor_resolutions, read_decision_log
        for entry in read_decision_log():
            aid = entry.get("article_id")
            if aid:
                audit.setdefault(str(aid), []).append(entry)
        for entry in read_advisor_resolutions():
            # Resolutions are keyed by event_id, not article_id. The
            # advisor_queue.jsonl tracks the article_id. Best-effort:
            # treat resolutions as global signal too (a resolution that
            # was on a high-uncertainty decision IS a signal about the
            # underlying article).
            eid = entry.get("event_id")
            if eid:
                res.setdefault(str(eid), []).append(entry)
    except Exception:
        pass

    return audit, res


def load_held_out_corpus(
    *,
    min_age_days: int = 90,
    holdout_fraction: float = 0.20,
    repo_root: Path | None = None,
) -> list[CorpusArticle]:
    """Load + holdout-split + gold-label all insights.

    Returns the held-out portion only (the most-recent
    `holdout_fraction` by published_at, after filtering to articles
    settled for at least `min_age_days`).
    """
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent.parent
    outputs_dir = repo_root / "data" / "outputs"
    if not outputs_dir.exists():
        return []

    audit_idx, res_idx = _load_audit_index()
    cutoff = datetime.now(timezone.utc) - timedelta(days=min_age_days)

    all_articles: list[CorpusArticle] = []
    for tenant_dir in outputs_dir.iterdir():
        if not tenant_dir.is_dir():
            continue
        insights_dir = tenant_dir / "insights"
        if not insights_dir.exists():
            continue
        for json_path in insights_dir.glob("*.json"):
            try:
                raw = json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            flat = _flatten_insight_basic(raw, fallback_id=json_path.stem)
            published = _parse_iso(flat.get("published_at"))
            # When min_age_days is 0 we accept every article (useful
            # for the small-corpus regime). When > 0 we filter to
            # articles settled enough for downstream signals to land.
            if min_age_days > 0:
                if not published or published > cutoff:
                    continue

            gold_band, gold_verdict, audit_clean = _gold_label_from_audit(
                flat["article_id"], audit_idx, res_idx,
            )

            all_articles.append(CorpusArticle(
                article_id=flat["article_id"],
                tenant_slug=tenant_dir.name,
                url=flat["url"],
                title=flat["title"],
                published_at=flat["published_at"],
                predicted_tier=flat["tier"],
                predicted_band=flat["materiality"],
                themes=flat["themes"],
                gold_tier_band=gold_band,
                gold_advisor_verdict=gold_verdict,
                gold_audit_clean=audit_clean,
                raw_insight=raw,
            ))

    # Sort by published_at and hold out the most-recent fraction
    all_articles.sort(key=lambda a: a.published_at or "")
    n_held = max(1, int(len(all_articles) * holdout_fraction))
    return all_articles[-n_held:]
