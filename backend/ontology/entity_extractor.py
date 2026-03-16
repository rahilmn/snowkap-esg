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
    sentiment: str | None = None  # positive, negative, neutral
    financial_signal: bool = False  # Whether article has financial impact signals
    frameworks_mentioned: list[str] = field(default_factory=list)  # Stage 3.1


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
    """Full extraction + ESG classification for an article."""
    if not llm.is_configured():
        return ExtractionResult(entities=[])

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
  "frameworks_mentioned": ["BRSR", "GRI 305", "TCFD"]
}}

ESG Topics should be specific: emissions, carbon, water, waste, biodiversity, labor_rights,
worker_safety, community, supply_chain_ethics, board_diversity, anti_corruption, data_privacy, etc.

frameworks_mentioned: List ALL ESG/sustainability frameworks referenced (BRSR, GRI, SASB, TCFD,
CDP, ESRS, CSRD, IFRS S1, IFRS S2, etc.) including specific indicator codes if mentioned."""

    try:
        raw_text = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
        )
        raw_text = raw_text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        data = json.loads(raw_text)

        if not isinstance(data, dict):
            logger.warning("extract_classify_invalid_format", type=type(data).__name__)
            return ExtractionResult(entities=[])

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

        return ExtractionResult(
            entities=entities,
            esg_pillar=data.get("esg_pillar"),
            esg_topics=data.get("esg_topics", []),
            sentiment=data.get("sentiment"),
            financial_signal=data.get("financial_signal", False),
            frameworks_mentioned=frameworks,
        )
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.error("extract_classify_parse_failed", error=str(e))
        return ExtractionResult(entities=[])
    except Exception as e:
        logger.error("extract_and_classify_failed", error=str(e))
        return ExtractionResult(entities=[])


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
                ?node rdfs:label ?label .
                OPTIONAL {{ ?node a ?type }}
                OPTIONAL {{ ?node ?edge ?_ }}
                FILTER(CONTAINS(LCASE(STR(?label)), LCASE("{escaped}")))
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
