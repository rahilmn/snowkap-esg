"""Seed demo data for Mahindra tenant."""
import asyncio
from backend.core.database import async_session_factory
from backend.models.company import Company
from backend.models.prediction import PredictionReport
from backend.models.news import Article, ArticleScore, CausalChain
from sqlalchemy import select

TENANT_ID = "4c8767d5-489d-41cd-8e8d-0cc8081a3d81"


async def seed():
    async with async_session_factory() as db:
        # 1. Seed companies
        companies_data = [
            ("Mahindra & Mahindra", "mahindra-mahindra", "mahindra.com", "Automobiles & Components"),
            ("Tech Mahindra", "tech-mahindra", "techmahindra.com", "IT Services"),
            ("Mahindra Logistics", "mahindra-logistics", "mahindralogistics.com", "Logistics & Transportation"),
            ("Mahindra Finance", "mahindra-finance", "mahindrafinance.com", "Financial Services"),
            ("Club Mahindra", "club-mahindra", "clubmahindra.com", "Hospitality & Tourism"),
        ]
        company_ids = []
        for name, slug, domain, industry in companies_data:
            c = Company(
                tenant_id=TENANT_ID, name=name, slug=slug, domain=domain, industry=industry,
            )
            db.add(c)
            await db.flush()
            company_ids.append(c.id)
            print(f"Company: {name} ({c.id})")

        # 2. Get article IDs
        result = await db.execute(select(Article.id).where(Article.tenant_id == TENANT_ID).limit(10))
        article_ids = [r[0] for r in result.all()]
        print(f"Found {len(article_ids)} articles")

        # 3. Seed article impact scores (ArticleScore has: article_id, company_id, relevance_score, impact_score, financial_exposure, causal_hops)
        impact_data = [
            (article_ids[0], company_ids[0], 0.9, 82, None, 1),
            (article_ids[0], company_ids[1], 0.5, 45, None, 2),
            (article_ids[1], company_ids[0], 0.8, 71, None, 0),
            (article_ids[2], company_ids[0], 0.95, 90, None, 0),
            (article_ids[2], company_ids[2], 0.7, 65, None, 1),
        ]
        if len(article_ids) > 3:
            impact_data.append((article_ids[3], company_ids[2], 0.85, 78, None, 0))
        for art_id, comp_id, rel_score, imp_score, fin_exp, hops in impact_data:
            s = ArticleScore(
                tenant_id=TENANT_ID, article_id=art_id, company_id=comp_id,
                relevance_score=rel_score, impact_score=imp_score,
                financial_exposure=fin_exp, causal_hops=hops,
            )
            db.add(s)

        # 4. Seed causal chains
        chains_data = [
            (article_ids[0], company_ids[0], ["Fuel Emissions Policy", "Automotive Industry", "Mahindra & Mahindra"], 1, "supplyChainUpstream", 0.82, "EU emission standards cascade to Indian automakers through export compliance"),
            (article_ids[2], company_ids[0], ["Carbon Neutrality Pledge", "Mahindra Group", "Mahindra & Mahindra"], 0, "directOperational", 0.90, "Direct commitment by Mahindra Group to achieve carbon neutrality by 2040"),
            (article_ids[2], company_ids[2], ["Carbon Neutrality Pledge", "Mahindra Group", "Fleet Operations", "Mahindra Logistics"], 1, "supplyChainDownstream", 0.65, "Group carbon pledge requires fleet electrification at Mahindra Logistics"),
        ]
        for art_id, comp_id, path, hops, rel_type, score, explanation in chains_data:
            c = CausalChain(
                tenant_id=TENANT_ID, article_id=art_id, company_id=comp_id,
                chain_path=path, hops=hops, relationship_type=rel_type,
                impact_score=score, explanation=explanation,
                framework_alignment=["BRSR", "GRI 305", "TCFD"],
            )
            db.add(c)

        # 5. Seed predictions
        predictions_data = [
            {
                "title": "Emission Compliance Cost Impact on Mahindra Automotive",
                "summary": "Tightening fuel emission standards in India may increase compliance costs for Mahindra automotive division by 8-12% over next 2 years.",
                "prediction_text": "If emission norms tighten to BS-VII equivalent by 2028, Mahindra & Mahindra faces Rs 800Cr-1200Cr in additional R&D and retooling costs. EV transition provides partial hedge.",
                "confidence_score": 0.78,
                "financial_impact": 85000000,
                "time_horizon": "medium",
                "company_id": company_ids[0],
                "article_id": article_ids[0],
                "status": "completed",
            },
            {
                "title": "Carbon Neutrality 2040 Progress Assessment",
                "summary": "Mahindra Group's 2040 carbon neutrality target is on track with science-based targets validated by SBTi.",
                "prediction_text": "Current trajectory shows 35% Scope 1+2 reduction achieved. Scope 3 remains the primary challenge, particularly in supply chain and fleet operations.",
                "confidence_score": 0.85,
                "financial_impact": None,
                "time_horizon": "long",
                "company_id": company_ids[0],
                "article_id": article_ids[2],
                "status": "completed",
            },
            {
                "title": "Logistics Fleet Electrification Impact",
                "summary": "Mahindra Logistics warehouse expansion and fleet electrification will reduce Scope 1 emissions by 25% but increase capex.",
                "prediction_text": "Electric fleet transition for last-mile delivery expected to reduce operational carbon footprint significantly. ROI positive within 4 years.",
                "confidence_score": 0.72,
                "financial_impact": 45000000,
                "time_horizon": "medium",
                "company_id": company_ids[2],
                "article_id": article_ids[3] if len(article_ids) > 3 else article_ids[0],
                "status": "completed",
            },
            {
                "title": "Tech Mahindra ESG Rating Improvement Forecast",
                "summary": "Tech Mahindra's renewable energy procurement and employee welfare initiatives position for MSCI ESG rating upgrade.",
                "prediction_text": None,
                "confidence_score": 0.65,
                "financial_impact": None,
                "time_horizon": "short",
                "company_id": company_ids[1],
                "article_id": None,
                "status": "pending",
            },
            {
                "title": "Supply Chain Disruption Risk: Semiconductor Shortage",
                "summary": "Ongoing semiconductor supply constraints affect Mahindra automotive production and ESG reporting timelines.",
                "prediction_text": "Semiconductor shortages may delay EV production targets by 6-9 months, impacting Scope 3 reporting under BRSR framework.",
                "confidence_score": 0.81,
                "financial_impact": 120000000,
                "time_horizon": "short",
                "company_id": company_ids[0],
                "article_id": None,
                "status": "completed",
            },
        ]
        for pd in predictions_data:
            risk = "high" if pd["confidence_score"] > 0.75 else "medium" if pd["confidence_score"] > 0.6 else "low"
            p = PredictionReport(
                tenant_id=TENANT_ID,
                company_id=pd["company_id"],
                article_id=pd["article_id"],
                title=pd["title"],
                summary=pd["summary"],
                prediction_text=pd["prediction_text"],
                confidence_score=pd["confidence_score"],
                financial_impact=pd["financial_impact"],
                time_horizon=pd["time_horizon"],
                status=pd["status"],
                agent_consensus={
                    "analysis": "Multi-agent consensus reached across 5 specialist agents",
                    "recommendation": "Monitor regulatory developments and accelerate EV transition roadmap",
                    "risk_level": risk,
                    "opportunities": ["Green bond financing", "Carbon credit trading", "ESG-linked lending"],
                },
            )
            db.add(p)

        # 6. Update articles with ESG pillars, sentiment, entities
        result = await db.execute(select(Article).where(Article.tenant_id == TENANT_ID))
        articles = result.scalars().all()
        pillars = ["Environmental", "Social", "Governance", "Environmental", "Social"]
        sentiments = ["negative", "neutral", "positive", "positive", "neutral"]
        entities_list = [
            ["Mahindra", "emission standards", "India"],
            ["climate change", "sustainability", "India"],
            ["Mahindra", "carbon neutral", "SBTi"],
            ["Mahindra Logistics", "warehousing", "Eastern India"],
            ["GST", "Mahindra Group", "investment"],
        ]
        for i, article in enumerate(articles):
            article.esg_pillar = pillars[i % len(pillars)]
            article.sentiment = sentiments[i % len(sentiments)]
            article.entities = entities_list[i % len(entities_list)]

        await db.commit()
        print(f"Seeded: {len(company_ids)} companies, {len(impact_data)} impact scores, {len(chains_data)} causal chains, {len(predictions_data)} predictions")
        print("Done!")


asyncio.run(seed())
