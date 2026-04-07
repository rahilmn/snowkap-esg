"""Entity extraction from news articles via LLM NER + resolution against Jena.

Per MASTER_BUILD_PLAN Part 1, Layer 2: Entity Extraction & Linking
- NER from news: companies, locations, commodities, regulations, events
- Entity resolution against Jena knowledge graph (fuzzy match + semantic similarity)

Stage 2.2: Rank matches by exact > substring > fuzzy + edge count.
Stage 3.1: frameworks_mentioned field on ExtractionResult.
"""

import json
from dataclasses import dataclass, field

import structlog

from backend.core import llm
from backend.core.config import settings
from backend.ontology.jena_client import jena_client

logger = structlog.get_logger()

SNOWKAP_NS = "http://snowkap.com/ontology/esg#"

# Framework alias normalization (Stage 3.1)
FRAMEWORK_ALIASES: dict[str, str] = {
    "task force on climate": "TCFD",
    "task force on climate-related financial disclosures": "TCFD",
    "global reporting initiative": "GRI",
    "sustainability accounting standards board": "SASB",
    "business responsibility and sustainability report": "BRSR",
    "business responsibility and sustainability reporting": "BRSR",
    "carbon disclosure project": "CDP",
    "european sustainability reporting standards": "ESRS",
    "corporate sustainability reporting directive": "CSRD",
    "international financial reporting standards": "IFRS",
    "ifrs s1": "IFRS_S1",
    "ifrs s2": "IFRS_S2",
    "international sustainability standards board": "ISSB",
    "science based targets initiative": "SBTi",
    "science-based targets": "SBTi",
    "sustainable finance disclosure regulation": "SFDR",
    "eu taxonomy": "EU_TAXONOMY",
}


@dataclass
class ExtractedEntity:
    """An entity extracted from a news article."""
    text: str
    entity_type: str  # company, location, commodity, regulation, event, person, industry
    confidence: float = 0.8
    resolved_uri: str | None = None  # URI in Jena graph after resolution
    esg_relevance: str | None = None  # E, S, G, or None


@dataclass
class ExtractionResult:
    """Full result of entity extraction + ESG classification for an article."""
    entities: list[ExtractedEntity]
    esg_pillar: str | None = None  # Primary: E, S, or G
    esg_topics: list[str] | None = None  # e.g., ["emissions", "water_scarcity"]
    sentiment: str | None = None  # positive, negative, neutral (legacy label)
    financial_signal: bool = False  # Whether article has financial impact signals
    frameworks_mentioned: list[str] = field(default_factory=list)  # Stage 3.1

    # Phase 1C: Multi-dimensional sentiment
    sentiment_score: float | None = None          # -1.0 (very negative) to +1.0 (very positive)
    sentiment_confidence: float | None = None     # 0.0 to 1.0
    aspect_sentiments: dict | None = None         # {"E": -0.8, "S": 0.2, "G": -0.3}

    # Phase 1C: Criticality assessment
    urgency: str | None = None                    # critical / high / medium / low
    time_horizon: str | None = None               # immediate / days / weeks / months
    reversibility: str | None = None              # irreversible / difficult / moderate / easy
    stakeholder_impact: list[str] | None = None   # ["investors", "regulators", "community"]

    # Phase 1C: Structured financial signal
    financial_signal_detail: dict | None = None   # {"type":"penalty","amount":50000000,"currency":"INR","confidence":0.85}

    # Phase 1C: Content classification
    content_type: str | None = None               # regulatory/financial/operational/reputational/technical/narrative/data_release

    # Phase 4: Climate events
    climate_events: list[str] | None = None        # ["water_scarcity", "drought", "heatwave"]

    # Advanced Intelligence: 5D relevance score
    relevance_data: dict | None = None  # {esg_correlation: 0-2, financial_impact: 0-2, ...}


def normalize_framework(name: str) -> str:
    """Normalize framework aliases to canonical names."""
    name_lower = name.strip().lower()
    for alias, canonical in FRAMEWORK_ALIASES.items():
        if alias in name_lower:
            return canonical
    return name.strip().upper().replace(" ", "_") if name else name


