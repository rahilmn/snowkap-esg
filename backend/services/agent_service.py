"""Agent service — LangGraph + Agency agents.

Per MASTER_BUILD_PLAN Phase 5 + Stage 7:
- 9 runtime Agency agent personalities as LangGraph specialist nodes
- Agent routing: Claude classifies user intent → selects specialist
- Personality loading from markdown files
- Full conversation pipeline with tool execution
- Stage 7.3: Agent-to-agent handoff protocol
- Stage 7.6: NEXUS-Lite multi-agent orchestration pipelines
"""

import json
import re
from pathlib import Path
from typing import Any

import structlog

from backend.core.config import settings

logger = structlog.get_logger()

# Directory containing agent personality markdown files
PERSONALITIES_DIR = Path(__file__).parent.parent / "agent" / "personalities"


def load_personality(agent_key: str) -> str:
    """Load an agent's personality prompt from its markdown file."""
    md_path = PERSONALITIES_DIR / f"{agent_key}.md"
    if md_path.exists():
        return md_path.read_text(encoding="utf-8")
    logger.warning("personality_not_found", agent=agent_key, path=str(md_path))
    return f"You are an ESG specialist agent. Provide helpful analysis."


# Agent roster per MASTER_BUILD_PLAN Part 3B: Runtime Agents
AGENT_ROSTER: dict[str, dict[str, Any]] = {
    "supply_chain": {
        "name": "ESG Supply Chain Analyst",
        "personality_key": "supply_chain",
        "keywords": ["supply chain", "supplier", "scope 3", "upstream", "downstream", "procurement", "tier 1", "tier 2", "commodity", "logistics"],
        "tools": ["sparql", "database", "causal_chain"],
    },
    "compliance": {
        "name": "ESG Compliance Monitor",
        "personality_key": "compliance",
        "keywords": ["compliance", "brsr", "gri", "tcfd", "esrs", "regulation", "disclosure", "framework", "reporting", "audit", "gap analysis"],
        "tools": ["sparql", "database", "ontology_rules"],
    },
    "analytics": {
        "name": "ESG Analytics Agent",
        "personality_key": "analytics",
        "keywords": ["analytics", "report", "dashboard", "kpi", "metrics", "trend", "benchmark", "score", "performance", "data"],
        "tools": ["database", "sparql", "prediction"],
    },
    "executive": {
        "name": "CXO Briefing Agent",
        "personality_key": "executive",
        "keywords": ["executive", "summary", "briefing", "cxo", "board", "strategic", "ceo", "cfo", "investor", "financial impact"],
        "tools": ["database", "prediction", "causal_chain"],
    },
    "trend": {
        "name": "ESG Trend Scout",
        "personality_key": "trend",
        "keywords": ["trend", "forecast", "emerging", "future", "prediction", "signal", "outlook", "upcoming", "next year"],
        "tools": ["database", "prediction", "causal_chain"],
    },
    "stakeholder": {
        "name": "Stakeholder Voice Agent",
        "personality_key": "stakeholder",
        "keywords": ["stakeholder", "feedback", "investor", "rating", "esg score", "perception", "reputation", "community", "employee"],
        "tools": ["database", "sparql"],
    },
    "opportunity": {
        "name": "ESG Opportunity Finder",
        "personality_key": "opportunity",
        "keywords": ["opportunity", "growth", "green revenue", "market", "advantage", "roi", "investment", "carbon credit", "sustainable finance"],
        "tools": ["database", "prediction", "sparql"],
    },
    "content": {
        "name": "ESG Report Writer",
        "personality_key": "content",
        "keywords": ["content", "write", "newsletter", "sustainability report", "draft", "narrative", "disclosure", "section"],
        "tools": ["database", "sparql", "ontology_rules"],
    },
    "legal": {
        "name": "Regulatory Intelligence Agent",
        "personality_key": "legal",
        "keywords": ["legal", "regulatory", "cbam", "sebi", "epa", "law", "policy", "penalty", "deadline", "mandate"],
        "tools": ["database", "sparql", "ontology_rules"],
    },
    "competitive": {
        "name": "ESG Competitive Intelligence",
        "personality_key": "competitive",
        "keywords": ["competitor", "competitive", "rival", "peer", "market position", "benchmark",
                     "industry comparison", "market share", "compare", "versus", "vs"],
        "tools": ["sparql", "database", "causal_chain"],
    },
    "validator": {
        "name": "ESG Recommendation Validator",
        "personality_key": "validator",
        "keywords": ["validate", "verify", "check", "confidence", "audit", "review recommendations"],
        "tools": ["database", "sparql"],
    },
}


def route_to_specialist(question: str) -> str:
    """Route user question to the best specialist agent based on keyword scoring.

    Uses weighted keyword matching. Falls back to 'analytics' as the default generalist.
    """
    question_lower = question.lower()
    best_match = "analytics"  # Default agent
    best_score = 0

    for agent_id, config in AGENT_ROSTER.items():
        score = sum(1 for kw in config["keywords"] if kw in question_lower)
        if score > best_score:
            best_score = score
            best_match = agent_id

    logger.info("agent_routed", agent=best_match, score=best_score, question_preview=question[:80])
    return best_match


async def classify_intent_with_llm(question: str, conversation_context: str = "") -> dict:
    """Use Claude to classify user intent and select the best specialist.

    Falls back to keyword routing if LLM is unavailable.
    """
    from backend.core import llm as llm_client

    if not llm_client.is_configured():
        return {
            "agent": route_to_specialist(question),
            "method": "keyword",
            "intent": "general_query",
        }

    try:
        agent_descriptions = "\n".join(
            f"- {aid}: {cfg['name']} — keywords: {', '.join(cfg['keywords'][:5])}"
            for aid, cfg in AGENT_ROSTER.items()
        )

        text = await llm_client.chat(
            system="You are a routing classifier. Given a user question about ESG/sustainability, select the most appropriate specialist agent. Respond with ONLY valid JSON.",
            messages=[{
                "role": "user",
                "content": f"""Available specialist agents:
{agent_descriptions}

User question: "{question}"
{f'Conversation context: {conversation_context[:500]}' if conversation_context else ''}

Respond with JSON: {{"agent": "<agent_id>", "intent": "<brief_intent>", "confidence": 0.0-1.0}}""",
            }],
            max_tokens=200,
        )

        text = text.strip()
        if text.startswith("{"):
            result = json.loads(text)
            if result.get("agent") in AGENT_ROSTER:
                return {**result, "method": "llm"}

    except Exception as e:
        logger.warning("llm_classification_failed", error=str(e))

    # Fallback to keyword routing
    return {
        "agent": route_to_specialist(question),
        "method": "keyword_fallback",
        "intent": "general_query",
    }


