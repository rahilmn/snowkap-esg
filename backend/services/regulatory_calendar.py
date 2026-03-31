"""Regulatory calendar and deadline detection for ESG frameworks.

Enhancement 2: Maps framework mentions in articles to upcoming regulatory
deadlines, enabling time-sensitive prioritization of ESG news.

Covers INDIA (BRSR, SEBI, RBI), EU (CSRD, CSDDD, EU Taxonomy, SFDR),
and GLOBAL (CDP, SBTi, ISSB, GRI, TCFD) frameworks.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Regulatory deadline registry
# ---------------------------------------------------------------------------

REGULATORY_DEADLINES: list[dict] = [
    # ── INDIA ──
    {
        "framework": "BRSR Core",
        "jurisdiction": "INDIA",
        "description": "Annual BRSR Core filing for top 1000 listed companies, due 60 days after fiscal year end (March 31).",
        "deadline_date": date(2026, 5, 30),
        "recurring": True,
        "recurrence_pattern": "annual",
    },
    {
        "framework": "SEBI ESG Disclosure",
        "jurisdiction": "INDIA",
        "description": "Quarterly ESG disclosure for top 1000 listed companies per SEBI circular.",
        "deadline_date": date(2026, 6, 30),
        "recurring": True,
        "recurrence_pattern": "quarterly",
    },
    {
        "framework": "RBI Climate Risk",
        "jurisdiction": "INDIA",
        "description": "RBI climate risk and sustainable finance framework — phased implementation from FY2025-26.",
        "deadline_date": date(2026, 3, 31),
        "recurring": False,
        "recurrence_pattern": None,
    },
    # ── EU ──
    {
        "framework": "CSRD Phase 1",
        "jurisdiction": "EU",
        "description": "CSRD Phase 1: Large public-interest entities (>500 employees), FY2024 reports.",
        "deadline_date": date(2025, 12, 31),
        "recurring": False,
        "recurrence_pattern": None,
    },
    {
        "framework": "CSRD Phase 2",
        "jurisdiction": "EU",
        "description": "CSRD Phase 2: All large companies meeting 2 of 3 size criteria, FY2025 reports.",
        "deadline_date": date(2026, 12, 31),
        "recurring": False,
        "recurrence_pattern": None,
    },
    {
        "framework": "CSRD Phase 3",
        "jurisdiction": "EU",
        "description": "CSRD Phase 3: Listed SMEs (opt-out possible until 2028), FY2026 reports.",
        "deadline_date": date(2027, 12, 31),
        "recurring": False,
        "recurrence_pattern": None,
    },
    {
        "framework": "CSDDD",
        "jurisdiction": "EU",
        "description": "Corporate Sustainability Due Diligence Directive — phased obligations 2027-2029.",
        "deadline_date": date(2027, 7, 26),
        "recurring": False,
        "recurrence_pattern": None,
    },
    {
        "framework": "EU Taxonomy",
        "jurisdiction": "EU",
        "description": "EU Taxonomy annual reporting on alignment of economic activities.",
        "deadline_date": date(2026, 12, 31),
        "recurring": True,
        "recurrence_pattern": "annual",
    },
    {
        "framework": "SFDR",
        "jurisdiction": "EU",
        "description": "Sustainable Finance Disclosure Regulation — periodic PAI reporting.",
        "deadline_date": date(2026, 6, 30),
        "recurring": True,
        "recurrence_pattern": "semi-annual",
    },
    # ── GLOBAL ──
    {
        "framework": "CDP",
        "jurisdiction": "GLOBAL",
        "description": "CDP annual questionnaire — submission window July to October.",
        "deadline_date": date(2026, 10, 31),
        "recurring": True,
        "recurrence_pattern": "annual",
    },
    {
        "framework": "SBTi",
        "jurisdiction": "GLOBAL",
        "description": "Science Based Targets initiative — quarterly validation cycles.",
        "deadline_date": date(2026, 6, 30),
        "recurring": True,
        "recurrence_pattern": "quarterly",
    },
    {
        "framework": "ISSB",
        "jurisdiction": "GLOBAL",
        "description": "IFRS S1 (General) and S2 (Climate) — adoption ongoing, jurisdiction-dependent.",
        "deadline_date": date(2026, 12, 31),
        "recurring": False,
        "recurrence_pattern": None,
    },
    {
        "framework": "IFRS S1",
        "jurisdiction": "GLOBAL",
        "description": "IFRS S1 General Requirements for Sustainability-related Financial Disclosures.",
        "deadline_date": date(2026, 12, 31),
        "recurring": False,
        "recurrence_pattern": None,
    },
    {
        "framework": "IFRS S2",
        "jurisdiction": "GLOBAL",
        "description": "IFRS S2 Climate-related Disclosures — adoption timeline varies by jurisdiction.",
        "deadline_date": date(2026, 12, 31),
        "recurring": False,
        "recurrence_pattern": None,
    },
    {
        "framework": "GRI",
        "jurisdiction": "GLOBAL",
        "description": "GRI Standards — no fixed deadline but annual reporting cycle expected.",
        "deadline_date": date(2026, 12, 31),
        "recurring": True,
        "recurrence_pattern": "annual",
    },
    {
        "framework": "TCFD",
        "jurisdiction": "GLOBAL",
        "description": "TCFD recommendations sunset into ISSB/IFRS S2 — transition period through 2025.",
        "deadline_date": date(2025, 12, 31),
        "recurring": False,
        "recurrence_pattern": None,
    },
]

# ---------------------------------------------------------------------------
# Framework name aliases for fuzzy matching against article text
# ---------------------------------------------------------------------------

_FRAMEWORK_ALIASES: dict[str, list[str]] = {
    "BRSR Core": ["brsr", "brsr core", "business responsibility"],
    "SEBI ESG Disclosure": ["sebi", "sebi esg", "sebi disclosure"],
    "RBI Climate Risk": ["rbi", "rbi climate", "reserve bank climate"],
    "CSRD Phase 1": ["csrd", "csrd phase 1", "corporate sustainability reporting"],
    "CSRD Phase 2": ["csrd", "csrd phase 2"],
    "CSRD Phase 3": ["csrd", "csrd phase 3", "listed sme"],
    "CSDDD": ["csddd", "cs3d", "due diligence directive"],
    "EU Taxonomy": ["eu taxonomy", "taxonomy regulation"],
    "SFDR": ["sfdr", "sustainable finance disclosure"],
    "CDP": ["cdp", "carbon disclosure project"],
    "SBTi": ["sbti", "science based target", "science-based target"],
    "ISSB": ["issb"],
    "IFRS S1": ["ifrs s1", "ifrs sustainability"],
    "IFRS S2": ["ifrs s2", "ifrs climate"],
    "GRI": ["gri", "global reporting initiative"],
    "TCFD": ["tcfd", "task force on climate"],
}


# ---------------------------------------------------------------------------
# Deadline detection phrases
# ---------------------------------------------------------------------------

_DEADLINE_PHRASES: list[str] = [
    "effective from",
    "mandatory by",
    "compliance deadline",
    "filing date",
    "due date",
    "comes into force",
    "implementation date",
    "phase-in",
    "transition period",
]

# Pre-compiled regex for deadline phrase detection
_DEADLINE_PATTERN = re.compile(
    "|".join(re.escape(phrase) for phrase in _DEADLINE_PHRASES),
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_nearest_deadline(
    framework_mentions: list[str],
    jurisdiction: str | None = None,
) -> dict | None:
    """Find the nearest upcoming regulatory deadline matching the given frameworks.

    Args:
        framework_mentions: Framework names/codes mentioned in an article
            (e.g. ["BRSR:P6", "CSRD", "GRI:305"]).
        jurisdiction: Optional filter — "INDIA", "EU", or "GLOBAL".

    Returns:
        Dict with deadline info and ``days_until``, or None if no match.
    """
    # BUG-20: Use UTC date instead of local system date
    today = datetime.now(timezone.utc).date()

    # Normalise mentions to lowercase for matching
    mentions_lower = [m.lower().split(":")[0] for m in framework_mentions]

    candidates: list[dict] = []

    for entry in REGULATORY_DEADLINES:
        # Jurisdiction filter
        if jurisdiction and entry["jurisdiction"] != jurisdiction.upper():
            continue

        # Skip deadlines already passed (unless recurring — project to next cycle)
        effective_date = _effective_deadline(entry, today)
        if effective_date is None:
            continue

        # Check if any mention matches this framework
        framework_lower = entry["framework"].lower()
        aliases = _FRAMEWORK_ALIASES.get(entry["framework"], [])

        matched = False
        for mention in mentions_lower:
            if mention in framework_lower or framework_lower.startswith(mention):
                matched = True
                break
            for alias in aliases:
                if mention in alias or alias in mention:
                    matched = True
                    break
            if matched:
                break

        if matched:
            days_until = (effective_date - today).days
            candidates.append({
                "framework": entry["framework"],
                "jurisdiction": entry["jurisdiction"],
                "description": entry["description"],
                "deadline_date": effective_date.isoformat(),
                "days_until": days_until,
                "recurring": entry["recurring"],
                "recurrence_pattern": entry["recurrence_pattern"],
            })

    if not candidates:
        return None

    # Return the nearest upcoming deadline
    candidates.sort(key=lambda c: c["days_until"])
    nearest = candidates[0]

    logger.debug(
        "regulatory_deadline_found",
        framework=nearest["framework"],
        days_until=nearest["days_until"],
        candidates=len(candidates),
    )
    return nearest


def detect_deadline_language(text: str) -> list[str]:
    """Scan article text for regulatory deadline-related phrases.

    Args:
        text: Article title, summary, or body text.

    Returns:
        List of matched deadline phrases found in the text.
    """
    matches = _DEADLINE_PATTERN.findall(text)
    # Deduplicate while preserving order, normalise to lowercase
    seen: set[str] = set()
    result: list[str] = []
    for match in matches:
        key = match.lower()
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _effective_deadline(entry: dict, today: date) -> date | None:
    """Return the next applicable deadline date, projecting recurring entries forward."""
    deadline = entry["deadline_date"]

    if deadline >= today:
        return deadline

    # For non-recurring deadlines that have passed, skip
    if not entry["recurring"]:
        return None

    # Project recurring deadlines forward
    pattern = entry.get("recurrence_pattern", "annual")

    if pattern == "annual":
        # Move to same month/day in the next applicable year
        candidate = deadline.replace(year=today.year)
        if candidate < today:
            candidate = deadline.replace(year=today.year + 1)
        return candidate

    if pattern == "quarterly":
        # Find next quarter-end after today
        quarter_ends = [
            date(today.year, 3, 31),
            date(today.year, 6, 30),
            date(today.year, 9, 30),
            date(today.year, 12, 31),
        ]
        for qe in quarter_ends:
            if qe >= today:
                return qe
        return date(today.year + 1, 3, 31)

    if pattern == "semi-annual":
        semi_dates = [
            date(today.year, 6, 30),
            date(today.year, 12, 31),
        ]
        for sd in semi_dates:
            if sd >= today:
                return sd
        return date(today.year + 1, 6, 30)

    # Fallback: treat as annual
    candidate = deadline.replace(year=today.year)
    if candidate < today:
        candidate = deadline.replace(year=today.year + 1)
    return candidate
