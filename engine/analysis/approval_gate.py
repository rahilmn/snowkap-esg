"""Phase 48.D — final display-approval gate.

A SECOND LLM (Opus 4.6) reviews every CRITICAL article's composed analysis
against the source article body BEFORE it is shown on the frontend. This is
the user's "add another LLM to approve and then only display on frontend"
requirement, and it directly prevents the failure modes that surfaced in
Phase 47 (fabricated ₹ figures, off-topic recommendations, ungrounded lede
claims).

Two paths:
  * CRITICAL articles (full Stage 10-12 + lede): Opus 4.6 reads the article
    body + the composed lede + 4 bullets + recommendations and returns a
    JSON verdict {approved, confidence, issues}. Rejected articles are NOT
    persisted to the deck — the orchestrator backfills from the candidate
    buffer.
  * LIGHT articles (Stages 1-9 only): deterministic checks (headline present,
    event classified, fresh, what_changed populated). No LLM — the light
    tier has minimal generated content + low fabrication risk.

Pre-LLM cheap checks reuse the existing verifiers so the expensive Opus call
only runs on content that already passed the mechanical gates.

Phase 49.1 — FAIL-CLOSED for criticals: if the approval LLM errors or returns
an unparseable verdict, the critical article is REJECTED (→ demoted to the
light tier, which has no lede/recs/₹ and so nothing to fabricate). A
rich-but-fabricated card is worse than a thin-but-accurate one; accuracy is
the product's bar. (Earlier fail-open let a JSW card with a fabricated
"₹588 Cr quarterly profit" lede through on a parse blip.) The light path
stays deterministic, so it is unaffected.
"""
from __future__ import annotations

from engine.analysis.text_budget import clamp_article_text

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ApprovalResult:
    approved: bool
    confidence: float = 0.0
    issues: list[str] = field(default_factory=list)
    reviewer: str = ""  # "opus" | "deterministic" | "fail_open"

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "confidence": self.confidence,
            "issues": self.issues,
            "reviewer": self.reviewer,
        }