async def run_agent_conversation(
    tenant_id: str,
    user_id: str,
    question: str,
    agent_id: str | None = None,
    db=None,
    article_id: str | None = None,
    designation: str | None = None,
) -> dict:
    """Run a full agent conversation turn.

    1. Check for NEXUS pipeline trigger (Stage 7.6)
    2. Classify intent → select agent (or use provided agent_id)
    3. Load agent personality
    4. Gather relevant data via tools
    5. Generate response with Claude using agent personality
    6. Detect handoff suggestions (Stage 7.3)

    Returns the agent's response with metadata.
    """
    from backend.agent.memory import memory_manager

    # Stage 7.6: Check for NEXUS pipeline trigger
    if not agent_id:
        pipeline_id = detect_nexus_pipeline(question)
        if pipeline_id:
            logger.info("nexus_pipeline_detected", pipeline=pipeline_id)
            return await run_nexus_pipeline(
                tenant_id=tenant_id,
                user_id=user_id,
                question=question,
                pipeline_id=pipeline_id,
                db=db,
                designation=designation,
            )

    # Step 1: Route to specialist
    if agent_id and agent_id in AGENT_ROSTER:
        classification = {"agent": agent_id, "method": "user_selected", "intent": "direct"}
    else:
        # Get conversation context for better routing
        recent = await memory_manager.get_memory(tenant_id, user_id, last_n=3)
        context = " ".join(m.get("content", "") for m in recent)
        classification = await classify_intent_with_llm(question, context)

    selected_agent = classification["agent"]
    agent_config = AGENT_ROSTER[selected_agent]

    # Step 2: Load personality
    personality = load_personality(agent_config["personality_key"])

    # Stage 7.3: Add handoff awareness to personality
    adjacent = _HANDOFF_MAP.get(selected_agent, [])
    if adjacent:
        adjacent_names = [AGENT_ROSTER[a]["name"] for a in adjacent if a in AGENT_ROSTER]
        personality += (
            f"\n\n## Handoff Awareness"
            f"\nIf the user's question crosses into another domain, note it in your response."
            f"\nAdjacent specialists: {', '.join(adjacent_names)}"
        )

    # Step 3: Gather tool data (ontology-driven)
    tool_context = await _gather_tool_context(
        tenant_id=tenant_id,
        question=question,
        tools=agent_config["tools"],
        db=db,
        article_id=article_id,
    )

    # Step 4: Get conversation memory
    memory_context = await memory_manager.get_context_summary(tenant_id, user_id)
    recent_messages = await memory_manager.get_memory(tenant_id, user_id, last_n=5)

    # Stage 7.4: Enrich with cross-agent topic context
    from backend.agent.memory import extract_topics
    question_topics = extract_topics(question)
    if question_topics:
        topic_memory = await memory_manager.get_topic_memory(
            tenant_id, user_id, question_topics, last_n=3,
        )
        if topic_memory:
            cross_context = "\n".join(
                f"[{m.get('metadata', {}).get('agent', '?')}] {m.get('content', '')[:200]}"
                for m in topic_memory
            )
            if memory_context:
                memory_context += f"\n\n## Related Prior Context\n{cross_context}"
            else:
                memory_context = f"## Related Prior Context\n{cross_context}"

    # Step 4b: Resolve role profile for personalization
    role_profile = None
    if designation:
        from backend.core.permissions import map_designation_to_role
        from backend.services.role_curation import get_role_profile
        mapped_role = map_designation_to_role(designation)
        role_profile = get_role_profile(mapped_role)

    # Step 5: Generate response
    response = await _generate_response(
        personality=personality,
        question=question,
        tool_context=tool_context,
        memory_context=memory_context,
        recent_messages=recent_messages,
        tenant_id=tenant_id,
        role_profile=role_profile,
        designation=designation,
    )

    # Step 6: Store conversation turn in memory
    await memory_manager.add_message(
        tenant_id, user_id, "user", question,
        metadata={"agent": selected_agent},
    )
    await memory_manager.add_message(
        tenant_id, user_id, "assistant", response,
        metadata={
            "agent": selected_agent,
            "classification": classification,
            "confidence": classification.get("confidence", 0.8),
        },
    )

    # Stage 7.3: Detect handoff suggestion
    handoff = detect_handoff(selected_agent, response, question)

    result = {
        "response": response,
        "agent": {
            "id": selected_agent,
            "name": agent_config["name"],
        },
        "classification": classification,
        "tools_used": list(tool_context.keys()),
    }

    if handoff:
        result["handoff_suggestion"] = handoff
        logger.info(
            "handoff_detected",
            from_agent=handoff["from"],
            to_agent=handoff["to"],
            signals=handoff["signals"],
        )

    return result


