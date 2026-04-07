"""Event Classifier — Heuristic pre-classifier for LLM impact score guard rails.

Track C2: Before the LLM generates an impact_score, this classifier detects
the event type from article text using keyword rules and returns score bounds.

This prevents LLM score drift:
- Routine capex scoring 7-8 when it should be 3-5
- Criminal fraud scoring 3-4 when it should be 8+
"""

import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()


@dataclass
class EventClassification:
    event_type: str          # Human-readable event category
    event_code: str          # Machine code e.g. "ROUTINE_CAPEX"
    score_ceiling: int | None   # Max score the LLM should assign (None = no cap)
    score_floor: int | None     # Min score the LLM should assign (None = no floor)
    calibration_hint: str    # One-line hint to include in the LLM prompt


# ---------------------------------------------------------------------------
# Event rules — ordered by specificity (more specific first)
# Each rule: (event_code, label, ceiling, floor, keywords_any, hint)
# keywords_any: article must contain AT LEAST ONE of these (case-insensitive)
# ---------------------------------------------------------------------------

_RULES: list[tuple[str, str, int | None, int | None, list[str], str]] = [

    # --- High-floor events (existential / systemic) ---
    (
        "CRIMINAL_INDICTMENT",
        "Criminal indictment / prosecution",
        None, 8,
        ["criminal", "indicted", "arrested", "chargesheet", "cbi", "enforcement directorate",
         "ed raid", "fraud case", "money laundering", "insider trading", "scam"],
        "Criminal or enforcement action — existential to leadership/brand, floor 8.",
    ),
    (
        "LICENSE_REVOCATION",
        "License revocation / ban",
        None, 8,
        ["license revoked", "licence revoked", "banned", "deregistered", "cancelled licence",
         "sebi ban", "rbi cancels", "permit cancelled", "blacklisted"],
        "License or permit revoked — operational halt risk, floor 8.",
    ),
    (
        "SYSTEMIC_REGULATORY",
        "Systemic regulatory change (sector-wide)",
        None, 8,
        ["all listed", "entire sector", "all companies", "mandatory for", "rbi circular",
         "sebi circular", "gazette notification", "regulatory framework", "new regulation",
         "mandatory disclosure", "brsr mandatory", "climate disclosure framework"],
        "Sector-wide regulatory mandate — systemic impact, floor 8.",
    ),
    (
        "MAJOR_MA",
        "Major M&A (>₹1000 Cr)",
        None, 7,
        ["merger", "acquisition", "takeover", "buyout", "stake acquisition",
         "strategic acquisition", "demerger", "amalgamation"],
        "Material M&A event — board-level impact, floor 7.",
    ),
    (
        "MAJOR_FINE",
        "Major regulatory fine (>₹50 Cr)",
        None, 7,
        ["heavy penalty", "massive fine", "record penalty",
         "fined rs 100", "fined rs 200", "fined rs 500", "fined rs 1000",
         "penalty of rs 100", "penalty of rs 200", "penalty of rs 500",
         "fine of usd 10 million", "fine of usd 50 million",
         "imposed penalty of", "levied fine of"],
        "Large regulatory penalty — material financial impact, floor 7.",
    ),
    (
        "CREDIT_RATING_DOWNGRADE",
        "Credit rating downgrade",
        None, 7,
        ["rating downgrade", "downgraded to", "outlook negative", "outlook revised downward",
         "credit watch negative", "rating cut", "moody's downgrade", "s&p downgrade",
         "crisil downgrade", "icra downgrade", "care downgrade"],
        "Rating action signals cost-of-capital impact, floor 7.",
    ),

    # --- Moderate events ---
    (
        "ESG_RATING_CHANGE",
        "ESG score / index inclusion or exclusion",
        None, 6,
        ["esg rating", "esg score", "added to index", "removed from index",
         "msci esg", "sustainalytics", "ftse4good", "dow jones sustainability",
         "nifty esg", "s&p esg"],
        "ESG rating change — investor flow impact, score 5-7 based on magnitude.",
    ),
    (
        "POLICY_FRAMEWORK_UPDATE",
        "Policy or framework update",
        6, None,
        ["policy update", "new guideline", "draft regulation", "consultation paper",
         "sebi consultation", "rbi guidelines", "ministry circular", "framework released",
         "brsr update", "taxonomy update"],
        "Regulatory policy update — compliance impact but not immediate financial, ceiling 6.",
    ),
    (
        "GREEN_BOND_ISSUANCE",
        "Green bond / sustainable finance issuance",
        7, 4,
        ["green bond", "sustainability bond", "esg bond", "social bond", "blue bond",
         "sustainable finance", "green loan", "esg loan", "transition bond"],
        "Green finance issuance — positive ESG signal, score 4-7 based on size.",
    ),
    (
        "MINOR_FINE",
        "Minor regulatory fine (<₹50 Cr)",
        4, None,
        ["fine of", "penalty of", "penalised", "penalized", "show cause notice",
         "regulatory action", "sebi penalty", "rbi penalty"],
        "Minor regulatory penalty — awareness item, ceiling 4 unless amount is large.",
    ),

    # --- Low-ceiling events (routine / noise) ---
    (
        "ROUTINE_CAPEX",
        "Routine capex / expansion announcement",
        5, None,
        ["capex", "capital expenditure", "expansion plan", "new plant", "new facility",
         "sets up", "inaugurates", "opens branch", "new office", "capacity expansion",
         "greenfield", "brownfield", "investment plan"],
        "Routine expansion — operational news, ceiling 5. Only higher if ESG-specific capex.",
    ),
    (
        "ROUTINE_FINANCIAL_RESULTS",
        "Routine quarterly / annual results",
        4, None,
        ["quarterly results", "q1 results", "q2 results", "q3 results", "q4 results",
         "annual results", "profit rises", "profit falls", "revenue up", "revenue down",
         "earnings per share", "net profit", "pat growth", "ebitda"],
        "Financial results — routine disclosure, ceiling 4 unless major miss/beat.",
    ),
    (
        "AWARD_RECOGNITION",
        "Award or recognition",
        3, None,
        ["award", "recognition", "ranked", "felicitated", "certificate", "best company",
         "top employer", "great place to work", "accolade", "honour", "honored"],
        "Award — reputation signal only, ceiling 3.",
    ),
    (
        "PARTNERSHIP_MOU",
        "MoU / partnership announcement",
        5, None,
        ["mou", "memorandum of understanding", "partnership", "collaboration agreement",
         "signs agreement", "ties up with", "joint venture", "strategic alliance"],
        "Partnership or MoU — low-commitment announcement, ceiling 5.",
    ),
    (
        "CSR_ANNOUNCEMENT",
        "CSR / philanthropy announcement",
        4, None,
        ["csr", "corporate social responsibility", "donation", "philanthropy",
         "foundation", "charitable", "community initiative", "skill development program"],
        "CSR activity — brand signal, not financially material, ceiling 4.",
    ),
    (
        "GENERIC_ESG_REPORT",
        "Generic ESG / sustainability report release",
        4, None,
        ["sustainability report", "esg report", "annual report", "integrated report",
         "brsr report", "published its", "releases report"],
        "Routine disclosure — low financial impact, ceiling 4.",
    ),
]