async def extract_entities(article_title: str, article_content: str) -> list[ExtractedEntity]:
    """Extract ESG-relevant entities from a news article using LLM NER."""
    if not llm.is_configured():
        logger.warning("llm_not_configured", action="entity_extraction")
        return []

    prompt = f"""Extract all ESG-relevant entities from this news article. Return ONLY a JSON array.

Title: {article_title}
Content: {article_content[:3000]}

For each entity, provide:
- "text": the entity name as it appears
- "type": one of ["company", "location", "commodity", "regulation", "event", "person", "industry", "framework"]
- "confidence": 0.0-1.0
- "esg_relevance": one of ["E", "S", "G", null] — Environmental, Social, or Governance

Focus on:
- Companies and organizations mentioned
- Geographic locations (cities, districts, states, countries)
- Commodities (oil, coal, steel, water, LPG, etc.)
- Regulations and policies (BRSR, EU CBAM, SEBI ESG, etc.)
- ESG events (spills, strikes, compliance failures, green initiatives)
- Industries mentioned

Return JSON array only, no markdown:
[{{"text": "...", "type": "...", "confidence": 0.9, "esg_relevance": "E"}}]"""

    try:
        raw_text = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
        )
        raw_text = raw_text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        entities_raw = json.loads(raw_text)

        if not isinstance(entities_raw, list):
            logger.warning("entity_extraction_invalid_format", type=type(entities_raw).__name__)
            return []

        entities = []
        for e in entities_raw:
            if not isinstance(e, dict) or "text" not in e or "type" not in e:
                continue
            entities.append(ExtractedEntity(
                text=str(e["text"]),
                entity_type=str(e["type"]),
                confidence=float(e.get("confidence", 0.8)),
                esg_relevance=e.get("esg_relevance"),
            ))

        logger.info("entities_extracted", count=len(entities), title_preview=article_title[:60])
        return entities
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.error("entity_extraction_parse_failed", error=str(e))
        return []
    except Exception as e:
        logger.error("entity_extraction_failed", error=str(e))
        return []


