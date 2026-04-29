"""Persona-bar scoring for comparison harness (Phase 5).

Scores an ESG brief (ours or a competitor's) on 30 dimensions covering the
three personas we ship: CFO, CEO, ESG Analyst. Each dimension is 0-3:

  0 — absent / generic
  1 — partial (has the concept but misses specificity)
  2 — present and specific
  3 — strong (exact match to the professional bar)

The scoring is regex + keyword-based. Deterministic, fast, not perfect —
but measurable. The goal is *relative* scoring (ours vs GPT-4o vs Gemini),
not absolute truth. Use with the harness in `scripts/compare_vs_chatgpt.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Text fragments / patterns used across personas
# ---------------------------------------------------------------------------

RUPEE_FIGURE = re.compile(
    r"(?:₹|Rs\.?|INR)\s*\d+(?:[,.]\d+)?\s*(?:Cr|crore|Lakh|Lkh|L)?",
    re.IGNORECASE,
)
BPS_FIGURE = re.compile(r"\d+(?:\.\d+)?\s*bps\b", re.IGNORECASE)
SOURCE_TAG = re.compile(r"\((?:from\s+article|engine\s+estimate)\)", re.IGNORECASE)

FRAMEWORK_CODES = [
    # specific section-level codes
    r"BRSR[:\s]+P\d+[:\s]+Q\d+",
    r"GRI[:\s]+\d{3}(?:[-:]\d+)?",
    r"ESRS[:\s]+[EGS]\d+",
    r"TCFD[:\s]+(?:governance|strategy|risk|metrics)",
    r"CSRD[:\s]+\w+",
    r"ISSB[:\s]+IFRS\s*S\d+",
    r"SASB[:\s]+[A-Z]+[-\w]*",
]
FRAMEWORK_SECTION_RE = re.compile("|".join(FRAMEWORK_CODES), re.IGNORECASE)

# Generic framework mentions (without section code)
FRAMEWORK_GENERIC = re.compile(
    r"\b(?:BRSR|GRI|ESRS|TCFD|CSRD|ISSB|SASB|CDP|SBTi|TNFD|SEC\s*Climate)\b",
    re.IGNORECASE,
)

# Peer mentions with date + ₹ (strong precedent)
PRECEDENT_STRONG = re.compile(
    r"(?P<peer>[A-Z][A-Za-z &]{3,30}?)\s*\(?\s*(?P<yr>20\d{2})\s*\)?"
    r".{0,200}?(?P<amt>(?:₹|Rs\.?|INR)\s*\d+(?:[,.]\d+)?\s*(?:Cr|crore|Lakh))",
    re.IGNORECASE | re.DOTALL,
)

# Proxy advisors
PROXY_ADVISORS = re.compile(r"\b(?:ISS|Glass\s*Lewis|InGovern|IiAS|SES\b)", re.IGNORECASE)

# Investor names commonly cited
BIG_INVESTORS = re.compile(
    r"\b(?:BlackRock|Vanguard|State\s*Street|Norges|NBIM|CalPERS|CalSTRS|MSCI\s+ESG|Sustainalytics)\b",
    re.IGNORECASE,
)

# Regulators
REGULATORS = re.compile(
    r"\b(?:SEBI|RBI|NGT|CPCB|CERT[-\s]?In|MoEF|DGMS|Ministry\s+of\s+Power|CERC|FERC|SEC)\b",
    re.IGNORECASE,
)

# Q&A section indicators — accept underscore/hyphen/space separators
# (structured JSON fields often use 'earnings_call'; prose uses 'earnings call')
QNA_SECTIONS = {
    "earnings_call": re.compile(r"earnings[-_\s]?call|investor[-_\s]?call|Q\s*&\s*A\s+(?:for\s+)?(?:earnings|investors)", re.IGNORECASE),
    "press": re.compile(r"press[-_\s]?(?:statement|release|note)", re.IGNORECASE),
    "board": re.compile(r"board(?:[-_\s]?level)?[-_\s]?(?:Q\s*&\s*A|qa|brief|pack|prep)", re.IGNORECASE),
    "regulator": re.compile(r"(?:to|for)\s+(?:SEBI|RBI|the\s+regulator)|regulator[-_\s]?(?:engagement|qa|q\s*&\s*a)", re.IGNORECASE),
}

# TCFD scenario paths
TCFD_PATHS = re.compile(r"1\.5\s*[°]?C|2\s*[°]?C|4\s*[°]?C|NZE|SSP\d", re.IGNORECASE)

# SDG sub-goal (e.g., 8.7, 16.6) NOT just "SDG 8"
SDG_SUBGOAL = re.compile(r"SDG\s*\d{1,2}\.\d", re.IGNORECASE)
SDG_GENERIC = re.compile(r"SDG\s*\d{1,2}\b", re.IGNORECASE)

# Confidence bound signals
BETA_PATTERN = re.compile(r"(?:β|beta)[\s=:]+\d+(?:\.\d+)?[-–]\d+(?:\.\d+)?", re.IGNORECASE)
LAG_PATTERN = re.compile(r"lag[-\s]?k?\s*[:=]?\s*\d+[-–]\d+\s*(?:m|q|month|quarter|week|day|year)", re.IGNORECASE)
FUNC_FORM_PATTERN = re.compile(
    r"\b(?:linear|log[-\s]?linear|threshold|ratio|step|composite)\b.*?(?:form|functional)|(?:form|functional)\b.*?\b(?:linear|log[-\s]?linear|threshold|ratio|step|composite)\b",
    re.IGNORECASE,
)

# ROI cap disclosure
ROI_CAP_WORDS = re.compile(r"cap(?:ped)?\s+at\s+\d+%|\d+%\s*\(capped|ceiling|cap\)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Scoring dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DimensionScore:
    name: str
    score: int  # 0-3
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "score": self.score, "evidence": self.evidence[:200]}


@dataclass
class PersonaScore:
    persona: str  # "cfo" | "ceo" | "esg_analyst"
    dimensions: list[DimensionScore] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(d.score for d in self.dimensions)

    @property
    def max_total(self) -> int:
        return 3 * len(self.dimensions)

    @property
    def pct(self) -> float:
        return (self.total / self.max_total * 100) if self.max_total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "persona": self.persona,
            "total": self.total,
            "max_total": self.max_total,
            "pct": round(self.pct, 1),
            "dimensions": [d.to_dict() for d in self.dimensions],
        }


@dataclass
class Scorecard:
    source: str  # "snowkap" | "gpt4o" | "gemini"
    cfo: PersonaScore
    ceo: PersonaScore
    esg_analyst: PersonaScore

    @property
    def total(self) -> int:
        return self.cfo.total + self.ceo.total + self.esg_analyst.total

    @property
    def max_total(self) -> int:
        return self.cfo.max_total + self.ceo.max_total + self.esg_analyst.max_total

    @property
    def pct(self) -> float:
        return (self.total / self.max_total * 100) if self.max_total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "total": self.total,
            "max_total": self.max_total,
            "pct": round(self.pct, 1),
            "cfo": self.cfo.to_dict(),
            "ceo": self.ceo.to_dict(),
            "esg_analyst": self.esg_analyst.to_dict(),
        }


# ---------------------------------------------------------------------------
# Scoring functions — one per persona
# ---------------------------------------------------------------------------


def _count(pattern: re.Pattern, text: str) -> int:
    return len(pattern.findall(text or ""))


def _score_cfo(text: str) -> PersonaScore:
    """10 dimensions for CFO brief."""
    dims: list[DimensionScore] = []

    # 1. Specific ₹ figure (not generic range)
    rupee_hits = _count(RUPEE_FIGURE, text)
    if rupee_hits >= 3:
        dims.append(DimensionScore("specific_rupee_figure", 3, f"{rupee_hits} ₹ figures"))
    elif rupee_hits >= 1:
        dims.append(DimensionScore("specific_rupee_figure", 2, f"{rupee_hits} ₹ figures"))
    else:
        dims.append(DimensionScore("specific_rupee_figure", 0, "no ₹ figures"))

    # 2. Source tag on ₹ figures
    tag_hits = _count(SOURCE_TAG, text)
    if tag_hits >= 2:
        dims.append(DimensionScore("source_tag_on_rupee", 3, f"{tag_hits} source tags"))
    elif tag_hits == 1:
        dims.append(DimensionScore("source_tag_on_rupee", 2, "1 source tag"))
    else:
        dims.append(DimensionScore("source_tag_on_rupee", 0, "no source tags"))

    # 3. Framework section-level citation
    section_hits = _count(FRAMEWORK_SECTION_RE, text)
    generic_hits = _count(FRAMEWORK_GENERIC, text)
    if section_hits >= 2:
        dims.append(DimensionScore("framework_section_cite", 3, f"{section_hits} section codes"))
    elif section_hits == 1:
        dims.append(DimensionScore("framework_section_cite", 2, "1 section code"))
    elif generic_hits > 0:
        dims.append(DimensionScore("framework_section_cite", 1, f"{generic_hits} generic framework mentions"))
    else:
        dims.append(DimensionScore("framework_section_cite", 0, "no framework mentions"))

    # 4. Peer benchmark with date + ₹
    strong = PRECEDENT_STRONG.search(text)
    if strong:
        dims.append(DimensionScore("peer_precedent_with_date_rupee", 3, strong.group(0)[:140]))
    elif re.search(r"\b20\d{2}\b", text) and RUPEE_FIGURE.search(text):
        dims.append(DimensionScore("peer_precedent_with_date_rupee", 1, "year + ₹ in same text but not joint"))
    else:
        dims.append(DimensionScore("peer_precedent_with_date_rupee", 0, "no named precedent with date + ₹"))

    # 5. Regulatory deadline.
    # Phase 13 S2 — build the year/FY whitelist from the current calendar
    # year so the regex doesn't go stale (was hardcoded `2026|2027|FY2[56]`
    # which would silently miss real deadline mentions in 2027+).
    from datetime import datetime
    _now_year = datetime.now().year
    _years = "|".join(str(y) for y in range(_now_year - 1, _now_year + 4))
    _fy_suffixes = sorted({(y % 100) for y in range(_now_year, _now_year + 4)})
    _fy_alt = "|".join(f"FY{s:02d}" for s in _fy_suffixes)
    _fy_q_alt = "|".join(rf"Q[1-4]\s*FY{s:02d}" for s in _fy_suffixes)
    _deadline_pattern = (
        rf"\b(?:{_years}|{_fy_alt}|{_fy_q_alt}|deadline|due\s+by|filed\s+by)\b"
        rf"|\b(?:by|before|until)\s+\w+\s+(?:{_years}|{_fy_alt})"
    )
    deadline = re.search(_deadline_pattern, text, re.IGNORECASE)
    if deadline:
        dims.append(DimensionScore("regulatory_deadline", 2, deadline.group(0)[:60]))
    else:
        dims.append(DimensionScore("regulatory_deadline", 0, "no regulatory deadline"))

    # 6. Do-nothing cost — looser, catches underscore + natural-language variants
    donothing = re.search(
        r"\b(?:cost\s+of\s+inaction|do[-_\s]?nothing|failure\s+to\s+act|"
        r"if\s+(?:no\s+(?:action|remediation)|we\s+(?:ignore|don't\s+act|doesn't\s+act))|"
        r"delayed?\s+action|inaction\s+(?:cost|risk)|"
        r"trajectory[:\s]+do[-_\s]?nothing)\b",
        text, re.IGNORECASE,
    )
    if donothing:
        dims.append(DimensionScore("cost_of_inaction", 2, donothing.group(0)[:60]))
    else:
        dims.append(DimensionScore("cost_of_inaction", 0, "do-nothing cost not stated"))

    # 7. CFO headline / length — look for a crisp first 100 words
    first_100 = " ".join(text.split()[:100])
    if re.search(r"\bCFO\b|\bP&L\b|margin|revenue\b", first_100, re.IGNORECASE) and RUPEE_FIGURE.search(first_100):
        dims.append(DimensionScore("cfo_tight_first_100_words", 2, "CFO-oriented + ₹ in first 100 words"))
    else:
        dims.append(DimensionScore("cfo_tight_first_100_words", 0, ""))

    # 8. Concrete lever (ministerial / named action with ₹ or timeline)
    concrete = re.search(
        r"(?:implement|commission|issue|file|appoint|deploy|build|reduce|increase)\s+[\w\s]+?\b(?:by\s+[\w\s]+20\d{2}|[0-9,]+\s*(?:Cr|crore|MW|GW|%))",
        text, re.IGNORECASE,
    )
    if concrete:
        dims.append(DimensionScore("concrete_lever", 3, concrete.group(0)[:120]))
    else:
        dims.append(DimensionScore("concrete_lever", 0, "no concrete lever"))

    # 9. ROI with cap disclosure
    if ROI_CAP_WORDS.search(text):
        dims.append(DimensionScore("roi_cap_disclosed", 3, "ROI cap disclosed"))
    elif re.search(r"\broi\b.{0,40}\d+%", text, re.IGNORECASE):
        dims.append(DimensionScore("roi_cap_disclosed", 1, "ROI % stated but no cap"))
    else:
        dims.append(DimensionScore("roi_cap_disclosed", 0, "no ROI"))

    # 10. Recovery path / timeline
    recovery = re.search(
        r"recover(?:y|ed)?[\s\w]+(?:in|within|after)\s+\d+[-\s]?(?:month|quarter|year|m|q|y)\b"
        r"|return\s+to\s+baseline",
        text, re.IGNORECASE,
    )
    if recovery:
        dims.append(DimensionScore("recovery_path", 2, recovery.group(0)[:80]))
    else:
        dims.append(DimensionScore("recovery_path", 0, "no recovery path"))

    return PersonaScore(persona="cfo", dimensions=dims)


def _score_ceo(text: str) -> PersonaScore:
    """10 dimensions for CEO brief."""
    dims: list[DimensionScore] = []

    # 1. Board-ready paragraph (60-120 words, no bullets)
    board_para = re.search(
        r"(?:board\s+(?:must|should|needs?|action|paragraph|brief)|Board\s+Action)[^\n]{200,1000}",
        text, re.IGNORECASE,
    )
    if board_para:
        dims.append(DimensionScore("board_ready_paragraph", 3, board_para.group(0)[:100]))
    elif re.search(r"\bboard\b", text, re.IGNORECASE):
        dims.append(DimensionScore("board_ready_paragraph", 1, "mentions board"))
    else:
        dims.append(DimensionScore("board_ready_paragraph", 0, "no board-level framing"))

    # 2. Named stakeholders (count regulators + proxy + big investors)
    stake_count = _count(REGULATORS, text) + _count(PROXY_ADVISORS, text) + _count(BIG_INVESTORS, text)
    if stake_count >= 4:
        dims.append(DimensionScore("named_stakeholders", 3, f"{stake_count} named stakeholders"))
    elif stake_count >= 2:
        dims.append(DimensionScore("named_stakeholders", 2, f"{stake_count} stakeholders"))
    elif stake_count == 1:
        dims.append(DimensionScore("named_stakeholders", 1, "1 stakeholder"))
    else:
        dims.append(DimensionScore("named_stakeholders", 0, "no named stakeholders"))

    # 3. Stakeholder stance with precedent (proxy advisor + timing)
    stance = re.search(
        r"(?:ISS|Glass\s*Lewis|SEBI|RBI|MSCI|BlackRock|Norges).{0,100}"
        r"(?:within|in\s+\d+[-\s]?\d*\s*(?:days|weeks|months|quarters))",
        text, re.IGNORECASE,
    )
    if stance:
        dims.append(DimensionScore("stakeholder_stance_with_window", 3, stance.group(0)[:140]))
    else:
        dims.append(DimensionScore("stakeholder_stance_with_window", 0, ""))

    # 4. Named analogous precedent with date + ₹ (reuse CFO-like check)
    strong = PRECEDENT_STRONG.search(text)
    if strong:
        dims.append(DimensionScore("analogous_precedent", 3, strong.group(0)[:140]))
    else:
        dims.append(DimensionScore("analogous_precedent", 0, "no named precedent"))

    # 5. 3-year trajectory — accept underscore separators
    traj = re.search(
        r"(?:do[-_\s]?nothing|inaction|no\s+action).{0,600}?(?:act[-_\s]?now|act\s+today|with\s+action|act\b)"
        r"|3[-\s]?year\s+(?:trajectory|path)"
        r"|FY2[6-9].{0,200}FY2[6-9]"
        r"|trajectory[:\s]+do[-_\s]?nothing",
        text, re.IGNORECASE | re.DOTALL,
    )
    if traj:
        dims.append(DimensionScore("three_year_trajectory", 3, traj.group(0)[:140]))
    else:
        dims.append(DimensionScore("three_year_trajectory", 0, "no do-nothing vs act-now framing"))

    # 6. Q&A drafts (check for 2+ of the 4 contexts)
    qna_present = sum(1 for pat in QNA_SECTIONS.values() if pat.search(text))
    if qna_present >= 3:
        dims.append(DimensionScore("qna_drafts", 3, f"{qna_present}/4 Q&A contexts"))
    elif qna_present >= 2:
        dims.append(DimensionScore("qna_drafts", 2, f"{qna_present}/4 Q&A contexts"))
    elif qna_present == 1:
        dims.append(DimensionScore("qna_drafts", 1, "1 Q&A context"))
    else:
        dims.append(DimensionScore("qna_drafts", 0, "no Q&A drafts"))

    # 7. Proxy advisor signal
    if PROXY_ADVISORS.search(text):
        dims.append(DimensionScore("proxy_advisor_signal", 2, "proxy advisor named"))
    else:
        dims.append(DimensionScore("proxy_advisor_signal", 0, ""))

    # 8. Investor behavior named
    if BIG_INVESTORS.search(text):
        dims.append(DimensionScore("investor_behavior_named", 2, "big investor named"))
    else:
        dims.append(DimensionScore("investor_behavior_named", 0, ""))

    # 9. Talent / workforce impact
    if re.search(r"\b(?:attrition|retention|talent|Glassdoor|hiring|recruit|union|strike|workforce)\b", text, re.IGNORECASE):
        dims.append(DimensionScore("talent_impact", 2, "talent/workforce mentioned"))
    else:
        dims.append(DimensionScore("talent_impact", 0, ""))

    # 10. Regulatory window timing
    window = re.search(
        r"(?:within|in)\s+\d+[-\s]?(?:to\s+)?\d*\s*(?:days|weeks|months|quarters)\b.*?(?:SEBI|RBI|regulator|enforcement)",
        text, re.IGNORECASE,
    )
    if window:
        dims.append(DimensionScore("regulatory_window", 2, window.group(0)[:100]))
    else:
        dims.append(DimensionScore("regulatory_window", 0, ""))

    return PersonaScore(persona="ceo", dimensions=dims)


def _score_esg_analyst(text: str) -> PersonaScore:
    """10 dimensions for ESG Analyst brief."""
    dims: list[DimensionScore] = []

    # 1. KPI table with ≥3 KPIs
    kpi_hits = sum(1 for kw in [
        "scope 1", "scope 2", "scope 3", "ltifr", "fatalit", "women on board",
        "board independence", "cyber incident", "water intensity", "renewable share",
        "emissions intensity", "waste", "training hours",
    ] if re.search(rf"\b{kw}\b", text, re.IGNORECASE))
    if kpi_hits >= 3:
        dims.append(DimensionScore("kpi_table", 3, f"{kpi_hits} KPIs"))
    elif kpi_hits >= 1:
        dims.append(DimensionScore("kpi_table", 2, f"{kpi_hits} KPIs"))
    else:
        dims.append(DimensionScore("kpi_table", 0, "no specific KPIs"))

    # 2. Peer quartile positioning
    quartile = re.search(
        r"\b(?:25th|50th|75th|90th|p25|p50|p75|median|quartile)\b.*?(?:peer|industry|benchmark)",
        text, re.IGNORECASE,
    )
    if quartile:
        dims.append(DimensionScore("peer_quartile", 3, quartile.group(0)[:120]))
    elif re.search(r"peer\s+median", text, re.IGNORECASE):
        dims.append(DimensionScore("peer_quartile", 2, "peer median mentioned"))
    else:
        dims.append(DimensionScore("peer_quartile", 0, ""))

    # 3. Confidence bounds — β + lag + form
    has_beta = BETA_PATTERN.search(text) is not None
    has_lag = LAG_PATTERN.search(text) is not None
    has_form = FUNC_FORM_PATTERN.search(text) is not None
    count = sum([has_beta, has_lag, has_form])
    if count == 3:
        dims.append(DimensionScore("confidence_bounds", 3, "β + lag + form all present"))
    elif count >= 2:
        dims.append(DimensionScore("confidence_bounds", 2, f"{count}/3 confidence-bound signals"))
    elif count == 1:
        dims.append(DimensionScore("confidence_bounds", 1, "partial confidence signals"))
    else:
        dims.append(DimensionScore("confidence_bounds", 0, "no β/lag/form"))

    # 4. Double materiality
    dm = re.search(
        r"(?:financial\s+materiality|impact\s+on\s+world|double\s+materiality|impact\s+materiality)",
        text, re.IGNORECASE,
    )
    if dm:
        dims.append(DimensionScore("double_materiality", 3, dm.group(0)[:80]))
    else:
        dims.append(DimensionScore("double_materiality", 0, ""))

    # 5. TCFD scenario framing (1.5/2/4°C)
    tcfd_count = len(set(m.group(0).lower() for m in TCFD_PATHS.finditer(text)))
    if tcfd_count >= 3:
        dims.append(DimensionScore("tcfd_scenarios", 3, f"{tcfd_count} scenario paths"))
    elif tcfd_count >= 1:
        dims.append(DimensionScore("tcfd_scenarios", 2, f"{tcfd_count} scenario path"))
    else:
        dims.append(DimensionScore("tcfd_scenarios", 0, ""))

    # 6. SDG at sub-goal level
    if SDG_SUBGOAL.search(text):
        dims.append(DimensionScore("sdg_subgoal", 3, SDG_SUBGOAL.search(text).group(0)))
    elif SDG_GENERIC.search(text):
        dims.append(DimensionScore("sdg_subgoal", 1, "generic SDG only"))
    else:
        dims.append(DimensionScore("sdg_subgoal", 0, ""))

    # 7. Audit trail (links claim → ontology/precedent/article)
    audit = re.search(
        r"(?:audit\s+trail|audit[:\s]+\w+|derivation|derived\s+from|"
        r"per\s+(?:ontology|cascade|precedent|primitive)|"
        r"primitive\s+cascade|sources?[:\s]+\[|"
        r"P2::[A-Z]+[→\-]>[A-Z]+)",
        text, re.IGNORECASE,
    )
    if audit:
        dims.append(DimensionScore("audit_trail", 2, audit.group(0)[:80]))
    else:
        dims.append(DimensionScore("audit_trail", 0, ""))

    # 8. Framework rationale (section code paired with "rationale"/"because"/"triggered because")
    rationale = re.search(
        r"(?:BRSR|GRI|ESRS|TCFD|CSRD|ISSB|SASB)[:\s]+\w+.{0,200}?"
        r"(?:rationale|because|triggered|mandatory|required|applicable\s+because)",
        text, re.IGNORECASE,
    )
    if rationale:
        dims.append(DimensionScore("framework_rationale", 3, rationale.group(0)[:120]))
    elif FRAMEWORK_SECTION_RE.search(text):
        dims.append(DimensionScore("framework_rationale", 1, "section cite but no rationale"))
    else:
        dims.append(DimensionScore("framework_rationale", 0, ""))

    # 9. Data source cited
    source_cited = re.search(
        r"\b(?:CDP|BRSR|Bloomberg|Refinitiv|PCAF|MSCI|Sustainalytics|DJSI|RE100|S&P|Moody's)\b",
        text, re.IGNORECASE,
    )
    if source_cited:
        dims.append(DimensionScore("data_source_cited", 2, source_cited.group(0)))
    else:
        dims.append(DimensionScore("data_source_cited", 0, ""))

    # 10. No fabricated-looking precedents (negative check)
    # Heuristic: if output has precedents but also says "based on" / "as I've seen" without concrete case names → soft flag.
    fabricated_signal = re.search(
        r"(?:based\s+on\s+(?:general|industry|my\s+understanding)|typical\s+(?:case|scenario)|"
        r"in\s+(?:general|the\s+industry)|might\s+have\s+seen)",
        text, re.IGNORECASE,
    )
    if not fabricated_signal and PRECEDENT_STRONG.search(text):
        dims.append(DimensionScore("no_fabricated_precedents", 3, "named precedents, no fabrication flags"))
    elif not fabricated_signal:
        dims.append(DimensionScore("no_fabricated_precedents", 2, "no fabrication flags (no precedents either)"))
    else:
        dims.append(DimensionScore("no_fabricated_precedents", 0, "fabrication-signal language present"))

    return PersonaScore(persona="esg_analyst", dimensions=dims)


def score_brief(text: str, source: str) -> Scorecard:
    """Score a brief (string) across all three personas.

    `text`: concatenated output — for our system it's the perspectives
    joined together; for a competitor it's the raw LLM response.
    `source`: "snowkap" | "gpt4o" | "gemini".
    """
    return Scorecard(
        source=source,
        cfo=_score_cfo(text or ""),
        ceo=_score_ceo(text or ""),
        esg_analyst=_score_esg_analyst(text or ""),
    )


def compute_win_matrix(scorecards: list[Scorecard]) -> dict[str, Any]:
    """Per-dimension winner across sources.

    Returns {persona: {dimension_name: winner_source | 'tie'}}.
    Useful for the harness HTML / markdown report.
    """
    out: dict[str, dict[str, str]] = {"cfo": {}, "ceo": {}, "esg_analyst": {}}
    for persona in ("cfo", "ceo", "esg_analyst"):
        # Collect per-dimension scores across sources
        dim_names: list[str] = []
        for sc in scorecards:
            p = getattr(sc, persona)
            for d in p.dimensions:
                if d.name not in dim_names:
                    dim_names.append(d.name)
        for dim_name in dim_names:
            best_score = -1
            winners: list[str] = []
            for sc in scorecards:
                p = getattr(sc, persona)
                for d in p.dimensions:
                    if d.name == dim_name:
                        if d.score > best_score:
                            best_score = d.score
                            winners = [sc.source]
                        elif d.score == best_score:
                            winners.append(sc.source)
                        break
            out[persona][dim_name] = winners[0] if len(winners) == 1 else "tie"
    return out
