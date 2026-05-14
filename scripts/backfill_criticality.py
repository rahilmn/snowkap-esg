"""Phase 1.7 backfill — compute + persist criticality_score on every existing
article in article_index.

The Phase 1.5 wiring stamps criticality on NEW articles automatically. This
script catches the existing ~110 articles that pre-date Phase 1 by reading
their JSON payload + invoking the same scoring path the live pipeline uses.

The plan §3.7 says: do NOT regenerate Stages 10-12. We only re-score —
Stages 1-9 outputs (NLP, themes, event, relevance, frameworks, risk, cascade)
are already on disk in the JSON. We extract the cascade total from the
saved insight when present (cascade-aware path), otherwise from the
financial_signal in NLP (baseline path).

Cost: ~110 articles × ~$0.00002 embedding = ~$0.0022 total. Negligible.

Usage:
    python scripts/backfill_criticality.py                # all articles
    python scripts/backfill_criticality.py --slug X       # one tenant
    python scripts/backfill_criticality.py --dry-run      # report only
    python scripts/backfill_criticality.py --skip-embedding  # no painpoint match (no LLM)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("backfill_criticality")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", help="One tenant slug only (default: all).")
    parser.add_argument("--dry-run", action="store_true", help="Report-only, no DB writes.")
    parser.add_argument(
        "--skip-embedding", action="store_true",
        help="Skip article-side embedding — fastest, but painpoint_match=0.",
    )
    args = parser.parse_args()

    from engine.config import get_data_path, load_companies
    from engine.db.connection import connect

    companies = {c.slug: c for c in load_companies()}
    outputs_root = get_data_path("outputs")

    # 1. Pull article rows that need backfill (criticality_score IS NULL)
    target_rows: list[dict[str, Any]] = []
    with connect() as c:
        cur = c.cursor()
        sql = "SELECT id, company_slug, json_path FROM article_index WHERE criticality_score IS NULL"
        params: tuple = ()
        if args.slug:
            sql += " AND company_slug = ?"
            params = (args.slug,)
        cur.execute(sql, params)
        for r in cur.fetchall():
            target_rows.append(dict(r) if hasattr(r, "keys") else {
                "id": r[0], "company_slug": r[1], "json_path": r[2],
            })

    print(f"=== Phase 1.7 criticality backfill [{('DRY RUN' if args.dry_run else 'LIVE')}] ===")
    print(f"Articles needing score: {len(target_rows)}")
    print(f"Skip embedding: {args.skip_embedding}")
    print()

    if not target_rows:
        print("Nothing to do — every article already has criticality_score.")
        return 0

    from engine.analysis.criticality_scorer import score as score_criticality
    from engine.analysis.painpoint_embeddings import (
        load_painpoint_embeddings, embed_article_for_scoring,
    )

    scored = 0
    skipped = 0
    failed = 0
    by_band: dict[str, int] = {}

    for row in target_rows:
        article_id = row.get("id")
        slug = row.get("company_slug")
        json_path = row.get("json_path")
        if not article_id or not slug or not json_path:
            skipped += 1
            continue

        # Resolve JSON path (stored as repo-relative)
        full_path = ROOT / json_path
        if not full_path.exists():
            logger.warning("[%s] JSON missing on disk: %s", article_id, full_path)
            skipped += 1
            continue

        try:
            payload = json.loads(full_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[%s] JSON parse failed: %s", article_id, exc)
            skipped += 1
            continue

        company = companies.get(slug)
        company_revenue = float(getattr(company, "revenue_cr", 0) or 0) if company else 0

        # Extract scoring inputs from the saved JSON
        article = payload.get("article") or {}
        pipeline = payload.get("pipeline") or {}
        insight = payload.get("insight") or {}
        relevance = pipeline.get("relevance") or {}
        event = pipeline.get("event") or {}
        nlp = pipeline.get("nlp") or {}

        # Cascade total — look in insight.criticality first (if anything from
        # Phase 1.5 already ran), then derive from decision_summary.
        cascade_total = 0.0
        decision = insight.get("decision_summary") or {}
        for key in ("financial_exposure", "key_risk", "top_opportunity"):
            v = decision.get(key) or ""
            import re
            m = re.search(r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d+)?)", str(v))
            if m:
                try:
                    cascade_total = max(cascade_total, float(m.group(1).replace(",", "")))
                except (TypeError, ValueError):
                    pass

        # Painpoint embeddings (cached) + article embedding (one OpenAI call per article)
        painpoint_embs: list[tuple[list[float], float]] = []
        article_emb: list[float] = []
        try:
            painpoint_embs = load_painpoint_embeddings(slug)
            if painpoint_embs and not args.skip_embedding:
                title = article.get("title") or ""
                content = (payload.get("article") or {}).get("content") or ""
                article_emb = embed_article_for_scoring(title, content[:200])
        except Exception as exc:
            logger.debug("[%s] embedding skipped: %s", article_id, exc)

        # Polarity
        sentiment = nlp.get("sentiment") if isinstance(nlp.get("sentiment"), (int, float)) else None
        if sentiment is not None and sentiment >= 1:
            narrative_polarity: str | None = "positive"
        elif sentiment is not None and sentiment <= -1:
            narrative_polarity = "negative"
        else:
            narrative_polarity = "neutral"
        event_polarity = (event.get("polarity") or insight.get("event_polarity") or "neutral")

        try:
            crit = score_criticality(
                relevance_total=relevance.get("total"),
                cascade_total_cr=cascade_total,
                company_revenue_cr=company_revenue,
                event_id=event.get("event_id"),
                article_embedding=article_emb,
                painpoint_embeddings=painpoint_embs,
                published_at=article.get("published_at"),
                source=article.get("source"),
                url=article.get("url"),
                cascade_confidence=insight.get("cascade_confidence"),
                event_polarity=event_polarity,
                narrative_polarity=narrative_polarity,
            )
        except Exception as exc:
            logger.warning("[%s] scorer failed: %s", article_id, exc)
            failed += 1
            continue

        by_band[crit.band] = by_band.get(crit.band, 0) + 1

        if args.dry_run:
            scored += 1
            continue

        # Write back to article_index (separate transaction per row so one
        # bad row doesn't poison the rest)
        try:
            with connect() as c:
                cur = c.cursor()
                cur.execute(
                    "UPDATE article_index SET criticality_score = ?, criticality_band = ? WHERE id = ?",
                    (crit.score, crit.band, article_id),
                )
        except Exception as exc:
            logger.warning("[%s] DB write failed: %s", article_id, exc)
            failed += 1
            continue

        # Also stamp into the JSON so the next read picks it up without
        # re-running this script.
        try:
            insight_existing = payload.get("insight") or {}
            insight_existing["criticality"] = crit.as_dict()
            payload["insight"] = insight_existing
            full_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.debug("[%s] JSON write failed: %s", article_id, exc)

        scored += 1

    print()
    print("=== Backfill complete ===")
    print(f"  Scored: {scored}")
    print(f"  Skipped: {skipped}")
    print(f"  Failed: {failed}")
    print(f"  Band distribution: {by_band}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
