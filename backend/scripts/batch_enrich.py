"""Batch enrichment script — runs all missing enrichment steps on articles.

Usage:
    cd D:/ClaudePowerofnow/snowkap-esg/snowkap-esg
    python -m backend.scripts.batch_enrich [--tenant TENANT_DOMAIN] [--limit N] [--dry-run]

Runs: framework_rag, geographic_intelligence, risk_spotlight, deep_insight, rereact
Skips any step where the article already has data.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


async def main(tenant_filter: str | None = None, limit: int = 0, dry_run: bool = False):
    from sqlalchemy import select, func, or_
    from backend.core.database import create_worker_session_factory
    from backend.core.config import settings
    from backend.models.news import Article
    from backend.models.company import Company
    from backend.models.tenant import Tenant

    print(f"[batch_enrich] OpenAI key configured: {bool(settings.OPENAI_API_KEY)}")
    print(f"[batch_enrich] Database: {settings.DATABASE_URL[:60]}...")

    session_factory = create_worker_session_factory()

    async with session_factory() as db:
        # Get tenants
        if tenant_filter:
            tenants_q = await db.execute(
                select(Tenant).where(Tenant.domain == tenant_filter)
            )
        else:
            tenants_q = await db.execute(select(Tenant))
        tenants = tenants_q.scalars().all()
        print(f"[batch_enrich] Found {len(tenants)} tenant(s)")

        for tenant in tenants:
            print(f"\n{'='*60}")
            print(f"[tenant: {tenant.domain}] Processing...")

            # Load company context for this tenant
            comp_res = await db.execute(
                select(Company).where(Company.tenant_id == tenant.id).limit(1)
            )
            comp = comp_res.scalars().first()
            company_name = comp.name if comp else "Unknown Company"
            company_industry = comp.industry if comp else None
            company_sasb = comp.sasb_category if comp else None
            company_market_cap = comp.market_cap_value if comp else None
            company_revenue = comp.revenue_last_fy if comp else None
            company_competitors = comp.competitors if comp else None
            company_hq_country = comp.headquarter_country if comp else None
            company_hq_region = comp.headquarter_region if comp else None
            company_exchange = comp.listing_exchange if comp else None
            company_market_cap_str = comp.market_cap if comp else None
            print(f"  Company: {company_name} | Cap: {company_market_cap_str} | Region: {company_hq_region}")

            # Find articles needing enrichment (missing any of the key fields)
            q = select(Article).where(
                Article.tenant_id == tenant.id,
                or_(
                    Article.framework_matches.is_(None),
                    Article.deep_insight.is_(None),
                    Article.rereact_recommendations.is_(None),
                    Article.risk_matrix.is_(None),
                )
            ).order_by(Article.published_at.desc())
            if limit:
                q = q.limit(limit)

            arts_res = await db.execute(q)
            articles = arts_res.scalars().all()
            print(f"  Articles needing enrichment: {len(articles)}")

            if dry_run:
                for art in articles:
                    missing = []
                    if not art.framework_matches: missing.append("framework")
                    if not art.deep_insight: missing.append("deep_insight")
                    if not art.rereact_recommendations: missing.append("rereact")
                    if not art.risk_matrix: missing.append("risk_matrix")
                    print(f"    [{art.id[:8]}] {art.title[:60]} — missing: {', '.join(missing)}")
                continue

            for i, art in enumerate(articles):
                content = art.content or art.summary or art.title or ""
                print(f"\n  [{i+1}/{len(articles)}] {art.title[:70]}...")
                t0 = time.time()

                # Step 1: Framework RAG
                if not art.framework_matches:
                    try:
                        from backend.services.framework_rag import retrieve_applicable_frameworks
                        matches = await retrieve_applicable_frameworks(
                            esg_themes=art.esg_themes if isinstance(art.esg_themes, list) else None,
                            article_content=content,
                            article_title=art.title,
                            company_region=company_hq_region,
                            company_market_cap=company_market_cap_str,
                        )
                        if matches:
                            art.framework_matches = [
                                {
                                    "framework_id": m.framework_id,
                                    "framework_name": m.framework_name,
                                    "triggered_sections": m.triggered_sections,
                                    "compliance_implications": m.compliance_implications,
                                    "relevance_score": m.relevance_score,
                                    "is_mandatory": m.is_mandatory,
                                }
                                for m in matches
                            ]
                            print(f"    [OK] framework_matches: {len(matches)} frameworks")
                    except Exception as e:
                        print(f"    [FAIL] framework_matches failed: {e}")

                # Step 2: Risk spotlight / risk matrix
                if not art.risk_matrix:
                    try:
                        from backend.services.risk_spotlight import run_risk_spotlight
                        spotlight = await run_risk_spotlight(
                            article_title=art.title,
                            article_content=content,
                            company_name=company_name,
                        )
                        if spotlight:
                            art.risk_matrix = spotlight
                            print(f"    [OK] risk_matrix")
                    except Exception as e:
                        print(f"    [FAIL] risk_matrix failed: {e}")

                # Step 3: Deep insight
                if not art.deep_insight:
                    try:
                        from backend.services.deep_insight_generator import generate_deep_insight
                        fm = art.framework_matches
                        fw_names: list[str] = []
                        if isinstance(fm, list):
                            fw_names = [f.get("framework_id", "") for f in fm if isinstance(f, dict)]

                        comp_names: list[str] = []
                        if isinstance(company_competitors, list):
                            comp_names = [c.get("name", c) if isinstance(c, dict) else str(c) for c in company_competitors[:5]]

                        deep = await generate_deep_insight(
                            article_title=art.title,
                            article_content=content,
                            article_summary=art.summary,
                            company_name=company_name,
                            frameworks=fw_names,
                            sentiment_score=art.sentiment_score,
                            urgency=art.urgency,
                            content_type=art.content_type,
                            esg_pillar=art.esg_pillar,
                            competitors=comp_names or None,
                            nlp_extraction=art.nlp_extraction,
                            esg_themes=art.esg_themes,
                            risk_matrix=art.risk_matrix,
                            market_cap=company_market_cap,
                            revenue=company_revenue,
                        )
                        if deep:
                            art.deep_insight = deep
                            print(f"    [OK] deep_insight")
                    except Exception as e:
                        print(f"    [FAIL] deep_insight failed: {e}")

                # Step 4: REREACT recommendations
                if not art.rereact_recommendations and art.deep_insight:
                    try:
                        from backend.services.rereact_engine import rereact_recommendations
                        fm = art.framework_matches
                        fw_names = []
                        if isinstance(fm, list):
                            fw_names = [f.get("framework_id", "") for f in fm if isinstance(f, dict)]

                        comp_names = []
                        if isinstance(company_competitors, list):
                            comp_names = [c.get("name", c) if isinstance(c, dict) else str(c) for c in company_competitors[:5]]

                        rr = await rereact_recommendations(
                            article_title=art.title,
                            article_content=content,
                            deep_insight=art.deep_insight,
                            company_name=company_name,
                            frameworks=fw_names,
                            content_type=art.content_type,
                            competitors=comp_names or None,
                            market_cap=company_market_cap_str,
                            listing_exchange=company_exchange,
                            headquarter_country=company_hq_country,
                        )
                        if rr:
                            art.rereact_recommendations = rr
                            print(f"    [OK] rereact_recommendations")
                    except Exception as e:
                        print(f"    [FAIL] rereact failed: {e}")

                elapsed = time.time() - t0
                print(f"    Done in {elapsed:.1f}s")

                # Commit every article to avoid losing progress
                try:
                    await db.commit()
                except Exception as e:
                    print(f"    [FAIL] commit failed: {e}")
                    await db.rollback()

                # Small delay to avoid rate limits
                if i < len(articles) - 1:
                    await asyncio.sleep(0.5)

        # Final stats
        print(f"\n{'='*60}")
        print("[batch_enrich] Done! Checking final coverage...")
        for tenant in tenants:
            total = await db.scalar(
                select(func.count()).where(Article.tenant_id == tenant.id)
            )
            has_framework = await db.scalar(
                select(func.count()).where(
                    Article.tenant_id == tenant.id,
                    Article.framework_matches.isnot(None),
                )
            )
            has_deep = await db.scalar(
                select(func.count()).where(
                    Article.tenant_id == tenant.id,
                    Article.deep_insight.isnot(None),
                )
            )
            has_rereact = await db.scalar(
                select(func.count()).where(
                    Article.tenant_id == tenant.id,
                    Article.rereact_recommendations.isnot(None),
                )
            )
            print(f"  [{tenant.domain}] total={total} framework={has_framework} deep_insight={has_deep} rereact={has_rereact}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch enrich articles")
    parser.add_argument("--tenant", type=str, help="Filter by tenant domain")
    parser.add_argument("--limit", type=int, default=0, help="Max articles per tenant (0=all)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be enriched")
    args = parser.parse_args()

    asyncio.run(main(args.tenant, args.limit, args.dry_run))