_APPROVAL_SYSTEM = """You are a senior ESG editor doing a final fact-check before \
an analysis is shown to a CFO. You are given the SOURCE ARTICLE and the \
ANALYSIS our engine produced (an editorial lede, a 4-bullet brief, and \
recommendations). Decide whether the analysis is SAFE TO PUBLISH.

Draw a hard line between two kinds of content:

1. CLAIMS ABOUT THE COMPANY / THE EVENT (the lede, what-changed, why-it-matters,
   and any ₹/$/€ figure attributed to THIS company as a fact). These MUST be
   grounded in the source article.

2. ADVISORY CONTEXT (recommendations and their peer benchmarks). These are our
   analyst's expert guidance and MAY draw on industry knowledge OUTSIDE the
   article — a recommendation citing "Tata Power issued a green bond" or a peer
   comparison is NORMAL and ACCEPTABLE even if that peer is not named in the
   source article. Do NOT reject for peer benchmarks being absent from the article.

REJECT (approved=false) only if ANY of these are true:
- A fact ABOUT THE COMPANY or THE EVENT (in the lede / what-changed / why-it-matters)
  contradicts or is absent from the source article (e.g. "already reports under
  BRSR/TCFD" when the article never says so; a capacity/figure that misreads the
  article; a regulator action the article doesn't mention).
- A ₹/$/€ figure is presented as THE COMPANY's actual figure-from-the-article when
  the article contains no such figure, it isn't marked an estimate, AND it does not
  match the engine's modeled exposure shown in the FINANCIAL EXPOSURE context below.
  (The engine deliberately models a TOTAL exposure — legal + reputational +
  cost-of-capital — that is legitimately LARGER than the single amount the article
  states. A why-it-matters / criticality line that leads with that modeled total is
  EXPECTED analytical output, not a fabrication. Reject only a figure that matches
  NEITHER the article's stated amount NOR the engine's modeled exposure, or one
  falsely tagged "(from article)".)
- The text is GARBLED, truncated, or has incomplete sentences (e.g. "exposed to a.",
  "₹3,000–", "realize these b") — never publish broken prose.
- The analysis is internally CONTRADICTORY (e.g. band says "Low priority" while the
  text frames it as strategically significant).
- A recommendation is plainly OFF-TOPIC (unrelated to the article's subject).

APPROVE (approved=true) if the company/event claims are grounded, the prose is
clean and consistent, and the recommendations are on-topic. Peer benchmarks
drawing on outside knowledge are fine. Minor style nits are not grounds for rejection.

Respond with ONLY a JSON object, no prose:
{"approved": true|false, "confidence": 0.0-1.0, "issues": ["short reason", ...]}"""


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of an LLM response (handles ``` fences)."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    start = t.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _approve_light(result: Any, analysis: dict[str, Any]) -> ApprovalResult:
    """Deterministic gate for light-tier articles. No LLM."""
    issues: list[str] = []
    wc = (analysis or {}).get("what_changed") or {}
    if not (wc.get("headline") or "").strip():
        issues.append("missing headline")
    if not (result is not None and getattr(result, "event", None) is not None):
        issues.append("event not classified")
    wim = (analysis or {}).get("why_it_matters") or {}
    if not (wim.get("criticality_summary") or "").strip():
        issues.append("missing criticality_summary")
    approved = not issues
    return ApprovalResult(
        approved=approved,
        confidence=1.0 if approved else 0.0,
        issues=issues,
        reviewer="deterministic",
    )


def _build_review_prompt(
    result: Any, analysis: dict[str, Any], recommendations: Any,
) -> str:
    body = clamp_article_text(getattr(result, "article_content", ""))
    title = getattr(result, "title", "") or ""
    a = analysis or {}
    lede = ((a.get("lede") or {}).get("text") or "").strip()
    wc = (a.get("what_changed") or {}).get("headline") or ""
    wim = a.get("why_it_matters") or {}
    summary = wim.get("criticality_summary") or ""
    stakes = wim.get("stakes_for_company") or ""
    _exp = wim.get("financial_exposure") or {}
    exposure = _exp.get("label") or ""
    # Phase 50 — an engine-estimate ₹ chip is a SCENARIO model output, not a
    # claim that the article quoted the figure. Label it as such so the reviewer
    # judges the editorial PROSE (lede / what-changed / why) for grounding and
    # does NOT reject the analysis merely because this clearly-tagged estimate
    # isn't in the article body.
    _exp_src = (_exp.get("source") or "").lower()
    _exp_is_estimate = _exp_src in ("engine_estimate", "primitive_engine", "suppressed", "not_computed")

    rec_lines: list[str] = []
    recs = []
    if recommendations is not None and not isinstance(recommendations, dict):
        recs = getattr(recommendations, "recommendations", None) or []
    for r in recs[:3]:
        rec_lines.append(
            f"- {getattr(r, 'title', '')} "
            f"(peer: {getattr(r, 'peer_benchmark', '') or 'n/a'}; "
            f"framework: {getattr(r, 'framework_section', '') or 'n/a'})"
        )

    parts = [
        "SOURCE ARTICLE",
        f"Title: {title}",
        f"Body:\n{body}",
        "",
        "ENGINE ANALYSIS TO REVIEW",
        f"Lede: {lede or '(none)'}",
        f"What changed: {wc}",
        f"Why it matters: {summary}",
        f"Stakes: {stakes}",
        (
            f"Financial exposure shown: {exposure} "
            "[ENGINE SCENARIO ESTIMATE — a model projection, NOT a claim the "
            "article quoted this figure. Do NOT reject the analysis solely "
            "because this estimate is absent from the article; judge the lede / "
            "what-changed / why-it-matters PROSE for grounding instead.]"
            if (exposure and _exp_is_estimate)
            else f"Financial exposure shown: {exposure or '(none)'}"
        ),
        "Recommendations:",
        *(rec_lines or ["(none)"]),
    ]
    return "\n".join(parts)


def approve_analysis_for_display(
    *,
    result: Any,
    insight: Any,
    unified_analysis: dict[str, Any],
    recommendations: Any = None,
    tier: str = "critical",
) -> ApprovalResult:
    """Approve (or reject) a composed analysis before it reaches the deck.

    `tier="light"` → deterministic. `tier="critical"` → Opus 4.6 review.
    Fail-open on infra error.
    """
    analysis = unified_analysis or {}

    if tier == "light":
        return _approve_light(result, analysis)

    # --- Critical path: cheap pre-checks, then Opus review ---------------
    # Cheap mechanical pre-checks first (reuse existing verifiers). A tone
    # violation or empty summary is an automatic reject without spending an
    # Opus call.
    issues: list[str] = []
    wim = analysis.get("why_it_matters") or {}
    if not (wim.get("criticality_summary") or "").strip():
        issues.append("empty criticality_summary")
    lede_text = ((analysis.get("lede") or {}).get("text") or "").strip()
    if lede_text:
        try:
            from engine.analysis.tone_guardrails import scan_for_violations
            hits = [
                h for h in scan_for_violations(lede_text)
                if h.get("kind") in {"banned_phrase", "score_leak"}
            ]
            if hits:
                issues.append(f"lede tone violations: {[h.get('kind') for h in hits][:3]}")
        except Exception:  # noqa: BLE001
            pass
    if issues:
        return ApprovalResult(
            approved=False, confidence=0.0, issues=issues, reviewer="deterministic",
        )

    # Opus review
    try:
        from engine.llm import get_llm_client
        client = get_llm_client(task_class="reasoning_heavy")
        user = _build_review_prompt(result, analysis, recommendations)
        resp = client.complete(
            messages=[
                {"role": "system", "content": _APPROVAL_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=400,
        )
        verdict = _extract_json(getattr(resp, "text", "") or "")
        if verdict is None:
            # Phase 49.1 — FAIL-CLOSED for criticals. A critical article
            # carries a generated lede + recs + ₹ figures (high fabrication
            # surface). If we can't parse the reviewer's verdict we must NOT
            # default to "approved" — that let a JSW card with a fabricated
            # "₹588 Cr quarterly profit" lede through. Reject → the deck
            # builder demotes it to the light tier (which has no lede/recs/₹
            # and so nothing to fabricate). A thin-but-accurate deck beats a
            # rich-but-fabricated one — accuracy is the product's bar.
            logger.warning(
                "[approval] could not parse verdict for %s — FAIL-CLOSED (reject)",
                getattr(result, "article_id", "?"),
            )
            return ApprovalResult(
                approved=False, confidence=0.0, issues=["unparseable verdict — fail-closed"],
                reviewer="fail_closed",
            )
        approved = bool(verdict.get("approved", True))
        confidence = float(verdict.get("confidence", 0.0) or 0.0)
        v_issues = [str(x)[:160] for x in (verdict.get("issues") or [])][:5]
        if not approved:
            logger.warning(
                "[approval] REJECTED %s (conf=%.2f): %s",
                getattr(result, "article_id", "?"), confidence, v_issues,
            )
        return ApprovalResult(
            approved=approved, confidence=confidence, issues=v_issues, reviewer="opus",
        )
    except Exception as exc:  # noqa: BLE001 — never let the gate be an outage source
        # Phase 49.1 — also FAIL-CLOSED on LLM/infra error for criticals.
        logger.warning(
            "[approval] LLM gate errored for %s (%s) — FAIL-CLOSED (reject)",
            getattr(result, "article_id", "?"), type(exc).__name__,
        )
        return ApprovalResult(
            approved=False, confidence=0.0, issues=[f"gate error: {type(exc).__name__} — fail-closed"],
            reviewer="fail_closed",
        )
