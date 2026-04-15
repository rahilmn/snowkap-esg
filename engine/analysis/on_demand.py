"""On-demand enrichment — Phase 14 hybrid intelligence.

Pre-processing at ingestion handles stages 1-9 (cheap, ontology-driven).
This module runs stages 10-12 on demand when a user clicks an article:
  - Stage 10: Deep insight generation (gpt-4.1)
  - Stage 11: Perspective transformation (ontology, no LLM)
  - Stage 12: REREACT recommendations (gpt-4.1-mini x3)
  + Phase I LLM intelligence layers (competitive brief, causal narrative,
    executive Q&A, sentiment trajectory)

Results are cached to disk so subsequent opens are instant.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from engine.config import Company, get_company, get_data_path

logger = logging.getLogger(__name__)


def enrich_on_demand(
    article_id: str, company_slug: str, force: bool = False
) -> dict[str, Any] | None:
    """Run deep enrichment for an article on demand.

    Args:
        force: If True, re-run enrichment even if already cached (for articles
               processed with old prompts that need primitive-enriched re-analysis).

    Returns the full enriched payload dict, or None on failure.
    """
    from engine.analysis.insight_generator import generate_deep_insight
    from engine.analysis.perspective_engine import transform_for_perspective
    from engine.analysis.pipeline import PipelineResult
    from engine.analysis.recommendation_engine import generate_recommendations
    from engine.output.writer import write_insight

    started = time.perf_counter()

    # 1. Find the article JSON
    insights_dir = get_data_path("outputs", company_slug, "insights")
    candidates = list(insights_dir.glob(f"*{article_id}*"))
    if not candidates:
        logger.warning("enrich_on_demand: no JSON for %s/%s", company_slug, article_id)
        return None
    json_path = candidates[0]

    # 2. Load existing payload
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    pipeline_data = payload.get("pipeline") or {}
    existing_insight = payload.get("insight") or {}

    # 3. Check if already enriched with CURRENT engine version
    CURRENT_SCHEMA_VERSION = "2.0-primitives-l2"
    stored_version = (payload.get("meta") or {}).get("schema_version", "")
    is_current = stored_version == CURRENT_SCHEMA_VERSION

    if not force and is_current and existing_insight.get("headline") and existing_insight.get("core_mechanism"):
        logger.info("enrich_on_demand: %s already enriched (v%s), returning cached", article_id, stored_version)
        return payload

    # 4. Get company and build PipelineResult
    company = get_company(company_slug)

    # 4a. Always re-run full pipeline (stages 1-9) from raw input article
    #     to pick up latest event keywords, ontology edges, and primitive mappings.
    result = _rerun_full_pipeline(article_id, company_slug, company)

    # 4b. Fall back to reconstructing from stored pipeline data if input not found
    if result is None:
        result = _reconstruct_pipeline_result(pipeline_data)

    if result.rejected:
        logger.info("enrich_on_demand: %s is rejected, skipping", article_id)
        return payload

    # 4c. Check content quality — flag paywall/thin articles
    # Check both raw content and NLP extraction quality
    raw_content_len = len(result.title or "")
    if hasattr(result, "nlp") and result.nlp:
        raw_content_len += len(result.nlp.narrative_core_claim or "")
        raw_content_len += len(result.nlp.narrative_implied_causation or "")
    if raw_content_len < 100:
        logger.warning(
            "enrich_on_demand: thin content (%d chars) for %s — likely paywalled",
            content_len, article_id,
        )
        result._thin_content = True  # type: ignore[attr-defined]
    else:
        result._thin_content = False  # type: ignore[attr-defined]

    # 5. Stage 10: Deep insight generation
    logger.info("enrich_on_demand: generating deep insight for %s", article_id)
    insight = generate_deep_insight(result, company)
    if not insight:
        logger.warning("enrich_on_demand: insight generation failed for %s", article_id)
        return payload

    # 6. Stage 11: Perspective transformation
    perspectives: dict[str, Any] = {}
    for lens in ("esg-analyst", "cfo", "ceo"):
        perspectives[lens] = transform_for_perspective(insight, result, lens)

    # 7. Stage 12: Recommendations
    recs = generate_recommendations(insight, result, company)

    # 8. Phase I: LLM intelligence layers
    intelligence = _run_intelligence_layers(result, insight, company)

    # 9. Write enriched payload back to disk
    written = write_insight(result, insight, perspectives, recs)

    # 9.5 Stage 12.5: Self-evolving ontology — collect discoveries (~5ms)
    try:
        from engine.ontology.discovery.collector import collect_discoveries
        discoveries = collect_discoveries(result, insight, company_slug)
        if discoveries > 0:
            logger.info("enrich_on_demand: %d discovery candidates collected", discoveries)
    except Exception as exc:
        logger.debug("discovery collection skipped: %s", exc)

    # 10. Reload and merge intelligence layers into the payload
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    if intelligence:
        payload["intelligence"] = intelligence
        json_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    elapsed = round(time.perf_counter() - started, 2)
    logger.info(
        "enrich_on_demand: %s enriched in %.1fs (insight=%s, recs=%d)",
        article_id,
        elapsed,
        bool(insight),
        len(recs.recommendations) if recs and not recs.do_nothing else 0,
    )
    return payload


def _rerun_full_pipeline(
    article_id: str, company_slug: str, company: Company
) -> Any | None:
    """Re-run stages 1-9 from the raw input article for fresh analysis.

    Looks up the article in data/inputs/news/{company_slug}/ and runs
    process_article() to get updated event classification, relevance,
    causal chains, etc. with latest ontology keywords.

    Returns PipelineResult or None if input article not found.
    """
    from engine.analysis.pipeline import process_article

    inputs_dir = get_data_path("inputs", "news", company_slug)
    if not inputs_dir.exists():
        logger.warning("_rerun_full_pipeline: no inputs dir for %s", company_slug)
        return None

    # Find the raw input article by ID
    candidates = list(inputs_dir.glob(f"*{article_id}*"))
    if not candidates:
        logger.warning("_rerun_full_pipeline: no input file for %s/%s", company_slug, article_id)
        return None

    import json as _json
    raw = _json.loads(candidates[0].read_text(encoding="utf-8"))
    article = {
        "id": raw.get("id", article_id),
        "title": raw.get("title", ""),
        "content": raw.get("content", ""),
        "summary": raw.get("summary", ""),
        "source": raw.get("source", ""),
        "url": raw.get("url", ""),
        "published_at": raw.get("published_at", ""),
        "metadata": raw.get("metadata", {}),
    }

    logger.info("_rerun_full_pipeline: re-running stages 1-9 for %s", article_id)
    result = process_article(article, company)
    logger.info(
        "_rerun_full_pipeline: %s → tier=%s, event=%s, score=%.1f",
        article_id,
        result.tier,
        result.event.label if result.event else "none",
        result.relevance.adjusted_total if result.relevance else 0,
    )
    return result


def _run_intelligence_layers(
    result: Any, insight: Any, company: Company
) -> dict[str, Any]:
    """Phase I: Run additional LLM intelligence layers on demand.

    Returns a dict with keys: competitive_brief, causal_narrative,
    anticipated_qa, sentiment_trajectory.
    """
    import os

    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return {}

    client = OpenAI(api_key=api_key)
    model = "gpt-4.1-mini"
    intelligence: dict[str, Any] = {}

    headline = insight.headline if hasattr(insight, "headline") else ""
    company_name = company.name
    industry = company.industry

    # I2: Competitive intelligence brief
    try:
        from engine.ontology.intelligence import query_competitors

        competitors = query_competitors(company.slug)
        if competitors:
            peer_names = ", ".join(competitors[:3])
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": f"You are a competitive intelligence analyst for {company_name} in the {industry} sector. Be specific with company names and ₹ figures.",
                    },
                    {
                        "role": "user",
                        "content": f'Given this ESG event: "{headline}", how would competitors {peer_names} be affected or respond? What has the industry done in similar situations? Max 200 words.',
                    },
                ],
                max_tokens=400,
                temperature=0.3,
            )
            intelligence["competitive_brief"] = resp.choices[0].message.content or ""
    except Exception as exc:
        logger.warning("competitive_brief failed: %s", exc)

    # I4: Causal narrative
    try:
        chains = result.causal_chains if hasattr(result, "causal_chains") else []
        if chains:
            chains_text = "\n".join(
                f"- {c.relationship_type} (hops={c.hops}): {c.explanation}"
                if hasattr(c, "relationship_type")
                else f"- {c.get('relationship_type', '')} (hops={c.get('hops', 0)}): {c.get('explanation', '')}"
                for c in chains[:5]
            )
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an ESG risk transmission analyst. Write a 3-4 sentence narrative explaining HOW the ESG event transmits through the company's operations, supply chain, or regulatory environment to create financial risk. Be specific and quantitative where possible.",
                    },
                    {
                        "role": "user",
                        "content": f"Event: {headline}\nCompany: {company_name} ({industry})\nCausal chains:\n{chains_text}",
                    },
                ],
                max_tokens=300,
                temperature=0.3,
            )
            intelligence["causal_narrative"] = resp.choices[0].message.content or ""
    except Exception as exc:
        logger.warning("causal_narrative failed: %s", exc)

    # I5: Executive Q&A pre-generation
    try:
        insight_json = json.dumps(insight.to_dict() if hasattr(insight, "to_dict") else {}, default=str)[:3000]
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a C-suite ESG advisor. Generate the 5 questions a CFO, CEO, or Board member would most likely ask about this intelligence brief, with concise answers (2-3 sentences each). Return JSON: {\"qa\": [{\"question\": \"...\", \"answer\": \"...\"}]}",
                },
                {
                    "role": "user",
                    "content": f"Company: {company_name} ({industry})\nInsight:\n{insight_json}",
                },
            ],
            max_tokens=600,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        qa_data = json.loads(resp.choices[0].message.content or "{}")
        intelligence["anticipated_qa"] = qa_data.get("qa", [])
    except Exception as exc:
        logger.warning("anticipated_qa failed: %s", exc)

    # I6: Sentiment trajectory
    try:
        import sqlite3

        db_path = get_data_path("snowkap.db")
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT title, json_path FROM article_index WHERE company_slug = ? ORDER BY published_at DESC LIMIT 6",
                (company.slug,),
            ).fetchall()
            conn.close()

            if len(rows) >= 2:
                prev_summaries = []
                for r in rows[1:6]:  # skip current article
                    try:
                        p = json.loads(Path(r["json_path"]).read_text(encoding="utf-8"))
                        nlp = p.get("pipeline", {}).get("nlp", {})
                        prev_summaries.append(
                            f"- {r['title'][:80]} (sentiment={nlp.get('sentiment', 0)})"
                        )
                    except Exception:
                        continue

                if prev_summaries:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[
                            {
                                "role": "system",
                                "content": 'You are a sentiment trend analyst. Return JSON: {"direction": "improving|declining|stable", "summary": "<1 sentence>", "emerging_themes": ["theme1", "theme2"]}',
                            },
                            {
                                "role": "user",
                                "content": f"Company: {company_name}\nRecent articles:\n"
                                + "\n".join(prev_summaries)
                                + f"\n\nNew article: {headline} (sentiment={getattr(result.nlp, 'sentiment', 0) if hasattr(result, 'nlp') else 0})",
                            },
                        ],
                        max_tokens=200,
                        temperature=0.2,
                        response_format={"type": "json_object"},
                    )
                    trajectory = json.loads(resp.choices[0].message.content or "{}")
                    intelligence["sentiment_trajectory"] = trajectory
    except Exception as exc:
        logger.warning("sentiment_trajectory failed: %s", exc)

    return intelligence


def _reconstruct_pipeline_result(pipeline_data: dict[str, Any]) -> Any:
    """Reconstruct a PipelineResult-like object from stored JSON.

    This is needed because the on-demand enrichment gets raw JSON from disk,
    but insight_generator and recommendation_engine expect PipelineResult.
    """
    from engine.analysis.pipeline import PipelineResult, NLPExtraction, ESGThemeTags
    from engine.nlp.event_classifier import EventClassification
    from engine.analysis.relevance_scorer import RelevanceScore

    nlp_data = pipeline_data.get("nlp") or {}
    themes_data = pipeline_data.get("themes") or {}
    event_data = pipeline_data.get("event") or {}
    rel_data = pipeline_data.get("relevance") or {}
    risk_data = pipeline_data.get("risk")

    # Reconstruct NLPExtraction
    nlp = NLPExtraction(
        sentiment=nlp_data.get("sentiment", 0),
        sentiment_confidence=nlp_data.get("sentiment_confidence", 0.5),
        tone=nlp_data.get("tone", ["neutral"]) if isinstance(nlp_data.get("tone"), list) else [nlp_data.get("tone", "neutral")],
        narrative_core_claim=nlp_data.get("narrative_core_claim") or nlp_data.get("core_claim", ""),
        narrative_implied_causation=nlp_data.get("narrative_implied_causation") or nlp_data.get("implied_causation", ""),
        narrative_stakeholder_framing=str(nlp_data.get("narrative_stakeholder_framing") or nlp_data.get("stakeholder_framing", "")),
        entities=nlp_data.get("entities", []),
        entity_types=nlp_data.get("entity_types", {}),
        financial_signal=nlp_data.get("financial_signal", {}),
        regulatory_references=nlp_data.get("regulatory_references", []),
        esg_pillar=nlp_data.get("esg_pillar", "") or themes_data.get("primary_pillar", ""),
        esg_topics=nlp_data.get("esg_topics", []),
        content_type=nlp_data.get("content_type", "news"),
        urgency=nlp_data.get("urgency", "medium"),
        time_horizon=nlp_data.get("time_horizon", "medium-term"),
        source_credibility_tier=nlp_data.get("source_credibility_tier", 3),
        climate_events=nlp_data.get("climate_events", []),
        raw_llm_response=nlp_data.get("raw_llm_response", {}),
    )

    themes = ESGThemeTags(
        primary_theme=themes_data.get("primary_theme", ""),
        primary_pillar=themes_data.get("primary_pillar", ""),
        primary_sub_metrics=themes_data.get("primary_sub_metrics", []),
        secondary_themes=themes_data.get("secondary_themes", []),
        confidence=themes_data.get("confidence", 0),
        method=themes_data.get("method", "llm"),
    )

    event = EventClassification(
        event_id=event_data.get("event_id", "event_default"),
        label=event_data.get("label") or event_data.get("event_type_label", "Unclassified"),
        score_floor=event_data.get("score_floor", 2),
        score_ceiling=event_data.get("score_ceiling", 6),
        financial_transmission=event_data.get("financial_transmission", ""),
        matched_keywords=event_data.get("matched_keywords", []),
        has_financial_quantum=event_data.get("has_financial_quantum", False),
        financial_amount_cr=event_data.get("financial_amount_cr"),
    )

    relevance = RelevanceScore(
        total=rel_data.get("total", 0),
        tier=pipeline_data.get("tier", "SECONDARY"),
        esg_correlation=rel_data.get("esg_correlation", 0),
        financial_impact=rel_data.get("financial_impact", 0),
        compliance_risk=rel_data.get("compliance_risk", 0),
        supply_chain_impact=rel_data.get("supply_chain_impact", 0),
        people_impact=rel_data.get("people_impact", 0),
        materiality_weight=rel_data.get("materiality_weight", 0.5),
        adjusted_total=rel_data.get("adjusted_total", 0),
        rejection_reason="",
        ontology_queries=0,
    )

    # Reconstruct causal chains as simple objects with attribute access
    from dataclasses import dataclass, field as dc_field

    @dataclass
    class _Chain:
        relationship_type: str = ""
        hops: int = 0
        impact_score: float = 0.0
        explanation: str = ""
        nodes: list[str] = dc_field(default_factory=list)
        edges: list[str] = dc_field(default_factory=list)

    causal_chains = [
        _Chain(
            relationship_type=c.get("relationship_type", ""),
            hops=c.get("hops", 0),
            impact_score=c.get("impact_score", 0),
            explanation=c.get("explanation", ""),
            nodes=c.get("nodes") or c.get("path", []),
            edges=c.get("edges", []),
        )
        for c in (pipeline_data.get("causal_chains") or [])
    ]

    # Reconstruct frameworks
    @dataclass
    class _FW:
        framework_id: str = ""
        framework_label: str = ""
        relevance: float = 0.0
        is_mandatory: bool = False
        profitability_link: str = ""
        triggered_sections: list[str] = dc_field(default_factory=list)
        triggered_by_themes: list[str] = dc_field(default_factory=list)

        def to_dict(self) -> dict:
            return {
                "framework_id": self.framework_id,
                "framework_label": self.framework_label,
                "relevance": self.relevance,
                "is_mandatory": self.is_mandatory,
                "profitability_link": self.profitability_link,
                "triggered_sections": self.triggered_sections,
                "triggered_by_themes": self.triggered_by_themes,
            }

    frameworks = [
        _FW(
            framework_id=f.get("framework_id", ""),
            framework_label=f.get("framework_label", ""),
            relevance=f.get("relevance", 0),
            is_mandatory=f.get("is_mandatory", False),
            profitability_link=f.get("profitability_link", ""),
            triggered_sections=f.get("triggered_sections", []),
        )
        for f in (pipeline_data.get("frameworks") or [])
    ]

    # Reconstruct risk
    risk = None
    if risk_data:
        from engine.analysis.risk_assessor import RiskAssessment, RiskScore

        def _rebuild_risks(risk_list: list[dict]) -> list[RiskScore]:
            return [
                RiskScore(
                    category=r.get("category", ""),
                    probability=r.get("probability", 0),
                    exposure=r.get("exposure", 0),
                    raw_score=r.get("raw_score", 0),
                    industry_weight=r.get("industry_weight", 1.0),
                    adjusted_score=r.get("adjusted_score", 0),
                    level=r.get("level", "LOW"),
                    lead_indicators=r.get("lead_indicators", []),
                    lag_indicators=r.get("lag_indicators", []),
                )
                for r in risk_list
            ]

        risk = RiskAssessment(
            esg_risks=_rebuild_risks(risk_data.get("esg_risks") or []),
            temples_risks=_rebuild_risks(risk_data.get("temples_risks") or []),
            top_risks=_rebuild_risks(risk_data.get("top_risks") or []),
            aggregate_score=risk_data.get("aggregate_score", 0),
            ontology_queries=risk_data.get("ontology_queries", 0),
        )

    return PipelineResult(
        article_id=pipeline_data.get("article_id", ""),
        title=pipeline_data.get("title", ""),
        url=pipeline_data.get("url", ""),
        source=pipeline_data.get("source", ""),
        published_at=pipeline_data.get("published_at", ""),
        company_slug=pipeline_data.get("company_slug", ""),
        nlp=nlp,
        themes=themes,
        event=event,
        relevance=relevance,
        causal_chains=causal_chains,
        frameworks=frameworks,
        risk=risk,
        stakeholders=pipeline_data.get("stakeholders", []),
        sdgs=pipeline_data.get("sdgs", []),
        tier=pipeline_data.get("tier", "SECONDARY"),
        rejected=pipeline_data.get("rejected", False),
        rejection_reason="",
        ontology_query_count=pipeline_data.get("ontology_query_count", 0),
        elapsed_seconds=0,
        stages_executed=pipeline_data.get("stages_executed", []),
    )