async def _gather_tool_context(
    tenant_id: str,
    question: str,
    tools: list[str],
    db=None,
    article_id: str | None = None,
) -> dict[str, Any]:
    """Run relevant tools to gather ONTOLOGY-DRIVEN context for the agent.

    Phase B2: Enhanced to be intelligence-driven, not generic.
    Always fetches: company profile, relevant frameworks, recent high-priority articles.
    When article_id is provided: fetches full article data, causal chains, climate risks.
    """
    from backend.agent.tools import TOOL_REGISTRY

    context = {}
    question_lower = question.lower()

    # === ALWAYS: Fetch company + ontology profile (the agent needs this for EVERY question) ===
    try:
        from backend.ontology.jena_client import jena_client
        graph_uri = f"urn:snowkap:tenant:{tenant_id}"

        # Company + industry + frameworks from Jena
        company_sparql = f"""
        PREFIX snowkap: <http://snowkap.com/ontology/esg#>
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?name ?type ?label WHERE {{
            GRAPH <{graph_uri}> {{
                ?s rdf:type ?type .
                ?s rdfs:label ?label .
                OPTIONAL {{ ?s rdfs:label ?name }}
            }}
        }} LIMIT 50
        """
        sparql_result = await jena_client.query(company_sparql, tenant_id=tenant_id)
        bindings = sparql_result.get("results", {}).get("bindings", [])

        companies = []
        frameworks = []
        material_issues = []
        for b in bindings:
            type_val = b.get("type", {}).get("value", "")
            label = b.get("label", {}).get("value", "")
            if "Company" in type_val:
                companies.append(label)
            elif "Framework" in type_val:
                frameworks.append(label)
            elif "MaterialIssue" in type_val:
                material_issues.append(label)

        context["ontology_profile"] = {
            "companies": companies,
            "frameworks": frameworks,
            "material_issues": material_issues,
            "total_triples": len(bindings),
        }
    except Exception as e:
        logger.debug("ontology_profile_fetch_failed", error=str(e))

    # === ALWAYS: Fetch company facilities + climate risks ===
    if db:
        try:
            from sqlalchemy import select
            from backend.models.company import Company, Facility
            comp_result = await db.execute(
                select(Company).where(Company.tenant_id == tenant_id)
            )
            company = comp_result.scalars().first()
            if company:
                context["company"] = {
                    "name": company.name,
                    "industry": company.industry,
                    "domain": company.domain,
                }
                fac_result = await db.execute(
                    select(Facility).where(
                        Facility.company_id == company.id,
                        Facility.tenant_id == tenant_id,
                    )
                )
                facilities = fac_result.scalars().all()
                context["facilities"] = [
                    {
                        "name": f.name,
                        "city": f.city,
                        "country": f.country,
                        "type": f.facility_type,
                        "climate_risk": f.climate_risk_zone,
                    }
                    for f in facilities
                ]
        except Exception as e:
            logger.debug("company_context_failed", error=str(e))

    # === ALWAYS: Fetch recent high-priority articles ===
    if db and "database" in tools:
        try:
            from sqlalchemy import select
            from backend.models.news import Article
            art_result = await db.execute(
                select(Article).where(
                    Article.tenant_id == tenant_id,
                    Article.priority_score.isnot(None),
                ).order_by(Article.priority_score.desc()).limit(5)
            )
            articles = art_result.scalars().all()
            context["recent_high_priority_articles"] = [
                {
                    "title": a.title,
                    "priority": f"{a.priority_level} ({a.priority_score})",
                    "type": a.content_type,
                    "urgency": a.urgency,
                    "sentiment": a.sentiment_score,
                    "climate_events": a.climate_events,
                    "insight": (a.executive_insight or "")[:200],
                }
                for a in articles
            ]
        except Exception as e:
            logger.debug("articles_context_failed", error=str(e))

    # === ARTICLE-SPECIFIC: Full article + causal chain + recommendations ===
    if article_id and db:
        try:
            from sqlalchemy import select
            from backend.models.news import Article, ArticleScore, CausalChain
            art = await db.execute(
                select(Article).where(Article.id == article_id, Article.tenant_id == tenant_id)
            )
            article = art.scalar_one_or_none()
            if article:
                context["article_context"] = {
                    "title": article.title,
                    "summary": article.summary,
                    "content_preview": (article.content or "")[:500],
                    "sentiment_score": article.sentiment_score,
                    "urgency": article.urgency,
                    "content_type": article.content_type,
                    "climate_events": article.climate_events,
                    "priority_score": article.priority_score,
                    "priority_level": article.priority_level,
                    "executive_insight": article.executive_insight,
                    "frameworks": [e.get("type") for e in (article.entities or []) if isinstance(e, dict) and e.get("type") == "framework"],
                }

                # v2.0: Include pre-computed intelligence module data
                if article.nlp_extraction:
                    nlp = article.nlp_extraction
                    context["article_context"]["nlp_sentiment"] = nlp.get("sentiment", {}).get("label")
                    context["article_context"]["nlp_tone"] = nlp.get("tone", {}).get("primary")
                    context["article_context"]["core_claim"] = nlp.get("narrative_arc", {}).get("core_claim")
                    context["article_context"]["source_tier"] = nlp.get("source_credibility", {}).get("tier")
                if article.esg_themes:
                    context["article_context"]["esg_primary_theme"] = article.esg_themes.get("primary_theme")
                    context["article_context"]["esg_secondary_themes"] = [
                        t.get("theme") for t in article.esg_themes.get("secondary_themes", [])
                    ]
                if article.risk_matrix:
                    top = article.risk_matrix.get("top_risks", [])[:3]
                    context["article_context"]["top_risks"] = [
                        f"{r.get('category_name')}={r.get('risk_score', '')}({r.get('classification', '')})"
                        for r in top
                    ]
                    context["article_context"]["aggregate_risk"] = article.risk_matrix.get("aggregate_score")
                    context["article_context"]["risk_matrix_mode"] = article.risk_matrix.get("mode", "spotlight")
                    context["article_context"]["relevance_score"] = article.relevance_score
                if article.framework_matches:
                    context["article_context"]["framework_matches"] = [
                        f"{m.get('framework_id')}:{','.join(m.get('triggered_sections', [])[:2])}"
                        for m in (article.framework_matches or [])[:5]
                    ]

                # Causal chains for this article
                chains = await db.execute(
                    select(CausalChain).where(
                        CausalChain.article_id == article_id,
                        CausalChain.tenant_id == tenant_id,
                    )
                )
                context["causal_chains"] = [
                    {
                        "relationship_type": c.relationship_type,
                        "hops": c.hops,
                        "impact_score": c.impact_score,
                        "explanation": c.explanation,
                        "frameworks": c.framework_alignment,
                    }
                    for c in chains.scalars().all()
                ]

                # Scores
                scores = await db.execute(
                    select(ArticleScore).where(
                        ArticleScore.article_id == article_id,
                        ArticleScore.tenant_id == tenant_id,
                    )
                )
                context["impact_scores"] = [
                    {
                        "impact_score": s.impact_score,
                        "causal_hops": s.causal_hops,
                        "frameworks": s.frameworks,
                        "financial_exposure": s.financial_exposure,
                    }
                    for s in scores.scalars().all()
                ]
        except Exception as e:
            logger.debug("article_context_failed", error=str(e))

    # === TOOL-SPECIFIC: Additional tools based on agent type ===
    for tool_name in tools:
        tool_info = TOOL_REGISTRY.get(tool_name)
        if not tool_info:
            continue

        try:
            if tool_name == "causal_chain":
                import re
                quoted = re.findall(r'"([^"]+)"', question)
                if quoted:
                    result = await tool_info["fn"](tenant_id=tenant_id, entity_name=quoted[0])
                    context["causal_chain_lookup"] = result

            elif tool_name == "prediction" and db:
                result = await tool_info["fn"](tenant_id=tenant_id, db=db)
                context["predictions"] = result

            elif tool_name == "ontology_rules":
                result = await tool_info["fn"](tenant_id=tenant_id, action="list")
                context["ontology_rules"] = result

        except Exception as e:
            logger.warning("tool_context_error", tool=tool_name, error=str(e))

    return context