async def extract_and_classify(
    article_title: str, article_content: str,
) -> ExtractionResult:
    """Full extraction + ESG classification + sentiment + criticality for an article.

    Phase 1C: Enhanced to extract multi-dimensional sentiment, criticality assessment,
    financial signals, and content type — all in a single LLM call.
    """
    if not llm.is_configured():
        return ExtractionResult(entities=[])

    # Log content quality — if only headline/summary, insights will be shallow
    content_len = len(article_content) if article_content else 0
    if content_len < 100:
        logger.warning(
            "shallow_content_for_extraction",
            title=article_title[:50],
            content_len=content_len,
            note="LLM will see limited text — insights may be shallow",
        )

    prompt = f"""Analyze this news article for ESG intelligence. Return a JSON object.

Title: {article_title}
Content: {article_content[:3000]}

Return exactly this structure (no markdown, pure JSON):
{{
  "entities": [
    {{"text": "...", "type": "company|location|commodity|regulation|event|person|industry|framework", "confidence": 0.9, "esg_relevance": "E|S|G|null"}}
  ],
  "esg_pillar": "E|S|G|null",
  "esg_topics": ["emissions", "water_scarcity", "labor_rights"],
  "sentiment": "positive|negative|neutral",
  "frameworks_mentioned": ["BRSR", "GRI 305", "TCFD"],

  "sentiment_score": -0.75,
  "sentiment_confidence": 0.88,
  "aspect_sentiments": {{"E": -0.8, "S": 0.1, "G": -0.3}},

  "urgency": "high",
  "time_horizon": "weeks",
  "reversibility": "difficult",
  "stakeholder_impact": ["investors", "regulators"],

  "financial_signal": {{
    "detected": true,
    "type": "penalty",
    "amount": 50000000,
    "currency": "INR",
    "confidence": 0.85
  }},

  "content_type": "regulatory",

  "climate_events": ["water_scarcity", "heatwave"],

  "relevance": {{
    "esg_correlation": 2,
    "financial_impact": 1,
    "compliance_risk": 2,
    "supply_chain_impact": 1,
    "people_impact": 1
  }}
}}

FIELD DEFINITIONS:

sentiment_score: Continuous scale from -1.0 (extremely negative/damaging) to +1.0 (extremely positive/beneficial).
  Examples: Major oil spill = -0.95, SEBI penalty = -0.8, New CSR initiative = +0.6, Routine disclosure = 0.0
sentiment_confidence: How certain about the sentiment (0.0 to 1.0).
aspect_sentiments: Per-pillar sentiment scores for E (environmental), S (social), G (governance). Omit pillars not relevant.

urgency: "critical" (action within 24h), "high" (within 1 week), "medium" (within 1 month), "low" (informational)
time_horizon: "immediate" (now/24h), "days" (1-7 days), "weeks" (1-4 weeks), "months" (1-12 months)
reversibility: "irreversible" (disaster, death), "difficult" (penalties, recalls), "moderate" (reversible with effort), "easy" (routine)
stakeholder_impact: List affected stakeholders: "investors", "regulators", "community", "employees", "customers", "board", "suppliers"

financial_signal: If monetary amounts, penalties, fines, investments, losses, revenue impacts, or EXPENSE signals are mentioned:
  - type: "penalty", "fine", "investment", "loss", "revenue_impact", "cost", "exposure",
          "capex", "tax", "insurance", "remediation"
    Use "penalty"/"fine" for regulatory penalties and fines.
    Use "capex" for capital expenditure requirements (e.g., pollution control equipment, EV fleet transition).
    Use "tax" for carbon tax, green cess, or environmental levies.
    Use "insurance" for insurance premium increases due to ESG/climate risk.
    Use "remediation" for cleanup costs, environmental restoration, contamination remediation.
    Use "cost" for general compliance costs, operational cost increases, or other expense signals.
  - amount: Numeric value (e.g., 50000000 for ₹5 Crore). Convert Crore/Lakh to raw numbers.
  - currency: "INR", "USD", "EUR"
  - confidence: How certain the amount is accurate (0.0-1.0)
  If no financial signal: {{"detected": false}}

content_type: Classify the article as one of:
  - "regulatory": Laws, regulations, compliance, SEBI notices, framework updates
  - "financial": M&A, earnings, investments, market movements
  - "operational": Supply chain, manufacturing, process changes
  - "reputational": Awards, controversies, public perception
  - "technical": Standards, methodologies, scientific findings
  - "narrative": Stories, case studies, educational content
  - "data_release": Reports, benchmarks, rankings, emissions data, ratings

ESG Topics: emissions, carbon, water, waste, biodiversity, labor_rights, worker_safety,
community, supply_chain_ethics, board_diversity, anti_corruption, data_privacy, etc.

frameworks_mentioned: List ALL ESG frameworks referenced (BRSR, GRI, SASB, TCFD,
CDP, ESRS, CSRD, IFRS S1, IFRS S2, etc.) with indicator codes if mentioned.

relevance: Score ESG relevance across 5 dimensions (0-2 each, total 0-10):
  - esg_correlation: 2=direct ESG theme, 1=indirect link, 0=no ESG connection
  - financial_impact: 2=quantified revenue/expense/valuation impact (includes compliance costs, fines, capex, remediation, carbon tax, insurance premium changes), 1=implied effect, 0=none
  - compliance_risk: 2=regulatory/disclosure obligation triggered, 1=potential, 0=none
  - supply_chain_impact: 2=direct Tier 1/2/3 effect, 1=indirect ripple, 0=none
    FOR FINANCIAL INSTITUTIONS (banks, NBFCs, AMCs, insurance): "supply chain" includes
    lending portfolio exposure, counterparty ESG risk, financed emissions (Scope 3 Category 15),
    and borrower ESG compliance. Score 1 if the article implies indirect portfolio impact
    (e.g., a regulation affecting borrowers), 2 if it directly affects lending decisions or
    portfolio valuation (e.g., a green bond funding specific projects).
  - people_impact: 2=direct workforce/community/consumer effect, 1=indirect, 0=none

climate_events: If the article discusses weather, climate, or environmental events, list them:
  - "drought", "water_scarcity", "monsoon_failure", "heatwave", "heat_stress"
  - "flood", "cyclone", "typhoon", "tsunami", "coastal_flood", "sea_level_rise"
  - "wildfire", "air_pollution", "smog"
  - "landslide", "earthquake", "soil_erosion"
  - "deforestation", "biodiversity_loss"
  If no climate events: empty list []"""

    try:
        raw_text = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2500,
            model="gpt-4.1-nano",  # Cheapest tier, sufficient for NER extraction
        )
        raw_text = raw_text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        data = json.loads(raw_text)

        if not isinstance(data, dict):
            logger.warning("extract_classify_invalid_format", type=type(data).__name__)
            return ExtractionResult(entities=[])

        # Parse entities
        entities = []
        for e in data.get("entities", []):
            if not isinstance(e, dict) or "text" not in e or "type" not in e:
                continue
            entities.append(ExtractedEntity(
                text=str(e["text"]),
                entity_type=str(e["type"]),
                confidence=float(e.get("confidence", 0.8)),
                esg_relevance=e.get("esg_relevance"),
            ))

        # Normalize framework mentions (Stage 3.1)
        raw_frameworks = data.get("frameworks_mentioned", [])
        if not isinstance(raw_frameworks, list):
            raw_frameworks = []
        frameworks = [normalize_framework(f) for f in raw_frameworks if isinstance(f, str) and f.strip()]

        # Parse financial signal — handle both old boolean and new object format
        raw_financial = data.get("financial_signal", False)
        financial_bool = False
        financial_detail = None
        if isinstance(raw_financial, dict):
            financial_bool = raw_financial.get("detected", False)
            if financial_bool:
                financial_detail = {
                    "type": raw_financial.get("type"),
                    "amount": raw_financial.get("amount"),
                    "currency": raw_financial.get("currency", "INR"),
                    "confidence": raw_financial.get("confidence", 0.5),
                }
        elif isinstance(raw_financial, bool):
            financial_bool = raw_financial

        # Parse aspect sentiments — validate structure
        raw_aspects = data.get("aspect_sentiments")
        aspect_sentiments = None
        if isinstance(raw_aspects, dict):
            aspect_sentiments = {}
            for k, v in raw_aspects.items():
                if k in ("E", "S", "G") and isinstance(v, (int, float)):
                    aspect_sentiments[k] = max(-1.0, min(1.0, float(v)))
            if not aspect_sentiments:
                aspect_sentiments = None

        # Parse sentiment score — clamp to range
        raw_sent_score = data.get("sentiment_score")
        sentiment_score = None
        if isinstance(raw_sent_score, (int, float)):
            sentiment_score = max(-1.0, min(1.0, float(raw_sent_score)))

        # Parse stakeholder impact — must be list of strings
        raw_stakeholders = data.get("stakeholder_impact", [])
        stakeholder_impact = None
        if isinstance(raw_stakeholders, list):
            stakeholder_impact = [str(s) for s in raw_stakeholders if isinstance(s, str)]
            if not stakeholder_impact:
                stakeholder_impact = None

        return ExtractionResult(
            entities=entities,
            esg_pillar=data.get("esg_pillar"),
            esg_topics=data.get("esg_topics", []),
            sentiment=data.get("sentiment") if isinstance(data.get("sentiment"), str) else None,
            financial_signal=financial_bool,
            frameworks_mentioned=frameworks,
            # Phase 1C: New fields
            sentiment_score=sentiment_score,
            sentiment_confidence=_safe_float(data.get("sentiment_confidence"), 0.0, 1.0),
            aspect_sentiments=aspect_sentiments,
            urgency=_safe_enum(data.get("urgency"), {"critical", "high", "medium", "low"}),
            time_horizon=_safe_enum(data.get("time_horizon"), {"immediate", "days", "weeks", "months"}),
            reversibility=_safe_enum(data.get("reversibility"), {"irreversible", "difficult", "moderate", "easy"}),
            stakeholder_impact=stakeholder_impact,
            financial_signal_detail=financial_detail,
            content_type=_safe_enum(
                data.get("content_type"),
                {"regulatory", "financial", "operational", "reputational", "technical", "narrative", "data_release"},
            ),
            # Phase 4: Climate events
            climate_events=[
                str(e).lower().strip() for e in data.get("climate_events", [])
                if isinstance(e, str) and e.strip()
            ] or None,
            # Advanced Intelligence: 5D relevance
            relevance_data=data.get("relevance") if isinstance(data.get("relevance"), dict) else None,
        )
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.error("extract_classify_parse_failed", error=str(e))
        return ExtractionResult(entities=[])
    except Exception as e:
        logger.error("extract_and_classify_failed", error=str(e))
        return ExtractionResult(entities=[])


