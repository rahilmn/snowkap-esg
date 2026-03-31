"""NLP Narrative & Tone Extraction Pipeline — Module 1 (v2.0).

5-step extraction before any scoring:
1. Sentiment Classification (5-point scale: -2 to +2)
2. Tone Analysis (controlled vocabulary)
3. Narrative Arc Extraction (core claim, implied causation, stakeholder framing, temporal)
4. Source Credibility Assessment (Tier 1-4)
5. ESG Signal Extraction (entities, quantities, regulatory refs, supply chain refs)
"""

import json
from dataclasses import dataclass, field
import structlog
from backend.core import llm

logger = structlog.get_logger()

# 5-point sentiment scale
SENTIMENT_LABELS = {
    -2: "STRONGLY_NEGATIVE",
    -1: "NEGATIVE",
    0: "NEUTRAL",
    1: "POSITIVE",
    2: "STRONGLY_POSITIVE",
}

# Controlled tone vocabulary
VALID_TONES = [
    "alarmist", "cautionary", "analytical", "neutral", "optimistic",
    "promotional", "adversarial", "conciliatory", "urgent", "speculative",
]

# Source credibility tiers
SOURCE_TIERS = {
    1: "Institutional (regulatory bodies, central banks, peer-reviewed, official filings)",
    2: "Established Media (FT, Bloomberg, Reuters, WSJ, recognized ESG publications)",
    3: "Secondary (trade publications, industry blogs, analyst notes)",
    4: "Unverified (social media, press releases without validation, opinion pieces)",
}

# Known Tier 1 sources
TIER_1_SOURCES = {"sebi", "rbi", "sec", "epa", "world bank", "imf", "un", "ipcc", "iea"}
TIER_2_SOURCES = {
    # Global
    "bloomberg", "reuters", "financial times", "wsj", "wall street journal",
    "bbc", "guardian", "nyt", "new york times", "cnbc",
    # Indian financial media
    "economic times", "business standard", "livemint", "mint", "moneycontrol",
    "ndtv", "business today", "cnbc tv18", "et now", "zeebiz", "zee business",
    "outlook business", "fortune india",
}


@dataclass
class NLPExtraction:
    """Full NLP extraction result per v2.0 Module 1."""
    # Step 1: Sentiment
    sentiment_score: int = 0  # -2 to +2
    sentiment_label: str = "NEUTRAL"

    # Step 2: Tone
    primary_tone: str = "neutral"
    secondary_tone: str | None = None

    # Step 3: Narrative Arc
    core_claim: str = ""
    supporting_evidence: list[str] = field(default_factory=list)
    implied_causation: str = ""
    stakeholder_framing: dict = field(default_factory=dict)  # {"protagonist": "", "antagonist": "", "affected": ""}
    temporal_framing: str = "present"  # backward / present / forward

    # Step 4: Source Credibility
    source_tier: int = 3
    source_rationale: str = ""

    # Step 5: ESG Signals
    named_entities: list[dict] = field(default_factory=list)  # [{"text": "", "type": "company|regulator|geography|framework"}]
    quantitative_claims: list[str] = field(default_factory=list)
    regulatory_references: list[str] = field(default_factory=list)
    supply_chain_references: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sentiment": {"score": self.sentiment_score, "label": self.sentiment_label},
            "tone": {"primary": self.primary_tone, "secondary": self.secondary_tone},
            "narrative_arc": {
                "core_claim": self.core_claim,
                "supporting_evidence": self.supporting_evidence,
                "implied_causation": self.implied_causation,
                "stakeholder_framing": self.stakeholder_framing,
                "temporal_framing": self.temporal_framing,
            },
            "source_credibility": {"tier": self.source_tier, "rationale": self.source_rationale},
            "esg_signals": {
                "named_entities": self.named_entities,
                "quantitative_claims": self.quantitative_claims,
                "regulatory_references": self.regulatory_references,
                "supply_chain_references": self.supply_chain_references,
            },
        }