async def _generate_response(
    personality: str,
    question: str,
    tool_context: dict,
    memory_context: str | None,
    recent_messages: list[dict],
    tenant_id: str,
    role_profile: dict | None = None,
    designation: str | None = None,
) -> str:
    """Generate the agent's response using Claude with personality and context."""
    from backend.core import llm as llm_client

    if not llm_client.is_configured():
        return f"I'd be happy to help with your ESG question, but the AI service is not configured. Please set the OPENAI_API_KEY environment variable."

    try:
        # Build system prompt with personality + ontology-driven intelligence
        system_parts = [
            personality,
            "\n## Platform Context",
            "You are part of the SNOWKAP ESG Intelligence Platform.",
            "Your responses MUST be grounded in the company's knowledge graph data, "
            "causal chain analysis, and framework intelligence shown below.",
            "Do NOT give generic ESG advice. Reference specific data points, "
            "framework codes (BRSR:P6, GRI:305), facility risks, and causal chains.",
            f"Current tenant: {tenant_id}",
            "\n## CRITICAL RULES",
            "- ONLY reference data points, framework codes, and metrics shown in the sections below",
            "- Do NOT invent or fabricate financial figures, amounts, or percentages not in the data",
            "- Do NOT mention framework codes (GRI:303, etc.) that are NOT listed in the provided frameworks",
            "- If financial_exposure is null/absent, say 'Financial impact data not yet available'",
            "- If you need data that isn't available, explicitly state 'This data is not available in the current analysis'",
            "- Ground EVERY claim in a specific data point from the sections below",
        ]

        # Add role-personalized audience context with sharp output formatting
        if role_profile and designation:
            from backend.core.permissions import map_designation_to_role
            role_key = map_designation_to_role(designation)

            # ── Universal severity/radar preamble (injected for ALL roles) ──
            system_parts.append(
                "\n## IMPACT SEVERITY ASSESSMENT — MANDATORY"
                "\nBefore giving any analysis, you MUST make a clear verdict using the data signals below."
                "\nLook at: priority_score (0-100), impact_score (0-10), urgency field, sentiment_score,"
                "\nfinancial_signal, causal_hops, and causal chain relationship_type."
                "\n"
                "\nSeverity rules (use the data — do NOT guess):"
                "\n- priority_score >= 75 OR urgency='critical' → HIGH SEVERITY — needs action this week"
                "\n- priority_score 50-74 AND urgency='high' → MODERATE SEVERITY — needs attention this month"
                "\n- priority_score 50-74 AND urgency='medium' → LOW-MODERATE — monitor, no immediate action"
                "\n- priority_score < 50 → LOW SEVERITY — awareness only, no action needed"
                "\n- If impact_score > 7 → bump severity up one level"
                "\n- If causal_hops == 0 (direct impact) → bump severity up one level"
                "\n- If financial_signal amount > 1000 crore → flag as financially material"
                "\n"
                "\nYou MUST state the verdict clearly in your response. If the data says this is low impact,"
                "\nSAY SO — do not inflate. If the data says this is critical, say so with urgency."
                "\nNEVER use words like 'might', 'could', 'potentially' without qualifying with the actual data."
                "\nInstead say: 'Impact score is 7.5/10 — this is material' or 'Priority 43/100 — monitor only'."
            )

            # ── Role-specific output templates ──
            _ROLE_OUTPUT_TEMPLATES: dict[str, str] = {
                "board_member": (
                    "\n## YOUR AUDIENCE — BOARD MEMBER"
                    "\nBriefing a Board Member. 2 minutes. Governance lens only."
                    "\n"
                    "\n## MANDATORY OUTPUT FORMAT (follow EXACTLY — no deviations)"
                    "\n"
                    "\n━━━ SEVERITY ━━━"
                    "\n🔴 / 🟡 / 🟢  **[HIGH/MODERATE/LOW]** — [One-line: what this means for the board]"
                    "\n`Priority: [X]/100 · Impact: [X]/10 · Urgency: [value] · Causal: [direct/indirect]`"
                    "\n"
                    "\n━━━ VERDICT ━━━"
                    "\n**On your radar?** [YES — requires board attention / WATCH — monitor next quarter / NO — no governance exposure]"
                    "\n[One sentence WHY, grounded in a specific data point]"
                    "\n"
                    "\n━━━ EXPOSURE ━━━"
                    "\n[2 sentences max: fiduciary risk, peer position, what happens if ignored]"
                    "\n"
                    "\n━━━ BOARD ACTION ━━━"
                    "\n→ [Action 1: governance-level directive, terse]"
                    "\n→ [Action 2 if needed, or 'No action required — monitor only']"
                    "\n"
                    "\nRULES: No jargon. No framework codes. Max 150 words after the severity line."
                    "\nIf severity is LOW, say 'No board action required' and stop — do NOT pad with advice."
                ),
                "ceo": (
                    "\n## YOUR AUDIENCE — CEO"
                    "\nAdvising the CEO. Competitive positioning and market narrative lens."
                    "\n"
                    "\n## MANDATORY OUTPUT FORMAT (follow EXACTLY — no deviations)"
                    "\n"
                    "\n━━━ SEVERITY ━━━"
                    "\n🔴 / 🟡 / 🟢  **[HIGH/MODERATE/LOW]** — [One-line competitive signal]"
                    "\n`Priority: [X]/100 · Impact: [X]/10 · Financial: [₹X cr or N/A] · Competitors moving: [Yes/No]`"
                    "\n"
                    "\n━━━ ON YOUR RADAR? ━━━"
                    "\n**[ACT NOW / WATCH / IGNORE]** — [One sentence: why, grounded in data]"
                    "\n"
                    "\n━━━ NARRATIVE SHIFT ━━━"
                    "\n[2-3 sentences: What story does this tell the market? Who gains/loses? Name competitors from data.]"
                    "\n"
                    "\n━━━ STRATEGIC RESPONSE ━━━"
                    "\n→ [Move 1: competitive action + timeline]"
                    "\n→ [Move 2: narrative/positioning play]"
                    "\n"
                    "\n**If we lead:** [one line]"
                    "\n**If we lag:** [one line]"
                    "\n"
                    "\nRULES: CEO language only — 'positioning', 'narrative', 'market signal'."
                    "\nMax 200 words after severity. If severity is LOW, say 'No strategic response needed' and give"
                    "\none line on why this doesn't move the needle. Do NOT manufacture urgency."
                ),
                "cfo": (
                    "\n## YOUR AUDIENCE — CFO"
                    "\nBriefing the CFO. Pure numbers. If you can't quantify, say 'data not available'."
                    "\n"
                    "\n## MANDATORY OUTPUT FORMAT (follow EXACTLY — no deviations)"
                    "\n"
                    "\n━━━ SEVERITY ━━━"
                    "\n🔴 / 🟡 / 🟢  **[HIGH/MODERATE/LOW]** — [One-line financial verdict]"
                    "\n`Priority: [X]/100 · Impact: [X]/10 · Financial signal: [₹X cr / N/A] · Exposure: [direct/indirect]`"
                    "\n"
                    "\n━━━ ON YOUR RADAR? ━━━"
                    "\n**[INVEST / INVESTIGATE / DEFER / IGNORE]** — [One sentence: why, with a number]"
                    "\n"
                    "\n━━━ FINANCIAL IMPACT ━━━"
                    "\n| Dimension | Impact | Confidence |"
                    "\n|-----------|--------|------------|"
                    "\n| Cost of capital | [+/- X bps or 'data not available'] | [High/Med/Low] |"
                    "\n| Valuation effect | [direction + magnitude or 'N/A'] | [High/Med/Low] |"
                    "\n| Cash flow | [capex/opex/revenue effect or 'N/A'] | [High/Med/Low] |"
                    "\n"
                    "\n━━━ INVESTMENT DECISION ━━━"
                    "\n| # | Action | Est. Cost | Payback | ROI |"
                    "\n|---|--------|-----------|---------|-----|"
                    "\n| 1 | [action] | [₹X cr or TBD] | [timeline] | [H/M/L] |"
                    "\n| 2 | [action] | [₹X cr or TBD] | [timeline] | [H/M/L] |"
                    "\n"
                    "\n**Bottom line:** [One sentence: invest / defer / no action needed]"
                    "\n"
                    "\nRULES: Every cell must have a number or explicitly say 'data not available'."
                    "\nNEVER say 'potentially' without a number. If severity is LOW, the investment table should"
                    "\nsay 'No investment required' — do NOT invent actions for low-impact events."
                ),
                "cso": (
                    "\n## YOUR AUDIENCE — CHIEF SUSTAINABILITY OFFICER"
                    "\nBriefing the CSO. Framework-first. Taxonomy-mapped. Deadline-driven."
                    "\n"
                    "\n## MANDATORY OUTPUT FORMAT (follow EXACTLY — no deviations)"
                    "\n"
                    "\n━━━ SEVERITY ━━━"
                    "\n🔴 / 🟡 / 🟢  **[HIGH/MODERATE/LOW]** — [One-line ESG score/framework verdict]"
                    "\n`Priority: [X]/100 · Impact: [X]/10 · Frameworks: [list from data] · Disclosure gap: [Yes/No]`"
                    "\n"
                    "\n━━━ ON YOUR RADAR? ━━━"
                    "\n**[ACT / MONITOR / NOTE]** — [One sentence: why, citing a specific framework section]"
                    "\n"
                    "\n━━━ FRAMEWORK IMPACT ━━━"
                    "\n| Framework | Section | Triggered? | Current Gap | Deadline |"
                    "\n|-----------|---------|------------|-------------|----------|"
                    "\n| [BRSR/GRI/etc.] | [section code] | [Yes/No] | [gap description] | [date or cycle] |"
                    "\n"
                    "\n━━━ ESG SCORE MOVEMENT ━━━"
                    "\n[2-3 sentences: Which indices/ratings move? Direction? By how much? Peer comparison.]"
                    "\n"
                    "\n━━━ ACTION PLAN ━━━"
                    "\n→ [Action 1: cite framework:section] — Due: [date]"
                    "\n→ [Action 2: cite framework:section] — Due: [date]"
                    "\n→ [Action 3 if needed]"
                    "\n"
                    "\nRULES: ALWAYS cite framework:section codes. If severity is LOW-MODERATE or below,"
                    "\nthe action plan should say 'No disclosure updates needed — log for next reporting cycle.'"
                    "\nDo NOT create framework obligations that don't exist."
                ),
                "compliance": (
                    "\n## YOUR AUDIENCE — COMPLIANCE OFFICER"
                    "\nAlerting Compliance / Legal. One question: are we exposed? Facts only."
                    "\n"
                    "\n## MANDATORY OUTPUT FORMAT (follow EXACTLY — no deviations)"
                    "\n"
                    "\n━━━ COMPLIANCE ALERT ━━━"
                    "\n🔴 **RED** / 🟡 **AMBER** / 🟢 **GREEN** — [One-line: obligation at risk or 'no exposure']"
                    "\n`Priority: [X]/100 · Regulatory deadline: [from data or 'none identified'] · Penalty risk: [Y/N]`"
                    "\n"
                    "\n━━━ ON YOUR RADAR? ━━━"
                    "\n**[FILE / REVIEW / MONITOR / NO ACTION]** — [One sentence: why]"
                    "\n"
                    "\n━━━ REGULATORY EXPOSURE ━━━"
                    "\n| Regulation | Section | Obligation | Deadline | Penalty |"
                    "\n|-----------|---------|------------|----------|---------|"
                    "\n| [name] | [section] | [action required] | [date] | [amount] |"
                    "\n(If no regulation is triggered, write: 'No regulatory obligation identified.')"
                    "\n"
                    "\n━━━ GAP STATUS ━━━"
                    "\n[1-2 sentences: current compliance state vs what this event changes. Or 'No gap.']"
                    "\n"
                    "\n━━━ REQUIRED FILINGS ━━━"
                    "\n→ [Filing 1 + deadline, or 'None required']"
                    "\n→ [Filing 2 + deadline if applicable]"
                    "\n"
                    "\nRULES: If GREEN, the entire response after the alert should be 1-2 lines max."
                    "\nDo NOT invent regulatory obligations. Only cite real regulations (SEBI LODR, BRSR Core,"
                    "\nCompanies Act 2013, RBI circulars). If unsure, say 'Regulation not confirmed — verify with legal.'"
                ),
                "supply_chain": (
                    "\n## YOUR AUDIENCE — SUPPLY CHAIN HEAD"
                    "\nBriefing Ops/Supply Chain. Tiers, geography, cost pass-through."
                    "\n"
                    "\n## MANDATORY OUTPUT FORMAT (follow EXACTLY — no deviations)"
                    "\n"
                    "\n━━━ SEVERITY ━━━"
                    "\n🔴 / 🟡 / 🟢  **[HIGH/MODERATE/LOW]** — [One-line ops impact verdict]"
                    "\n`Priority: [X]/100 · Supply chain hops: [X] · Geographic risk: [from facility data] · Cost impact: [Y/N]`"
                    "\n"
                    "\n━━━ ON YOUR RADAR? ━━━"
                    "\n**[ACT / MONITOR / IGNORE]** — [One sentence: why, citing specific tier or facility]"
                    "\n"
                    "\n━━━ IMPACT MAP ━━━"
                    "\n| Tier | Affected | Impact | Severity |"
                    "\n|------|----------|--------|----------|"
                    "\n| Tier 1 (Direct) | [entity/facility] | [description] | [H/M/L] |"
                    "\n| Tier 2 (Indirect) | [entity] | [description] | [H/M/L] |"
                    "\n| Geographic | [region/facility] | [description] | [H/M/L] |"
                    "\n"
                    "\n━━━ COST EFFECT ━━━"
                    "\n[1-2 sentences: margin impact, pass-through feasibility. Or 'No cost impact identified.']"
                    "\n"
                    "\n━━━ OPERATIONAL ACTIONS ━━━"
                    "\n→ [Action 1: specific, tactical]"
                    "\n→ [Action 2 if needed, or 'No action — monitor only']"
                    "\n"
                    "\nRULES: Reference specific facilities from the data. If no supply chain impact,"
                    "\nsay 'No operational exposure' and stop. Max 200 words."
                ),
            }

            role_template = _ROLE_OUTPUT_TEMPLATES.get(role_key)
            if role_template:
                system_parts.append(role_template)
            else:
                # Fallback for unmapped roles
                system_parts.append(
                    f"\n## YOUR AUDIENCE — {designation.upper()}"
                    f"\n{role_profile.get('description', '')}"
                    f"\n"
                    f"\n## MANDATORY OUTPUT FORMAT"
                    f"\n"
                    f"\n━━━ SEVERITY ━━━"
                    f"\n🔴 / 🟡 / 🟢  **[HIGH/MODERATE/LOW]** — [One-line verdict]"
                    f"\n`Priority: [X]/100 · Impact: [X]/10`"
                    f"\n"
                    f"\n━━━ ON YOUR RADAR? ━━━"
                    f"\n**[YES/WATCH/NO]** — [One sentence grounded in data]"
                    f"\n"
                    f"\n━━━ ANALYSIS ━━━"
                    f"\n[3-5 sentences focused on {role_profile.get('primary_focus', 'key impact')}]"
                    f"\n"
                    f"\n━━━ ACTIONS ━━━"
                    f"\n→ [Action or 'No action required']"
                    f"\n"
                    f"\nStyle: {role_profile.get('recommendation_style', 'concise')}."
                    f"\nIf severity is LOW, keep response under 100 words."
                )

        # Add company profile from ontology
        if tool_context.get("company"):
            c = tool_context["company"]
            system_parts.append(
                f"\n## Company Profile\n"
                f"Name: {c.get('name')}\n"
                f"Industry: {c.get('industry')}\n"
                f"Domain: {c.get('domain')}"
            )

        # Add facilities + climate risks
        if tool_context.get("facilities"):
            fac_lines = []
            for f in tool_context["facilities"]:
                risk = f" (CLIMATE RISK: {f['climate_risk']})" if f.get("climate_risk") else ""
                fac_lines.append(f"- {f['name']} in {f.get('city','?')}, {f.get('country','?')} [{f.get('type','')}]{risk}")
            system_parts.append(f"\n## Facilities\n" + "\n".join(fac_lines))

        # Add ontology knowledge (frameworks, material issues)
        if tool_context.get("ontology_profile"):
            op = tool_context["ontology_profile"]
            system_parts.append(
                f"\n## Knowledge Graph\n"
                f"Frameworks: {', '.join(op.get('frameworks', []))}\n"
                f"Material Issues: {', '.join(op.get('material_issues', []))}"
            )

        # Add article-specific context if analyzing an article
        if tool_context.get("article_context"):
            ac = tool_context["article_context"]
            article_parts = [
                f"\n## Article Being Analyzed",
                f"Title: {ac.get('title')}",
                f"Priority: {ac.get('priority_level')} ({ac.get('priority_score')})",
                f"Content Type: {ac.get('content_type')}",
                f"Urgency: {ac.get('urgency')}",
                f"Sentiment: {ac.get('nlp_sentiment', ac.get('sentiment_score'))}",
                f"Tone: {ac.get('nlp_tone', 'unknown')}",
                f"Source Credibility: Tier {ac.get('source_tier', '?')}",
            ]
            if ac.get("esg_primary_theme"):
                article_parts.append(f"ESG Theme: {ac['esg_primary_theme']} + {ac.get('esg_secondary_themes', [])}")
            if ac.get("top_risks"):
                article_parts.append(f"Top Risks: {', '.join(ac['top_risks'])}")
                article_parts.append(f"Aggregate Risk: {ac.get('aggregate_risk', '?')}")
            if ac.get("framework_matches"):
                article_parts.append(f"Frameworks Triggered: {', '.join(ac['framework_matches'])}")
            if ac.get("core_claim"):
                article_parts.append(f"Core Claim: {ac['core_claim']}")
            if ac.get("executive_insight"):
                article_parts.append(f"Executive Insight: {ac['executive_insight']}")
            system_parts.append("\n".join(article_parts))

            # v2.0: Tier-aware dedup instruction
            risk_mode = (ac.get("risk_matrix_mode") or "none")
            if risk_mode == "full":
                # HOME-tier: user saw everything — interpret, don't repeat
                system_parts.append(
                    "\n## CONTEXT DEDUPLICATION — HOME-TIER ARTICLE"
                    "\nThe user has ALREADY reviewed the FULL intelligence brief, including:"
                    "\n- Full 10-category risk matrix with probability × exposure scores"
                    "\n- Framework alignment with triggered sections and compliance implications"
                    "\n- NLP narrative analysis, ESG theme classification, deep insight"
                    "\nDo NOT repeat these data points. Instead:"
                    "\n- INTERPRET the risk matrix for their specific role"
                    "\n- PRIORITIZE which risks need action THIS WEEK"
                    "\n- CONNECT to their company's specific exposure"
                    "\n- RECOMMEND concrete next steps with deadlines"
                )
            else:
                # FEED-tier: user saw spotlight only — you CAN go deeper
                system_parts.append(
                    "\n## CONTEXT — FEED-TIER ARTICLE"
                    f"\nThis is a FEED-tier article (relevance {ac.get('relevance_score', '?')}/10)."
                    "\nThe user has seen: NLP analysis, ESG themes, and a Risk Spotlight (top 3 risks)."
                    "\nThe user has NOT seen: full 10-category risk matrix, framework alignment, or deep insight."
                    "\nIf the user asks for deeper analysis, you SHOULD generate it:"
                    "\n- Run a full risk assessment across all 10 categories"
                    "\n- Cite specific framework sections (BRSR:P6, GRI:305, etc.)"
                    "\n- Provide the same analytical depth as a HOME-tier brief"
                    "\nThe user clicked 'Ask AI' because they want MORE than the pre-computed summary."
                )

        # Add causal chains
        if tool_context.get("causal_chains"):
            chain_lines = []
            for ch in tool_context["causal_chains"]:
                chain_lines.append(
                    f"- {ch.get('relationship_type')}: {ch.get('explanation', '')} "
                    f"(impact: {ch.get('impact_score')}, hops: {ch.get('hops')}, "
                    f"frameworks: {ch.get('frameworks', [])})"
                )
            system_parts.append(f"\n## Causal Chains\n" + "\n".join(chain_lines))

        # Add recent high-priority articles
        if tool_context.get("recent_high_priority_articles"):
            art_lines = []
            for a in tool_context["recent_high_priority_articles"][:3]:
                art_lines.append(
                    f"- [{a.get('priority')}] {a.get('title')} "
                    f"(type: {a.get('type')}, urgency: {a.get('urgency')}, "
                    f"climate: {a.get('climate_events')})"
                )
            system_parts.append(f"\n## Recent High-Priority News\n" + "\n".join(art_lines))

        if memory_context:
            system_parts.append(f"\n## Conversation History\n{memory_context}")

        # Add remaining tool data
        skip_keys = {"company", "facilities", "ontology_profile", "article_context",
                     "causal_chains", "impact_scores", "recent_high_priority_articles"}
        for tool_name, data in tool_context.items():
            if tool_name in skip_keys:
                continue
            if isinstance(data, (dict, list)) and data:
                data_str = json.dumps(data, default=str)
                if len(data_str) > 1500:
                    data_str = data_str[:1500] + "..."
                system_parts.append(f"\n### {tool_name}\n```json\n{data_str}\n```")

        system_prompt = "\n".join(system_parts)

        # Build messages (include recent conversation)
        messages = []
        for msg in recent_messages[-5:]:
            messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            })
        messages.append({"role": "user", "content": question})

        return await llm_client.chat(
            system=system_prompt,
            messages=messages,
            max_tokens=1000,
            model="gpt-4.1",
        )

    except Exception as e:
        logger.error("agent_response_error", error=str(e))
        return f"I encountered an error while analyzing your question: {str(e)}"