def _safe_float(value, min_val: float, max_val: float) -> float | None:
    """Parse a float from LLM output, clamped to range."""
    if value is None:
        return None
    try:
        f = float(value)
        return max(min_val, min(max_val, f))
    except (TypeError, ValueError):
        return None


def _safe_enum(value, valid: set[str]) -> str | None:
    """Parse a string enum from LLM output, validate against allowed values."""
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    return v if v in valid else None


async def resolve_entities_against_graph(
    entities: list[ExtractedEntity],
    tenant_id: str,
) -> list[ExtractedEntity]:
    """Resolve extracted entities against the tenant's Jena knowledge graph.

    Stage 2.2: Rank matches by exact > substring > fuzzy + edge count in graph.
    """
    graph_uri = jena_client._tenant_graph(tenant_id)
    resolved = []

    for entity in entities:
        escaped = entity.text.replace("\\", "\\\\").replace('"', '\\"')
        sparql = f"""
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        PREFIX snowkap: <{SNOWKAP_NS}>
        SELECT ?node ?label ?type (COUNT(?edge) AS ?edge_count) WHERE {{
            GRAPH <{graph_uri}> {{
                {{
                    ?node rdfs:label ?label .
                    FILTER(CONTAINS(LCASE(STR(?label)), LCASE("{escaped}")))
                }}
                UNION
                {{
                    ?alias rdfs:label ?label .
                    FILTER(CONTAINS(LCASE(STR(?label)), LCASE("{escaped}")))
                    ?alias snowkap:sameCompany ?node .
                }}
                OPTIONAL {{ ?node a ?type }}
                OPTIONAL {{ ?node ?edge ?_ }}
            }}
        }}
        GROUP BY ?node ?label ?type
        ORDER BY DESC(?edge_count)
        LIMIT 5
        """
        try:
            result = await jena_client.query(sparql)
            bindings = result.get("results", {}).get("bindings", [])

            if bindings:
                best = _rank_matches(bindings, entity.text)
                entity.resolved_uri = best["node"]["value"]
                logger.debug(
                    "entity_resolved",
                    text=entity.text,
                    uri=entity.resolved_uri,
                )
        except Exception as e:
            logger.debug("entity_resolution_failed", text=entity.text, error=str(e))

        resolved.append(entity)

    resolved_count = sum(1 for e in resolved if e.resolved_uri)
    logger.info(
        "entities_resolved",
        total=len(entities),
        resolved=resolved_count,
        tenant_id=tenant_id,
    )
    return resolved


def _rank_matches(bindings: list[dict], query_text: str) -> dict:
    """Rank entity matches: exact label > substring > fuzzy + edge count."""
    query_lower = query_text.lower().strip()
    exact = []
    substring = []
    fuzzy = []

    for b in bindings:
        label = b.get("label", {}).get("value", "").lower().strip()
        edge_count = int(b.get("edge_count", {}).get("value", "0"))

        if label == query_lower:
            exact.append((b, edge_count))
        elif query_lower in label or label in query_lower:
            substring.append((b, edge_count))
        else:
            fuzzy.append((b, edge_count))

    for tier in [exact, substring, fuzzy]:
        if tier:
            tier.sort(key=lambda x: x[1], reverse=True)
            return tier[0][0]

    return bindings[0]
