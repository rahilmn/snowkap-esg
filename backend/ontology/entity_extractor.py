"""Entity extraction from news articles via Claude NER + resolution against Jena.

Per MASTER_BUILD_PLAN Part 1, Layer 2: Entity Extraction & Linking
- NER from news: companies, locations, commodities, regulations, events
- Entity resolution against Jena knowledge graph (fuzzy match + semantic similarity)
"""

import json
from dataclasses import dataclass

import structlog
from anthropic import AsyncAnthropic

from backend.core.config import settings
from backend.ontology.jena_client import jena_client

logger = structlog.get_logger()

SNOWKAP_NS = "http://snowkap.com/ontology/esg#"


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
    sentiment: str | None = None  # positive, negative, neutral
    financial_signal: bool = False  # Whether article has financial impact signals


async def extract_entities(article_title: str, article_content: str) -> list[ExtractedEntity]:
    """Extract ESG-relevant entities from a news article using Claude NER."""
    if not settings.ANTHROPIC_API_KEY:
        logger.warning("anthropic_key_missing", action="entity_extraction")
        return []

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

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
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        entities_raw = json.loads(response.content[0].text)
        entities = [
            ExtractedEntity(
                text=e["text"],
                entity_type=e["type"],
                confidence=e.get("confidence", 0.8),
                esg_relevance=e.get("esg_relevance"),
            )
            for e in entities_raw
        ]
        logger.info("entities_extracted", count=len(entities), title_preview=article_title[:60])
        return entities
    except Exception as e:
        logger.error("entity_extraction_failed", error=str(e))
        return []


async def extract_and_classify(
    article_title: str, article_content: str,
) -> ExtractionResult:
    """Full extraction + ESG classification for an article."""
    if not settings.ANTHROPIC_API_KEY:
        return ExtractionResult(entities=[])

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

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
  "financial_signal": true|false,
  "frameworks_mentioned": ["BRSR", "GRI"]
}}

ESG Topics should be specific: emissions, carbon, water, waste, biodiversity, labor_rights,
worker_safety, community, supply_chain_ethics, board_diversity, anti_corruption, data_privacy, etc."""

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        data = json.loads(response.content[0].text)

        entities = [
            ExtractedEntity(
                text=e["text"],
                entity_type=e["type"],
                confidence=e.get("confidence", 0.8),
                esg_relevance=e.get("esg_relevance"),
            )
            for e in data.get("entities", [])
        ]

        return ExtractionResult(
            entities=entities,
            esg_pillar=data.get("esg_pillar"),
            esg_topics=data.get("esg_topics", []),
            sentiment=data.get("sentiment"),
            financial_signal=data.get("financial_signal", False),
        )
    except Exception as e:
        logger.error("extract_and_classify_failed", error=str(e))
        return ExtractionResult(entities=[])


async def resolve_entities_against_graph(
    entities: list[ExtractedEntity],
    tenant_id: str,
) -> list[ExtractedEntity]:
    """Resolve extracted entities against the tenant's Jena knowledge graph.

    Per MASTER_BUILD_PLAN: Entity resolution against Jena (fuzzy match + semantic similarity)
    """
    graph_uri = jena_client._tenant_graph(tenant_id)
    resolved = []

    for entity in entities:
        # Build SPARQL to find matching nodes by label
        escaped = entity.text.replace("\\", "\\\\").replace('"', '\\"')
        sparql = f"""
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        PREFIX snowkap: <{SNOWKAP_NS}>
        SELECT ?node ?label ?type WHERE {{
            GRAPH <{graph_uri}> {{
                ?node rdfs:label ?label .
                OPTIONAL {{ ?node a ?type }}
                FILTER(CONTAINS(LCASE(STR(?label)), LCASE("{escaped}")))
            }}
        }}
        LIMIT 3
        """
        try:
            result = await jena_client.query(sparql)
            bindings = result.get("results", {}).get("bindings", [])

            if bindings:
                # Take the best match
                best = bindings[0]
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