# --- Stage 7.3: Agent-to-Agent Handoff Protocol ---

# Domain adjacency: which agents commonly hand off to each other
_HANDOFF_MAP: dict[str, list[str]] = {
    "compliance": ["legal", "content"],
    "legal": ["compliance", "executive"],
    "supply_chain": ["analytics", "opportunity"],
    "analytics": ["executive", "trend"],
    "executive": ["analytics", "stakeholder"],
    "trend": ["analytics", "opportunity"],
    "stakeholder": ["executive", "content"],
    "opportunity": ["analytics", "executive"],
    "content": ["compliance", "legal"],
}

# Keywords that signal a handoff is needed
_HANDOFF_SIGNALS: dict[str, list[str]] = {
    "legal": ["penalty", "regulation", "sebi", "cbam", "lawsuit", "enforcement", "legal risk"],
    "compliance": ["gap analysis", "disclosure", "brsr section", "framework mapping", "audit"],
    "executive": ["board", "cxo", "briefing", "strategic", "investor presentation"],
    "analytics": ["quantify", "data", "metrics", "benchmark", "score"],
    "supply_chain": ["supplier", "supply chain", "scope 3", "tier 1"],
    "trend": ["emerging", "forecast", "next year", "outlook"],
    "stakeholder": ["investor sentiment", "rating", "reputation", "community"],
    "opportunity": ["green revenue", "carbon credit", "roi", "investment"],
    "content": ["draft", "write", "narrative", "disclosure section", "report"],
}