def classify_event(title: str, content: str | None = None) -> EventClassification:
    """Classify an article into an event type and return score bounds.

    Matches against title + first 500 chars of content.
    Returns the FIRST matching rule (ordered by specificity above).
    Falls back to UNKNOWN (no bounds) if nothing matches.
    """
    text = (title + " " + (content[:500] if content else "")).lower()
    # Normalize ₹ amounts for matching
    text = re.sub(r"rs\.?\s*", "₹", text)
    text = re.sub(r"inr\s*", "₹", text)

    for event_code, label, ceiling, floor, keywords, hint in _RULES:
        if any(kw in text for kw in keywords):
            result = EventClassification(
                event_type=label,
                event_code=event_code,
                score_ceiling=ceiling,
                score_floor=floor,
                calibration_hint=hint,
            )
            logger.debug(
                "event_classified",
                event_code=event_code,
                ceiling=ceiling,
                floor=floor,
                title=title[:60],
            )
            return result

    # No rule matched — return unrestricted
    return EventClassification(
        event_type="General ESG news",
        event_code="UNKNOWN",
        score_ceiling=None,
        score_floor=None,
        calibration_hint="No specific event type detected — score based on financial materiality anchors.",
    )


def enforce_score_bounds(
    score: float,
    classification: EventClassification,
    has_financial_quantum: bool = False,
) -> tuple[float, str | None]:
    """Clamp the LLM-generated score to the classification's bounds.

    Also applies the post-score validation rule:
    If score ≥ 7 but no ₹ amount or % figure was found in the article,
    flag for downward adjustment.

    Returns:
        (adjusted_score, warning_message | None)
    """
    warning = None
    adjusted = score

    if classification.score_ceiling is not None and score > classification.score_ceiling:
        adjusted = float(classification.score_ceiling)
        warning = (
            f"Score clamped from {score} → {adjusted} "
            f"(event type '{classification.event_type}' ceiling: {classification.score_ceiling})"
        )

    if classification.score_floor is not None and score < classification.score_floor:
        adjusted = float(classification.score_floor)
        warning = (
            f"Score raised from {score} → {adjusted} "
            f"(event type '{classification.event_type}' floor: {classification.score_floor})"
        )

    # Post-score validation: high score without financial quantum
    if adjusted >= 7.0 and not has_financial_quantum:
        adjusted = min(adjusted, 6.5)
        warning = (
            f"Score reduced from {score} → {adjusted}: "
            "score ≥7 requires a specific ₹ amount or % financial impact in the article"
        )

    if warning:
        logger.info("score_guard_applied", original=score, adjusted=adjusted, reason=warning[:100])

    return adjusted, warning


def has_financial_quantum(text: str) -> bool:
    """Return True if the text contains a specific ₹ amount or % financial figure."""
    text_lower = text.lower()
    # Look for ₹/Rs/crore amounts or percentage impacts
    patterns = [
        r"₹\s*[\d,]+",
        r"rs\.?\s*[\d,]+\s*(?:cr|crore|lakh|million|billion)",
        r"inr\s*[\d,]+",
        r"\d+\s*%\s*(?:revenue|margin|impact|growth|decline|drop|fall|rise)",
        r"(?:revenue|margin|profit|loss|valuation)\s+(?:of|at|by)\s+₹",
    ]
    return any(re.search(p, text_lower) for p in patterns)
