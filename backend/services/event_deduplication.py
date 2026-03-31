"""Event Deduplication — Cluster duplicate articles covering the same event.

GAP 8: Multiple articles about the same event (e.g., IDFC ₹590 Cr fraud) get
inconsistent risk scores. This module detects duplicate events via entity + date
clustering, consolidates risk assessments (highest wins), and links related coverage.

Usage: Called after article scoring to merge duplicate event signals.
"""

from datetime import timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.news import Article, ArticleScore

logger = structlog.get_logger()

# Two articles are "same event" if they share an entity AND are within this window
EVENT_WINDOW_HOURS = 72

# Minimum title similarity (Jaccard on word sets) to consider same-event
MIN_TITLE_SIMILARITY = 0.35


def _word_set(text: str) -> set[str]:
    """Extract meaningful words (len > 3) from text, lowercased."""
    stop = {"the", "and", "for", "with", "from", "this", "that", "been", "have", "will", "into"}
    return {
        w.lower().strip(".,;:!?\"'()-")
        for w in text.split()
        if len(w) > 3 and w.lower() not in stop
    }


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two word sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


async def deduplicate_events(
    tenant_id: str,
    company_id: str,
    db: AsyncSession,
) -> list[dict]:
    """Find and consolidate duplicate event clusters for a company.

    Returns list of clusters: [{event_key, article_ids, consolidated_risk_score, primary_article_id}]
    """
    # Fetch recent articles for this company with scores
    result = await db.execute(
        select(Article, ArticleScore)
        .join(ArticleScore, ArticleScore.article_id == Article.id)
        .where(
            Article.tenant_id == tenant_id,
            ArticleScore.company_id == company_id,
        )
        .order_by(Article.created_at.desc())
        .limit(200)
    )
    rows = result.all()

    if not rows:
        return []

    # Build article data
    articles = []
    for article, score in rows:
        articles.append({
            "id": article.id,
            "title": article.title,
            "words": _word_set(article.title),
            "created_at": article.created_at,
            "priority_score": article.priority_score or 0,
            "priority_level": article.priority_level or "LOW",
            "risk_matrix": (article.deep_insight or {}).get("risk_matrix", {}),
            "relevance_score": score.relevance_score or 0,
        })

    # Cluster by title similarity + time window
    clusters: list[list[dict]] = []
    assigned: set[str] = set()

    for i, a in enumerate(articles):
        if a["id"] in assigned:
            continue

        cluster = [a]
        assigned.add(a["id"])

        for j in range(i + 1, len(articles)):
            b = articles[j]
            if b["id"] in assigned:
                continue

            # Check time window
            if a["created_at"] and b["created_at"]:
                delta = abs((a["created_at"] - b["created_at"]).total_seconds())
                if delta > EVENT_WINDOW_HOURS * 3600:
                    continue

            # Check title similarity
            sim = _jaccard_similarity(a["words"], b["words"])
            if sim >= MIN_TITLE_SIMILARITY:
                cluster.append(b)
                assigned.add(b["id"])

        if len(cluster) > 1:
            clusters.append(cluster)

    # Consolidate each cluster
    consolidated = []
    for cluster in clusters:
        # Use highest priority score across all articles in cluster
        best = max(cluster, key=lambda x: x["priority_score"])

        # Merge risk matrices — take max score per risk category
        merged_risks = {}
        for art in cluster:
            rm = art.get("risk_matrix", {})
            for risk in rm.get("top_risks", []):
                name = risk.get("name", "")
                score_val = risk.get("score", 0)
                if name and (name not in merged_risks or score_val > merged_risks[name]):
                    merged_risks[name] = score_val

        consolidated.append({
            "primary_article_id": best["id"],
            "article_ids": [a["id"] for a in cluster],
            "article_count": len(cluster),
            "consolidated_priority_score": best["priority_score"],
            "consolidated_priority_level": best["priority_level"],
            "consolidated_risks": merged_risks,
            "title_sample": best["title"],
        })

        logger.info(
            "event_cluster_found",
            tenant_id=tenant_id,
            company_id=company_id,
            articles=len(cluster),
            primary=best["title"][:60],
            score=best["priority_score"],
        )

    return consolidated


async def apply_deduplication(
    tenant_id: str,
    company_id: str,
    db: AsyncSession,
) -> int:
    """Run deduplication and update articles with related_coverage metadata.

    Returns number of articles updated with cluster info.
    """
    clusters = await deduplicate_events(tenant_id, company_id, db)
    updated = 0

    for cluster in clusters:
        primary_id = cluster["primary_article_id"]
        related_ids = [aid for aid in cluster["article_ids"] if aid != primary_id]

        for article_id in cluster["article_ids"]:
            result = await db.execute(
                select(Article).where(
                    Article.id == article_id,
                    Article.tenant_id == tenant_id,
                )
            )
            article = result.scalar_one_or_none()
            if not article:
                continue

            # Store cluster metadata on each article
            meta = article.scoring_metadata or {}
            meta["event_cluster"] = {
                "primary_article_id": primary_id,
                "is_primary": article_id == primary_id,
                "related_article_ids": related_ids if article_id == primary_id else [primary_id],
                "cluster_size": cluster["article_count"],
                "consolidated_priority": cluster["consolidated_priority_score"],
                "consolidated_risks": cluster["consolidated_risks"],
            }
            article.scoring_metadata = meta

            # Non-primary articles inherit the highest priority from cluster
            if article_id == primary_id:
                article.priority_score = cluster["consolidated_priority_score"]
                article.priority_level = cluster["consolidated_priority_level"]

            updated += 1

    if updated:
        await db.flush()
        logger.info(
            "deduplication_applied",
            tenant_id=tenant_id,
            company_id=company_id,
            clusters=len(clusters),
            articles_updated=updated,
        )

    return updated