def detect_handoff(
    agent_id: str,
    response: str,
    question: str,
) -> dict | None:
    """Stage 7.3: Detect if the current agent's response suggests a handoff.

    Returns a structured handoff recommendation or None.
    """
    response_lower = response.lower()
    question_lower = question.lower()
    adjacent = _HANDOFF_MAP.get(agent_id, [])

    for target_agent in adjacent:
        signals = _HANDOFF_SIGNALS.get(target_agent, [])
        matching_signals = [s for s in signals if s in response_lower or s in question_lower]

        if len(matching_signals) >= 2:
            target_config = AGENT_ROSTER.get(target_agent, {})
            return {
                "from": agent_id,
                "to": target_agent,
                "to_name": target_config.get("name", target_agent),
                "context": f"Based on discussion about: {', '.join(matching_signals[:3])}",
                "acceptance_criteria": _build_acceptance_criteria(target_agent, matching_signals),
                "signals": matching_signals,
            }

    return None


def _build_acceptance_criteria(target_agent: str, signals: list[str]) -> list[str]:
    """Build acceptance criteria for a handoff based on target agent capabilities."""
    criteria_map = {
        "legal": ["Identify specific regulation/law", "Quantify penalty range", "Provide compliance timeline"],
        "compliance": ["Map to framework section", "Identify disclosure gap", "Suggest remediation steps"],
        "executive": ["Summarize in SCQA format", "Lead with financial impact", "Provide 3 recommendations"],
        "analytics": ["Quantify with specific metrics", "Include trend analysis", "Benchmark against peers"],
        "supply_chain": ["Map supplier risk tiers", "Quantify Scope 3 impact", "Identify geographic exposure"],
        "trend": ["Identify 3+ signals", "Provide counter-signals", "Suggest monitoring cadence"],
        "stakeholder": ["Map stakeholder positions", "Identify perception gaps", "Track rating changes"],
        "opportunity": ["Estimate ROI range", "List risk factors", "Categorize as quick-win or strategic"],
        "content": ["Draft in framework format", "Include data tables", "Match audience tone"],
    }
    return criteria_map.get(target_agent, ["Provide detailed analysis"])[:3]