def _source_matches(source_lower: str, terms: set[str]) -> bool:
    """Word-boundary-aware source matching to avoid greedy substring hits.

    Checks if any term appears as a whole word (or at start/end) in source_lower,
    rather than as an arbitrary substring (e.g. 'sebi' should NOT match 'SoccerBible').
    """
    words = source_lower.split()
    for term in terms:
        # Multi-word terms (e.g. "financial times") — check as contiguous phrase
        if " " in term:
            if term in source_lower:
                return True
            continue
        # Single-word terms — must appear as a whole word
        if term in words:
            return True
        # Also match if source starts/ends with the term (handles no-space cases like "sebi.gov")
        if source_lower.startswith(term) or source_lower.endswith(term):
            return True
        # Padded boundary check for terms embedded between punctuation
        if f" {term} " in f" {source_lower} ":
            return True
    return False


def assess_source_credibility(source: str | None) -> tuple[int, str]:
    """Rule-based source credibility Tier 1-4 classification."""
    if not source:
        return 4, "No source attribution"
    source_lower = source.lower().strip()
    # strip common suffixes
    for suffix in [".com", ".in", ".org", ".gov", ".co.uk", " - ", " | "]:
        source_lower = source_lower.split(suffix)[0].strip()

    if _source_matches(source_lower, TIER_1_SOURCES):
        return 1, f"Institutional source: {source}"
    if _source_matches(source_lower, TIER_2_SOURCES):
        return 2, f"Established media: {source}"
    # Check for .gov domains
    if ".gov" in (source or "").lower():
        return 1, f"Government source: {source}"
    # trade/industry publications
    trade_signals = ["trade", "industry", "journal", "analyst", "research", "report"]
    if any(s in source_lower for s in trade_signals):
        return 3, f"Trade/industry publication: {source}"
    return 3, f"Secondary source: {source}"


def _is_non_english(text: str) -> bool:
    """Quick heuristic: if >30% of characters are non-ASCII letters, likely non-English."""
    if not text:
        return False
    sample = text[:500]
    non_ascii = sum(1 for c in sample if ord(c) > 127 and c.isalpha())
    alpha = sum(1 for c in sample if c.isalpha()) or 1
    return (non_ascii / alpha) > 0.3