# --- Stage 7.6: NEXUS-Lite Multi-Agent Pipelines ---

# Pipeline definitions: ordered list of agents for complex queries
NEXUS_PIPELINES: dict[str, dict[str, Any]] = {
    "board_briefing": {
        "description": "Prepare a board briefing on ESG risk",
        "triggers": ["board briefing", "board presentation", "cxo briefing", "prepare a briefing"],
        "agents": ["supply_chain", "analytics", "executive"],
        "final_agent": "executive",
    },
    "compliance_review": {
        "description": "Full compliance review with legal assessment",
        "triggers": ["full compliance review", "compliance audit", "regulatory review", "gap analysis with legal"],
        "agents": ["compliance", "legal", "content"],
        "final_agent": "content",
    },
    "investment_case": {
        "description": "Build ESG investment case",
        "triggers": ["investment case", "green investment", "esg business case", "sustainability roi"],
        "agents": ["opportunity", "analytics", "executive"],
        "final_agent": "executive",
    },
    "risk_assessment": {
        "description": "Comprehensive ESG risk assessment",
        "triggers": ["comprehensive risk", "full risk assessment", "risk report", "enterprise risk"],
        "agents": ["supply_chain", "trend", "analytics"],
        "final_agent": "analytics",
    },
    "competitive_landscape": {
        "description": "Competitive ESG landscape analysis",
        "triggers": ["competitive landscape", "how do we compare", "competitor analysis", "competitive position", "peer comparison", "versus competitors"],
        "agents": ["competitive", "analytics", "executive"],
        "final_agent": "executive",
    },
    "rereact_analysis": {
        "description": "Validated ESG recommendation pipeline",
        "triggers": ["validate recommendations", "deep analysis", "structured recommendations", "REREACT"],
        "agents": ["supply_chain", "analytics", "validator"],
        "final_agent": "validator",
    },
}


def detect_nexus_pipeline(question: str) -> str | None:
    """Stage 7.6: Check if a question triggers a multi-agent NEXUS pipeline."""
    question_lower = question.lower()
    for pipeline_id, config in NEXUS_PIPELINES.items():
        if any(trigger in question_lower for trigger in config["triggers"]):
            return pipeline_id
    return None


async def run_nexus_pipeline(
    tenant_id: str,
    user_id: str,
    question: str,
    pipeline_id: str,
    db=None,
    designation: str | None = None,
) -> dict:
    """Stage 7.6: Execute a multi-agent NEXUS-Lite pipeline.

    Each agent's output feeds the next agent as context.
    The final agent produces the user-facing response.
    """
    from backend.agent.memory import memory_manager

    pipeline = NEXUS_PIPELINES[pipeline_id]
    agent_sequence = pipeline["agents"]
    final_agent = pipeline["final_agent"]

    logger.info(
        "nexus_pipeline_start",
        pipeline=pipeline_id,
        agents=agent_sequence,
        tenant_id=tenant_id,
    )

    # Resolve role profile for personalization in pipeline
    nexus_role_profile = None
    if designation:
        from backend.core.permissions import map_designation_to_role
        from backend.services.role_curation import get_role_profile
        mapped_role = map_designation_to_role(designation)
        nexus_role_profile = get_role_profile(mapped_role)

    accumulated_context: list[dict[str, str]] = []
    all_tools_used: list[str] = []

    for i, agent_id in enumerate(agent_sequence):
        agent_config = AGENT_ROSTER[agent_id]
        personality = load_personality(agent_config["personality_key"])

        # Build the prompt for this agent in the pipeline
        if i == 0:
            agent_question = question
        else:
            # Feed previous agents' output as context
            prior_context = "\n\n".join(
                f"--- {ctx['agent_name']} Analysis ---\n{ctx['response']}"
                for ctx in accumulated_context
            )
            if agent_id == final_agent:
                agent_question = (
                    f"Based on the following specialist analyses, {question}\n\n"
                    f"{prior_context}\n\n"
                    f"Synthesize these into your final deliverable."
                )
            else:
                agent_question = (
                    f"{question}\n\n"
                    f"Previous analysis:\n{prior_context}\n\n"
                    f"Add your specialist perspective to build on this analysis."
                )

        # Gather tools for this agent
        tool_context = await _gather_tool_context(
            tenant_id=tenant_id,
            question=agent_question,
            tools=agent_config["tools"],
            db=db,
        )
        all_tools_used.extend(tool_context.keys())

        # Get memory context
        memory_context = await memory_manager.get_context_summary(tenant_id, user_id)

        # Generate this agent's response (role-personalized for final agent)
        response = await _generate_response(
            personality=personality,
            question=agent_question,
            tool_context=tool_context,
            memory_context=memory_context,
            recent_messages=[],
            tenant_id=tenant_id,
            role_profile=nexus_role_profile if agent_id == final_agent else None,
            designation=designation if agent_id == final_agent else None,
        )

        accumulated_context.append({
            "agent_id": agent_id,
            "agent_name": agent_config["name"],
            "response": response,
        })

        logger.info(
            "nexus_pipeline_step",
            pipeline=pipeline_id,
            step=i + 1,
            agent=agent_id,
            response_len=len(response),
        )

    # Store the full pipeline interaction in memory
    await memory_manager.add_message(
        tenant_id, user_id, "user", question,
        metadata={"agent": "nexus", "pipeline": pipeline_id},
    )

    final_response = accumulated_context[-1]["response"] if accumulated_context else "Pipeline produced no output."

    # Add pipeline provenance to response
    agent_names = [AGENT_ROSTER[a]["name"] for a in agent_sequence]
    provenance = f"\n\n---\n*Pipeline: {' → '.join(agent_names)}*"

    await memory_manager.add_message(
        tenant_id, user_id, "assistant", final_response,
        metadata={
            "agent": final_agent,
            "pipeline": pipeline_id,
            "pipeline_agents": agent_sequence,
        },
    )

    return {
        "response": final_response + provenance,
        "agent": {
            "id": final_agent,
            "name": AGENT_ROSTER[final_agent]["name"],
        },
        "classification": {
            "agent": final_agent,
            "method": "nexus_pipeline",
            "pipeline": pipeline_id,
            "pipeline_agents": agent_sequence,
        },
        "tools_used": list(set(all_tools_used)),
        "pipeline": {
            "id": pipeline_id,
            "agents": [
                {"id": ctx["agent_id"], "name": ctx["agent_name"], "response_preview": ctx["response"][:200]}
                for ctx in accumulated_context
            ],
        },
    }