async def _translate_if_needed(title: str, content: str) -> tuple[str, str]:
    """Detect non-English content and translate to English via LLM.

    Returns (translated_content, translated_title). If already English, returns unchanged.
    """
    if not _is_non_english(title) and not _is_non_english(content):
        return content, title

    logger.info("non_english_detected", title=title[:50])
    try:
        raw = await llm.chat(
            system=(
                "You are a professional translator. Translate the given text to English. "
                "Preserve all numbers, company names, dates, and technical terms exactly. "
                "Return ONLY valid JSON — no markdown, no explanation."
            ),
            messages=[{"role": "user", "content": f"""Translate this article to English:

TITLE: {title}

CONTENT: {content[:3000]}

Return JSON:
{{"translated_title": "<English title>", "translated_content": "<English translation of the full content>", "original_language": "<detected language>"}}"""}],
            max_tokens=2000,
            model="gpt-4o",
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        if not raw.startswith("{"):
            raw = raw[raw.find("{"):]
        if not raw.endswith("}"):
            raw = raw[:raw.rfind("}") + 1]

        data = json.loads(raw)
        translated_title = data.get("translated_title", title)
        translated_content = data.get("translated_content", content)
        lang = data.get("original_language", "unknown")
        logger.info("article_translated", from_lang=lang, title=translated_title[:60])
        return translated_content, translated_title
    except Exception as e:
        logger.warning("translation_failed", error=str(e))
        return content, title


async def run_nlp_pipeline(
    article_title: str,
    article_content: str | None,
    article_source: str | None = None,
) -> NLPExtraction:
    """Run the full 5-step NLP extraction pipeline on an article.

    This MUST run before any scoring, relevance assessment, or analysis.
    Returns structured NLPExtraction with all 5 steps completed.
    """
    result = NLPExtraction()

    # Step 4 is rule-based, do it first (no LLM needed)
    result.source_tier, result.source_rationale = assess_source_credibility(article_source)

    # Steps 1-3, 5 via single LLM call for efficiency
    if not llm.is_configured():
        result.core_claim = article_title
        return result

    text = article_content[:3000] if article_content else article_title

    # Step 0: Language detection and translation for non-English content
    text, article_title = await _translate_if_needed(article_title, text)

    # Sanitize title for JSON prompt — replace currency symbols that cause encoding issues
    safe_title = article_title.replace("₹", "Rs.").replace("€", "EUR ").replace("£", "GBP ")

    try:
        raw = await llm.chat(
            system=(
                "You are an NLP extraction engine for ESG news analysis. "
                "Extract structured narrative, tone, and signal data from articles. "
                "Return ONLY valid JSON — no markdown, no explanation."
            ),
            messages=[{"role": "user", "content": f"""Analyze this article and extract:

TITLE: "{safe_title}"
CONTENT: {text}

Return JSON:
{{
  "sentiment_score": <int -2 to +2, where -2=crisis/scandal/catastrophic, -1=risk/concern/criticism, 0=factual/routine, +1=progress/favorable, +2=breakthrough/transformative>,
  "primary_tone": "<one of: alarmist, cautionary, analytical, neutral, optimistic, promotional, adversarial, conciliatory, urgent, speculative>",
  "secondary_tone": "<one of the above or null>",
  "core_claim": "<the primary assertion or event in 1 sentence>",
  "supporting_evidence": ["<key data point 1>", "<key data point 2>"],
  "implied_causation": "<the causal chain the article constructs, e.g. 'Policy X → Market shift Y → Company impact Z'>",
  "stakeholder_framing": {{"protagonist": "<who is positioned positively>", "antagonist": "<who is positioned negatively>", "affected": "<who is impacted>"}},
  "temporal_framing": "<backward (post-mortem) | present (breaking) | forward (predictive)>",
  "named_entities": [{{"text": "<name>", "type": "<company|regulator|geography|framework|commodity>"}}],
  "quantitative_claims": ["<any numbers, percentages, amounts mentioned>"],
  "regulatory_references": ["<specific laws, directives, standards mentioned>"],
  "supply_chain_references": ["<supplier names, tiers, supply chain geographies>"]
}}"""}],
            max_tokens=800,
            model="gpt-4o",
        )
        raw = raw.strip()
        # Strip markdown code fences
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        # BUG-17: Robust JSON extraction — try direct parse first,
        # then use balanced-brace extraction as fallback
        data = None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Find outermost balanced braces using a stack-based approach
            start = raw.find("{")
            if start >= 0:
                depth = 0
                end = -1
                in_string = False
                escape_next = False
                for i in range(start, len(raw)):
                    c = raw[i]
                    if escape_next:
                        escape_next = False
                        continue
                    if c == "\\":
                        escape_next = True
                        continue
                    if c == '"' and not escape_next:
                        in_string = not in_string
                        continue
                    if in_string:
                        continue
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            end = i
                            break
                if end > start:
                    raw = raw[start:end + 1]
                    data = json.loads(raw)

        if data is None:
            raise ValueError("Could not extract valid JSON from LLM response")

        # Step 1: Sentiment
        score = data.get("sentiment_score", 0)
        score = max(-2, min(2, int(score)))
        result.sentiment_score = score
        result.sentiment_label = SENTIMENT_LABELS.get(score, "NEUTRAL")

        # Step 2: Tone
        primary = (data.get("primary_tone") or "neutral").lower()
        result.primary_tone = primary if primary in VALID_TONES else "neutral"
        secondary = (data.get("secondary_tone") or "").lower()
        result.secondary_tone = secondary if secondary in VALID_TONES else None

        # Step 3: Narrative Arc
        result.core_claim = data.get("core_claim", article_title)
        result.supporting_evidence = data.get("supporting_evidence", [])[:3]
        result.implied_causation = data.get("implied_causation", "")
        result.stakeholder_framing = data.get("stakeholder_framing", {})
        tf = (data.get("temporal_framing") or "present").lower()
        result.temporal_framing = tf if tf in ("backward", "present", "forward") else "present"

        # Step 5: ESG Signals
        result.named_entities = data.get("named_entities", [])[:20]
        result.quantitative_claims = data.get("quantitative_claims", [])[:10]
        result.regulatory_references = data.get("regulatory_references", [])[:10]
        result.supply_chain_references = data.get("supply_chain_references", [])[:10]

        logger.info(
            "nlp_pipeline_complete",
            sentiment=result.sentiment_label,
            tone=result.primary_tone,
            temporal=result.temporal_framing,
            entities=len(result.named_entities),
        )
    except Exception as e:
        logger.error("nlp_pipeline_failed", error=str(e))
        result.core_claim = article_title

    return result
