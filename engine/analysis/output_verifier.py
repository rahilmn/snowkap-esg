"""Phase 3 output verifier — the "defensibility" guard.

Runs after the Stage 10 LLM call and corrects / flags the five most common
failure modes a CFO would catch in 30 seconds:

  1. **Margin math** — verifies (event_cr / revenue_cr) × 10_000 ≈ margin_bps
     within ±5%. If off, corrects and flags `computed_override: true`.
  2. **Source tags** — every ₹ figure must carry `(from article)` or
     `(engine estimate)`. Missing tags are auto-appended using a heuristic
     (article text mention = `from article`; primitive cascade = `engine estimate`).
  3. **CFO headline hygiene** — no Greek letters, no framework IDs, ≤ 100 words.
     Offending content is stripped or truncated.
  4. **Framework citations** — every code must have a rationale string
     (pulled from ontology `hasRationale` triples when missing).
  5. **ROI cap disclosure** — if `roi_percentage` was clamped, a flag
     `roi_capped` + `roi_cap_reason` is set so the UI can render a tooltip.

Each correction is logged with structured fields so an audit trail is
available per article.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — enforcement rules
# ---------------------------------------------------------------------------

_GREEK_RE = re.compile(r"[α-ωΑ-Ω]")
_FRAMEWORK_ID_RE = re.compile(
    r"\b(?:BRSR|GRI|ESRS|TCFD|CSRD|CDP|ISSB|SBTi|TNFD|SEC\s*Climate|SASB|DJSI|COSO|SFDR|MSCI|Sustainalytics)\s*[:]?\s*[A-Z0-9\-]+\b"
)
_RUPEE_FIGURE_RE = re.compile(
    r"(?:₹|Rs\.?|INR)\s*\d+(?:,\d+)*(?:\.\d+)?\s*(?:Cr|crore|Lakh|Lkh|L)?",
    re.IGNORECASE,
)
_SOURCE_TAG_RE = re.compile(r"\((?:from\s+article|engine\s+estimate)\)", re.IGNORECASE)

CFO_MAX_WORDS = 100
MARGIN_TOLERANCE = 0.05  # ±5%
BPS_PER_FRACTION = 10_000


@dataclass
class VerifierReport:
    """Per-article audit trail of what the verifier changed."""
    corrections: list[str]  # list of human-readable fixes applied
    warnings: list[str]     # non-blocking issues noted
    math_ok: bool
    margin_bps_original: float | None
    margin_bps_corrected: float | None
    source_tags_added: int
    framework_rationales_added: int
    roi_caps_disclosed: int
    headline_truncated: bool


# ---------------------------------------------------------------------------
# 1. Margin math reconciliation
# ---------------------------------------------------------------------------


def _extract_cr_amount(text: str) -> float | None:
    """Pull the first ₹X Cr figure out of a string. Returns None if not found."""
    if not text:
        return None
    m = re.search(
        r"(?:₹|Rs\.?|INR)\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*(Cr|crore|Lakh|Lkh|L)?",
        text, re.IGNORECASE,
    )
    if not m:
        # Try bare number + Cr suffix
        m = re.search(r"(\d+(?:,\d+)*(?:\.\d+)?)\s*(Cr|crore)", text, re.IGNORECASE)
        if not m:
            return None
    try:
        amt = float(m.group(1).replace(",", ""))
        unit = (m.group(2) or "").lower() if m.lastindex and m.lastindex >= 2 else ""
        if unit.startswith("l"):
            amt = amt / 100  # 1 Cr = 100 Lakh
        return amt
    except (ValueError, TypeError):
        return None


def _extract_bps_value(text: str) -> float | None:
    """Pull the first bps figure out of a string."""
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*bps\b", text, re.IGNORECASE)
    return float(m.group(1)) if m else None


def verify_margin_math(
    deep_insight: dict,
    revenue_cr: float,
) -> tuple[dict, VerifierReport]:
    """Check that `margin_pressure` bps figures reconcile with cited ₹ amounts.

    If a ₹X Cr figure on revenue ₹Y Cr should equal X/Y × 10_000 bps but the
    LLM wrote something different, correct in place and flag `computed_override`.

    Returns (updated_dict, report). The input dict is not mutated.
    """
    import copy
    out = copy.deepcopy(deep_insight)
    corrections: list[str] = []
    warnings: list[str] = []
    original_bps = None
    corrected_bps = None
    math_ok = True

    if revenue_cr <= 0:
        warnings.append("revenue_cr is 0 or negative — skipping margin math check")
        return out, VerifierReport(
            corrections=[], warnings=warnings, math_ok=True,
            margin_bps_original=None, margin_bps_corrected=None,
            source_tags_added=0, framework_rationales_added=0,
            roi_caps_disclosed=0, headline_truncated=False,
        )

    # Scopes to check: each scope = a set of related fields we pair together.
    # ₹ amount may live in one field (e.g., headline) and bps in another
    # (e.g., margin_pressure); the check pairs them.
    scopes: list[tuple[str, list[tuple[str, ...]]]] = [
        (
            "financial_timeline.immediate",
            [
                ("financial_timeline", "immediate", "headline"),
                ("financial_timeline", "immediate", "margin_pressure"),
                ("financial_timeline", "immediate", "revenue_at_risk"),
            ],
        ),
        (
            "impact_analysis",
            [
                ("impact_analysis", "margin_pressure"),
                ("impact_analysis", "financial"),
            ],
        ),
    ]

    def _get(path: tuple[str, ...]) -> tuple[dict | None, str, str | None]:
        """Return (parent_dict, leaf_key, text) or (None, '', None) if missing."""
        node = out
        for key in path[:-1]:
            node = node.get(key, {}) if isinstance(node, dict) else {}
        leaf_key = path[-1]
        if not isinstance(node, dict):
            return None, leaf_key, None
        text = node.get(leaf_key)
        if not isinstance(text, str):
            return node, leaf_key, None
        return node, leaf_key, text

    for scope_label, paths in scopes:
        # First pass: find ANY ₹ amount and ANY bps in the scope
        scope_cr: float | None = None
        scope_bps: float | None = None
        bps_path: tuple[str, ...] | None = None

        for path in paths:
            _, _, text = _get(path)
            if not text:
                continue
            if scope_cr is None:
                c = _extract_cr_amount(text)
                if c is not None:
                    scope_cr = c
            if scope_bps is None:
                b = _extract_bps_value(text)
                if b is not None:
                    scope_bps = b
                    bps_path = path

        if scope_cr is None or scope_bps is None or bps_path is None:
            continue

        expected_bps = (scope_cr / revenue_cr) * BPS_PER_FRACTION
        if expected_bps <= 0:
            continue

        if original_bps is None:
            original_bps = scope_bps
        deviation = abs(scope_bps - expected_bps) / expected_bps
        if deviation > MARGIN_TOLERANCE:
            parent, leaf_key, text = _get(bps_path)
            if parent is not None and text:
                new_text = re.sub(
                    r"\d+(?:\.\d+)?\s*bps",
                    f"{expected_bps:.1f} bps (computed_override)",
                    text,
                    count=1,
                )
                parent[leaf_key] = new_text
            corrected_bps = expected_bps
            corrections.append(
                f"margin math at {scope_label}: cited {scope_bps:.1f} bps, "
                f"computed {expected_bps:.1f} bps (dev {deviation:.1%})"
            )
            math_ok = False
            logger.warning(
                "output_verifier: margin math off at %s — ₹%.1f Cr / ₹%.0f Cr rev = %.1f bps, cited %.1f bps",
                scope_label, scope_cr, revenue_cr, expected_bps, scope_bps,
            )

    return out, VerifierReport(
        corrections=corrections,
        warnings=warnings,
        math_ok=math_ok,
        margin_bps_original=original_bps,
        margin_bps_corrected=corrected_bps,
        source_tags_added=0,
        framework_rationales_added=0,
        roi_caps_disclosed=0,
        headline_truncated=False,
    )


# ---------------------------------------------------------------------------
# 2. Source tag enforcement
# ---------------------------------------------------------------------------


def _has_source_tag(text: str) -> bool:
    return bool(_SOURCE_TAG_RE.search(text or ""))


def _infer_source_tag(
    text: str,
    article_excerpts: list[str] | None = None,
) -> str:
    """Guess whether a ₹ figure came from the article or is an engine estimate.

    Conservative heuristic: treat as "from article" only when the article
    text contains the same ₹ amount in ₹/Rs/crore/lakh context (not just
    a loose substring match, which false-matches on e.g. '45,000 crore'
    containing '450'). Default is (engine estimate).
    """
    cr_value = _extract_cr_amount(text)
    if cr_value is None or not article_excerpts:
        return "(engine estimate)"

    # Build ₹-contextual patterns for the value and its ±10% window.
    # Note: we search AGAINST a comma-stripped copy of each excerpt, so
    # "Rs 45,000 crore" and "Rs 45000 crore" both match `\b45000\b`.
    def _rupee_pattern(v: float) -> re.Pattern:
        v_int = int(round(v))
        return re.compile(
            rf"(?:₹|Rs\.?|INR)\s*{v_int}(?:\.\d+)?\s*(?:Cr|crore|Lakh|Lkh|L)?\b"
            rf"|\b{v_int}(?:\.\d+)?\s*(?:Cr|crore)\b",
            re.IGNORECASE,
        )

    patterns = [_rupee_pattern(cr_value)]
    patterns.append(_rupee_pattern(cr_value * 0.9))
    patterns.append(_rupee_pattern(cr_value * 1.1))

    for excerpt in article_excerpts:
        if not excerpt:
            continue
        # Strip comma thousands-separators so "45,000" normalises to "45000"
        normalised = re.sub(r"(\d),(\d)", r"\1\2", excerpt)
        for pat in patterns:
            if pat.search(normalised):
                return "(from article)"
    return "(engine estimate)"


def verify_cross_section_consistency(
    deep_insight: dict,
    tolerance_pct: float = 0.35,
) -> tuple[float | None, list[str]]:
    """Phase 12.5 — canonical exposure check.

    Scans every ₹ figure across `headline`, `decision_summary.financial_exposure`,
    `decision_summary.key_risk`, `core_mechanism`, and `net_impact_summary`.
    Returns the largest (canonical) value + a list of warnings for any figure
    that diverges from the canonical by more than `tolerance_pct`.

    Prior observation (Waaree contract-win rerun, Phase 12.4): different
    sections quoted ₹477.5 Cr (headline), ₹14.4 Cr (ESG Analyst) and
    ₹33.4 Cr (margin impact) — all for the same event. Not yet a hallucination
    but an internal inconsistency that undermines credibility. This checker
    surfaces the drift so the caller can log / re-prompt / downgrade confidence.
    """
    warnings: list[str] = []

    fields_to_scan = [
        ("headline", deep_insight.get("headline") or ""),
        ("exposure", (deep_insight.get("decision_summary") or {}).get("financial_exposure") or ""),
        ("key_risk", (deep_insight.get("decision_summary") or {}).get("key_risk") or ""),
        ("top_opportunity", (deep_insight.get("decision_summary") or {}).get("top_opportunity") or ""),
        ("net_impact", deep_insight.get("net_impact_summary") or ""),
    ]

    # Extract all ₹ figures per field (may be multiple — take the largest)
    per_field: dict[str, float] = {}
    for field_name, text in fields_to_scan:
        figures = _extract_all_cr_amounts(text)
        if figures:
            per_field[field_name] = max(figures)

    if len(per_field) < 2:
        return (None if not per_field else next(iter(per_field.values())), warnings)

    # Canonical = largest value across all fields (usually the headline's
    # total exposure). Perspective-specific drift is measured against it.
    canonical = max(per_field.values())

    for field_name, value in per_field.items():
        if canonical <= 0:
            continue
        deviation = abs(value - canonical) / canonical
        if deviation > tolerance_pct:
            warnings.append(
                f"cross-section ₹ drift: {field_name}=₹{value:.1f} Cr "
                f"vs canonical ₹{canonical:.1f} Cr ({deviation:.0%} deviation)"
            )
    return canonical, warnings


def _extract_all_cr_amounts(text: str) -> list[float]:
    """Extract every ₹X Cr figure from a string. Returns a list (possibly
    empty). Used by the cross-section consistency check."""
    if not text:
        return []
    values: list[float] = []
    pattern = re.compile(
        r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d+)?)\s*(?:Cr|crore)",
        re.IGNORECASE,
    )
    for m in pattern.finditer(text):
        raw = m.group(1).replace(",", "")
        try:
            values.append(float(raw))
        except ValueError:
            continue
    # Also catch bare "X Cr" without ₹ symbol (LLM sometimes drops it)
    bare = re.compile(r"\b([\d,]+(?:\.\d+)?)\s*(?:Cr|crore)\b", re.IGNORECASE)
    for m in bare.finditer(text):
        raw = m.group(1).replace(",", "")
        try:
            v = float(raw)
            # De-dup: skip if already captured by the ₹-prefixed pass
            if not any(abs(v - existing) < 0.01 for existing in values):
                values.append(v)
        except ValueError:
            continue
    return values


# ---------------------------------------------------------------------------
# Phase 18 — Semantic ₹ drift detector + reused-number hallucination audit
# ---------------------------------------------------------------------------
#
# The Phase 12.5 numerical drift check catches cases where one section says
# ₹477.5 Cr and another says ₹14.4 Cr for the same event. But it MISSES the
# inverse failure mode: the LLM citing the SAME ₹ value in unrelated
# contexts.
#
# Live-fail (IDFC First Bank Q4 calendar, 2026-04-24):
#   "₹500 Cr market-cap loss" + "₹500 Cr green bond" + "₹500 Cr P/E
#   compression" — all in one insight. Same number, three completely
#   different concepts. The article only contained ONE ₹500-ish figure
#   (₹503 Cr Q3 net profit). This is a semantic-drift hallucination —
#   the LLM is recycling one anchor number for many distinct claims.
#
# Detector strategy:
#   1. Scan every ₹ figure with a ~6-word context window before + after
#   2. Group figures whose values are within ±5% of each other ("same value")
#   3. For each group, compute the Jaccard overlap of the noun-phrase
#      tokens in their context windows
#   4. If overlap < 0.20 across 3+ occurrences → semantic-drift warning
#
# Stopwords are intentionally aggressive — we want the noun phrases to
# carry the SEMANTIC LOAD (margin / penalty / revenue / contract etc.),
# not the connective tissue.

_SEMANTIC_STOPWORDS = frozenset({
    # Articles, prepositions, conjunctions
    "a", "an", "the", "and", "or", "but", "if", "of", "in", "on", "at",
    "for", "to", "from", "with", "by", "as", "into", "onto", "upon",
    # Common verbs with little semantic load
    "is", "are", "was", "were", "be", "been", "being", "has", "have", "had",
    "do", "does", "did", "will", "would", "could", "should", "may", "might",
    "can", "must", "shall",
    # Pronouns
    "this", "that", "these", "those", "it", "its", "their", "they", "we", "our",
    "his", "her", "him", "she", "he",
    # Numbers/quantifiers + ₹/Cr noise
    "cr", "crore", "rs", "inr", "₹", "lakh", "cr.", "rs.", "approx", "approximately",
    "approx.", "estimate", "engine", "article", "from",
    # Polarity / generic-claim words
    "very", "more", "less", "much", "many", "some", "any", "every",
    "all", "no", "not", "non",
    # Connectors LLM uses heavily
    "due", "such", "which", "that", "thus", "hence", "therefore", "however",
})


def _context_tokens(text: str, span_start: int, span_end: int, window: int = 6) -> set[str]:
    """Return the set of meaningful tokens (lowercased, stopwords removed)
    in a `window`-word window before + after the matched span."""
    if not text:
        return set()
    pre = text[max(0, span_start - 200): span_start]
    post = text[span_end: span_end + 200]
    pre_tokens = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", pre)[-window:]
    post_tokens = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", post)[:window]
    tokens = {t.lower() for t in (pre_tokens + post_tokens)}
    return tokens - _SEMANTIC_STOPWORDS


def _scan_cr_figures_with_context(
    deep_insight: dict,
) -> list[tuple[str, float, set[str], str]]:
    """Walk all ₹ figures across the deep_insight and return a list of
    (field_name, value, context_tokens, surrounding_text) tuples. Used by
    `verify_semantic_consistency`."""
    fields = [
        ("headline", deep_insight.get("headline") or ""),
        ("exposure", (deep_insight.get("decision_summary") or {}).get("financial_exposure") or ""),
        ("key_risk", (deep_insight.get("decision_summary") or {}).get("key_risk") or ""),
        ("top_opportunity", (deep_insight.get("decision_summary") or {}).get("top_opportunity") or ""),
        ("core_mechanism", deep_insight.get("core_mechanism") or ""),
        ("net_impact", deep_insight.get("net_impact_summary") or ""),
    ]
    pattern = re.compile(
        r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d+)?)\s*(?:Cr|crore)",
        re.IGNORECASE,
    )

    found: list[tuple[str, float, set[str], str]] = []
    for field_name, text in fields:
        if not text:
            continue
        for m in pattern.finditer(text):
            raw = m.group(1).replace(",", "")
            try:
                value = float(raw)
            except ValueError:
                continue
            ctx = _context_tokens(text, m.start(), m.end(), window=6)
            # Surrounding text for diagnostics (used in warning messages)
            surrounding = text[max(0, m.start() - 40): m.end() + 40].strip()
            found.append((field_name, value, ctx, surrounding))
    return found


def verify_semantic_consistency(
    deep_insight: dict,
    value_tolerance: float = 0.05,
    overlap_threshold: float = 0.20,
    min_reuses: int = 3,
) -> list[str]:
    """Phase 18 — semantic ₹ drift detector.

    Catches the "same number / different concepts" hallucination pattern
    (live-fail: IDFC Q4 calendar — ₹500 Cr cited as market-cap loss,
    green-bond size, AND P/E compression in one insight). Flags any
    cluster of ≥ `min_reuses` ₹ figures whose values agree within
    `value_tolerance` but whose context noun-phrase tokens overlap below
    `overlap_threshold` (Jaccard).

    Returns a list of warning strings — empty if all reuses share enough
    semantic context to be plausibly the same claim.
    """
    figures = _scan_cr_figures_with_context(deep_insight)
    if len(figures) < min_reuses:
        return []

    # Group by value (within tolerance — handles ₹500 vs ₹503 from
    # comma-stripping or rounding). Use a simple greedy bucket.
    groups: list[list[int]] = []
    for idx, (_, value, _, _) in enumerate(figures):
        if value <= 0:
            continue
        placed = False
        for g in groups:
            anchor = figures[g[0]][1]
            if abs(value - anchor) / max(anchor, 1.0) <= value_tolerance:
                g.append(idx)
                placed = True
                break
        if not placed:
            groups.append([idx])

    warnings: list[str] = []
    for g in groups:
        if len(g) < min_reuses:
            continue
        # Compute pairwise Jaccard overlap. Drift = the contexts don't
        # share at least `overlap_threshold` of their tokens.
        ctx_sets = [figures[i][2] for i in g]
        # Skip groups where every context is empty (no signal to compare)
        if all(len(s) == 0 for s in ctx_sets):
            continue
        # Average pairwise Jaccard
        n = len(ctx_sets)
        pair_count = 0
        overlap_sum = 0.0
        for i in range(n):
            for j in range(i + 1, n):
                a, b = ctx_sets[i], ctx_sets[j]
                if not a and not b:
                    continue
                union = a | b
                if not union:
                    continue
                overlap_sum += len(a & b) / len(union)
                pair_count += 1
        if pair_count == 0:
            continue
        avg_overlap = overlap_sum / pair_count
        if avg_overlap < overlap_threshold:
            anchor_value = figures[g[0]][1]
            fields_seen = ", ".join(figures[i][0] for i in g)
            warnings.append(
                f"semantic ₹ drift: ₹{anchor_value:.1f} Cr re-used in {len(g)} "
                f"different contexts (fields: {fields_seen}; avg context overlap "
                f"{avg_overlap:.0%}). LLM is likely recycling a single anchor "
                f"number for distinct claims."
            )
    return warnings


def audit_reused_article_figures(
    deep_insight: dict,
    article_excerpts: list[str] | None = None,
    max_distinct_uses: int = 2,
) -> tuple[dict, int]:
    """Phase 18 — reused-number hallucination audit.

    `audit_source_tags` (Phase 12.7) checks if a `(from article)` figure
    EXISTS in the article body. But it doesn't notice when the SAME
    article figure is paired with multiple unrelated claims. Live-fail
    (IDFC Q4 calendar): the article had exactly one ₹503 Cr figure (Q3
    net profit), but the LLM tagged THREE different claims as
    "(from article)" each citing ₹500 Cr — only one of them could
    plausibly be the true source claim.

    Strategy: for each `(from article)`-tagged ₹ figure, cluster by
    value AND context tokens. When >max_distinct_uses claims share the
    same value but have non-overlapping contexts, downgrade the extras
    to `(engine estimate)` since at most one can be the genuine
    article-sourced claim.

    Returns (updated_dict, count_of_tags_downgraded).
    """
    import copy
    out = copy.deepcopy(deep_insight)
    if article_excerpts is None:
        article_excerpts = []

    # Step 1 — collect every (from article)-tagged ₹ figure with location.
    locations: list[dict[str, Any]] = []
    pattern = re.compile(
        r"((?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d+)?)\s*(?:Cr|crore))"
        r"([^(]{0,120})\(from article\)",
        re.IGNORECASE,
    )

    def _walk_collect(node: Any, path: tuple) -> None:
        if isinstance(node, str):
            for m in pattern.finditer(node):
                raw = m.group(2).replace(",", "")
                try:
                    value = float(raw)
                except ValueError:
                    continue
                # Wide context: 6 words before + everything between the
                # figure and the closing "(from article)" tag + 6 words
                # after. This catches the descriptor clause like "₹503 Cr
                # regulatory penalty (from article)" → tokens include
                # "regulatory" + "penalty" so we can compare across
                # claims that recycle the same number for distinct concepts.
                ctx_before = _context_tokens(node, m.start(), m.start(), window=6)
                # The 'middle' clause between figure and tag carries the
                # claim's noun phrase (e.g. "regulatory provision at risk").
                middle_tokens = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", m.group(3) or "")
                ctx_middle = {t.lower() for t in middle_tokens} - _SEMANTIC_STOPWORDS
                ctx_after = _context_tokens(node, m.end(), m.end(), window=6)
                ctx = ctx_before | ctx_middle | ctx_after
                locations.append({
                    "path": path,
                    "value": value,
                    "ctx": ctx,
                    "text_match": m.group(0),
                    "node": node,
                })
            return
        if isinstance(node, dict):
            for k, v in node.items():
                _walk_collect(v, path + (k,))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk_collect(v, path + (i,))

    _walk_collect(out, ())

    if not locations:
        return out, 0

    # Step 2 — group by value (±5% tolerance, mirrors semantic drift)
    groups: list[list[int]] = []
    for idx, loc in enumerate(locations):
        v = loc["value"]
        placed = False
        for g in groups:
            anchor = locations[g[0]]["value"]
            if abs(v - anchor) / max(anchor, 1.0) <= 0.05:
                g.append(idx)
                placed = True
                break
        if not placed:
            groups.append([idx])

    # Step 3 — within each oversized group (>max_distinct_uses), downgrade
    # all but the FIRST occurrence (chronological order matches the LLM's
    # writing order — the first occurrence is most likely the true claim).
    to_downgrade: list[int] = []
    for g in groups:
        if len(g) <= max_distinct_uses:
            continue
        ctx_sets = [locations[i]["ctx"] for i in g]
        # Edge case: every context is empty (LLM emitted bare "₹X Cr (from
        # article)" everywhere). Treat as repetition, not as distinct
        # hallucination — leave alone. We have no evidence either way.
        if all(len(s) == 0 for s in ctx_sets):
            continue
        # Pairwise Jaccard overlap across the group. Skip pairs where
        # both sides are empty (no signal to compare).
        n = len(ctx_sets)
        avg_overlap = 0.0
        pair_count = 0
        for i in range(n):
            for j in range(i + 1, n):
                a, b = ctx_sets[i], ctx_sets[j]
                if not a and not b:
                    continue
                union = a | b
                if not union:
                    continue
                avg_overlap += len(a & b) / len(union)
                pair_count += 1
        if pair_count > 0 and (avg_overlap / pair_count) > 0.40:
            # High overlap — same claim repeated, leave as-is.
            continue
        # Distinct claims sharing one number → downgrade everything except
        # the first occurrence (chronological order ≈ LLM writing order).
        # The first occurrence is most likely the genuine source claim.
        to_downgrade.extend(g[1:])

    if not to_downgrade:
        return out, 0

    # Step 4 — apply downgrades by walking the tree again and rewriting
    # the matching string slices.
    downgrade_set = {locations[i]["text_match"] for i in to_downgrade}

    def _walk_rewrite(node: Any) -> Any:
        if isinstance(node, str):
            new = node
            for old_text in downgrade_set:
                if old_text in new:
                    # Replace ONLY this occurrence's `(from article)` tag.
                    # The text_match captures the figure + middle + tag, so
                    # swap "(from article)" for "(engine estimate)" inside.
                    replaced = old_text.replace(
                        "(from article)", "(engine estimate)"
                    )
                    new = new.replace(old_text, replaced, 1)
            return new
        if isinstance(node, dict):
            return {k: _walk_rewrite(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk_rewrite(x) for x in node]
        return node

    out = _walk_rewrite(out)
    return out, len(to_downgrade)


def audit_source_tags(
    deep_insight: dict,
    article_excerpts: list[str] | None = None,
) -> tuple[dict, int]:
    """Phase 12.7 — independently audit every `(from article)` tag the LLM
    emitted and downgrade to `(engine estimate)` when the article doesn't
    actually support the figure.

    Observed failure mode (Waaree anti-dumping article, 2026-04-24): the
    article body contained **zero** ₹ figures, yet the LLM produced
    "₹353.6 Cr direct revenue hit (from article)" — a hallucinated
    attribution. The existing `enforce_source_tags` skips claims that
    already carry a tag; this auditor catches exactly that case.

    Returns (updated_dict, count_of_tags_downgraded).
    """
    import copy
    out = copy.deepcopy(deep_insight)
    downgraded = 0

    # Regex catches "₹<number> Cr" followed by ≤ 120 chars of descriptive
    # prose, then "(from article)". Needed because the LLM typically writes
    # something like "₹353.6 Cr direct revenue hit (from article)" with a
    # descriptor clause between the figure and the claim.
    claim_re = re.compile(
        r"((?:₹|Rs\.?|INR)\s*[\d,]+(?:\.\d+)?\s*(?:Cr|crore|Lakh|Lkh|L|bn|billion)?)"
        r"([^(]{0,120})\(from article\)",
        re.IGNORECASE,
    )

    def _walk(node: Any) -> Any:
        nonlocal downgraded
        if isinstance(node, str):
            if "(from article)" not in node.lower():
                return node

            def _maybe_downgrade(m):
                nonlocal downgraded
                figure_text = m.group(1)
                middle = m.group(2)
                cr_value = _extract_cr_amount(figure_text)
                if cr_value is None:
                    return m.group(0)
                inferred = _infer_source_tag(figure_text, article_excerpts)
                if inferred == "(from article)":
                    return m.group(0)  # claim is justified — leave it
                downgraded += 1
                return f"{figure_text}{middle}(engine estimate)"

            return claim_re.sub(_maybe_downgrade, node)

        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(x) for x in node]
        return node

    out = _walk(out)
    return out, downgraded


def enforce_source_tags(
    deep_insight: dict,
    article_excerpts: list[str] | None = None,
) -> tuple[dict, int]:
    """Add `(from article)` or `(engine estimate)` next to every ₹ figure.

    Returns (updated_dict, count_of_tags_added).
    """
    import copy
    out = copy.deepcopy(deep_insight)
    added = 0

    def _walk(node: Any) -> Any:
        nonlocal added
        if isinstance(node, str):
            if not _RUPEE_FIGURE_RE.search(node) or _has_source_tag(node):
                return node
            tag = _infer_source_tag(node, article_excerpts)
            # Append tag after the first ₹ figure occurrence
            new_node = _RUPEE_FIGURE_RE.sub(
                lambda m: f"{m.group(0)} {tag}" if not _SOURCE_TAG_RE.search(node[m.end():m.end()+25]) else m.group(0),
                node,
                count=1,
            )
            if new_node != node:
                added += 1
            return new_node
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(x) for x in node]
        return node

    out = _walk(out)
    return out, added


# ---------------------------------------------------------------------------
# 3. CFO headline hygiene
# ---------------------------------------------------------------------------


def sanitise_cfo_headline(headline: str) -> tuple[str, bool]:
    """Strip Greek letters + framework IDs, enforce word cap. Returns (clean, was_modified)."""
    if not headline:
        return "", False
    original = headline
    # Strip Greek letters — rare but seen in LLM output
    cleaned = _GREEK_RE.sub("", headline)
    # Strip framework IDs like "BRSR:P6" or "GRI:303-3"
    cleaned = _FRAMEWORK_ID_RE.sub("", cleaned)
    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Enforce word cap
    words = cleaned.split()
    truncated = False
    if len(words) > CFO_MAX_WORDS:
        cleaned = " ".join(words[:CFO_MAX_WORDS]) + "…"
        truncated = True
    return cleaned, (cleaned != original)


# ---------------------------------------------------------------------------
# 4. Framework rationale injection
# ---------------------------------------------------------------------------


def inject_framework_rationales(
    deep_insight: dict,
    rationale_lookup: dict[str, str] | None = None,
) -> tuple[dict, int]:
    """For each framework code cited, ensure a rationale follows.

    `rationale_lookup`: {section_code: rationale_text} from the ontology.
    If a rationale exists and the output doesn't already carry one, annotate.

    Returns (updated_dict, count_of_rationales_added).
    """
    if not rationale_lookup:
        return deep_insight, 0

    import copy
    out = copy.deepcopy(deep_insight)
    added = 0

    def _walk(node: Any) -> Any:
        nonlocal added
        if isinstance(node, str):
            for code, rationale in rationale_lookup.items():
                if code in node and rationale and rationale not in node:
                    # Append inline: "GRI:303-3" → "GRI:303-3 (rationale: <text>)"
                    idx = node.find(code)
                    # Only annotate first occurrence to avoid spam
                    after = node[idx + len(code):idx + len(code) + 12]
                    if "(rationale" not in after:
                        node = node.replace(
                            code, f"{code} (rationale: {rationale})", 1
                        )
                        added += 1
            return node
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(x) for x in node]
        return node

    out = _walk(out)
    return out, added


# ---------------------------------------------------------------------------
# 5. ROI cap disclosure
# ---------------------------------------------------------------------------


ROI_CAPS = {
    "compliance": 500.0,
    "financial": 300.0,
    "strategic": 400.0,
    "esg_positioning": 400.0,
    "operational": 200.0,
}


def flag_roi_cap(recommendation: dict) -> dict:
    """Add `roi_capped: true` + `roi_cap_reason` if the recommendation's ROI
    hits the cap for its type. The clamp is already applied by the recommendation
    engine; this function surfaces that fact for the UI.
    """
    rec_type = str(recommendation.get("type", "")).lower()
    roi = recommendation.get("roi_percentage")
    if roi is None:
        return recommendation

    cap = ROI_CAPS.get(rec_type)
    if cap is None:
        return recommendation

    if abs(roi - cap) < 0.01:
        recommendation = dict(recommendation)
        recommendation["roi_capped"] = True
        recommendation["roi_cap_reason"] = (
            f"Capped at {cap:.0f}% ({rec_type} ceiling). "
            f"Actual avoided cost may exceed this; cap prevents over-claim."
        )
    return recommendation


def flag_roi_caps_bulk(recommendations: list[dict]) -> tuple[list[dict], int]:
    """Run flag_roi_cap across a list. Returns (updated_list, caps_disclosed)."""
    out = []
    count = 0
    for rec in recommendations:
        new_rec = flag_roi_cap(rec)
        if new_rec.get("roi_capped"):
            count += 1
        out.append(new_rec)
    return out, count


# ---------------------------------------------------------------------------
# Top-level verifier entry point
# ---------------------------------------------------------------------------


# Phase 12.4 — Narrative-data coherence check.
#
# Problem observed on the Waaree PSPCL solar-auction article (2026-04-24):
# The event was a positive contract win, but the LLM produced a crisis
# narrative with ₹807 Cr "regulatory exposure" — fabricated because the
# primitive cascade had been misdirected by a bad event classification.
#
# The earlier verifier catches math drift (bps vs revenue) but didn't check
# whether the narrative polarity matches the article polarity. This pass
# does that coherence check, scoring:
#   - nlp_sentiment (-2..+2, from NLP extraction)
#   - event_sign (+1 for contract/certification/capacity events, -1 for
#     penalty/violation/disruption, 0 for neutral/routine)
#   - insight_polarity (+1 when materiality is LOW/MODERATE AND top_opportunity
#     is non-empty; -1 when materiality is HIGH/CRITICAL AND key_risk is
#     heavy; 0 otherwise)
#
# If the three signals don't align within tolerance, we emit a coherence
# warning and downgrade materiality by one tier. The goal is NOT to silently
# flip the narrative (that would hide bugs upstream) — it's to flag the
# incoherence so the engineer sees it and root-causes the misclassification.
_POSITIVE_EVENTS = {
    "event_contract_win", "event_capacity_addition", "event_esg_certification",
    "event_order_book_update", "event_green_finance_milestone",
    "event_transition_announcement", "event_esg_partnership",
    "event_ma_deal", "event_dividend_policy", "event_award_recognition",
}
_NEGATIVE_EVENTS = {
    "event_social_violation", "event_supply_chain_disruption",
    "event_labour_strike", "event_cyber_incident", "event_community_protest",
    "event_ngo_report", "event_license_revocation", "event_board_change",
}


def verify_low_confidence_classification(
    deep_insight: dict,
    event_matched_keywords: list[str] | None,
    nlp_sentiment: int | None,
    has_financial_quantum: bool,
) -> tuple[dict, list[str]]:
    """Phase 13 S4 — surface low-confidence classification signals.

    When the upstream pipeline has weak signal (e.g. event matched on a
    theme-fallback or single keyword + neutral sentiment + no financial
    quantum in the article), the LLM has insufficient grounding and is
    likely to over-confidently produce CRITICAL/HIGH materiality output.

    This pass detects that combination, downgrades materiality by one
    tier, and adds a `low_confidence_classification: true` flag to the
    insight so the UI/email can render a yellow "low-confidence" badge
    instead of treating the output as ground truth.

    Returns (updated_insight, warnings_list).
    """
    warnings: list[str] = []
    out = dict(deep_insight)

    keywords = list(event_matched_keywords or [])
    is_theme_fallback = keywords == ["[theme_fallback]"]
    is_single_weak_match = len(keywords) <= 1 and not is_theme_fallback
    is_neutral_sentiment = (
        isinstance(nlp_sentiment, (int, float)) and -1 < nlp_sentiment < 1
    )

    # Trigger conditions (any combination of):
    triggers: list[str] = []
    if is_theme_fallback:
        triggers.append("event matched only via theme fallback")
    if is_single_weak_match and is_neutral_sentiment and not has_financial_quantum:
        triggers.append("single weak keyword + neutral sentiment + no ₹ in article")

    if not triggers:
        return out, warnings

    # Mark as low-confidence + downgrade materiality by one step
    decision = dict(out.get("decision_summary") or {})
    materiality = (decision.get("materiality") or "").upper()
    _downgrade = {
        "CRITICAL": "HIGH",
        "HIGH": "MODERATE",
        "MODERATE": "LOW",
        "LOW": "LOW",
        "NON-MATERIAL": "NON-MATERIAL",
    }
    new_mat = _downgrade.get(materiality, materiality)
    if new_mat and new_mat != materiality:
        decision["materiality"] = new_mat
        out["decision_summary"] = decision
        warnings.append(
            f"low-confidence classification — review before sending "
            f"({'; '.join(triggers)}); materiality {materiality} → {new_mat}"
        )
    else:
        warnings.append(
            f"low-confidence classification — review before sending "
            f"({'; '.join(triggers)})"
        )

    # Flag stays even if materiality wasn't downgraded — UI uses it for
    # the yellow "low-confidence" badge regardless of materiality bucket.
    out["low_confidence_classification"] = True
    return out, warnings


def verify_narrative_coherence(
    deep_insight: dict,
    event_id: str,
    nlp_sentiment: int | None,
) -> tuple[dict, VerifierReport]:
    """Check that article polarity ↔ event polarity ↔ insight polarity agree.

    Returns (maybe-adjusted-insight, report). Emits a warning via the report
    if the signals diverge, and downgrades materiality by one step
    (CRITICAL→HIGH, HIGH→MODERATE, etc.) to avoid shipping confidently wrong
    alarmist output. Does NOT rewrite the narrative — that would mask bugs.
    """
    report = VerifierReport(
        corrections=[],
        warnings=[],
        math_ok=True,
        margin_bps_original=None,
        margin_bps_corrected=None,
        source_tags_added=0,
        framework_rationales_added=0,
        roi_caps_disclosed=0,
        headline_truncated=False,
    )
    out = dict(deep_insight)

    event_sign = 0
    if event_id in _POSITIVE_EVENTS:
        event_sign = +1
    elif event_id in _NEGATIVE_EVENTS:
        event_sign = -1
    else:
        # Phase 17 — ambiguous events (quarterly_results, dividend_policy,
        # ma_deal, esg_rating_change, climate_disclosure_index) inherit
        # polarity from sentiment so the coherence check still fires.
        try:
            from engine.analysis.recommendation_archetypes import is_ambiguous_event
            if is_ambiguous_event(event_id) and isinstance(nlp_sentiment, (int, float)):
                if nlp_sentiment >= 1:
                    event_sign = +1
                elif nlp_sentiment <= -1:
                    event_sign = -1
        except Exception:
            pass

    # Read the insight's decision_summary polarity
    decision = out.get("decision_summary") or {}
    materiality = (decision.get("materiality") or "").upper()
    key_risk = decision.get("key_risk") or ""
    top_opportunity = decision.get("top_opportunity") or ""

    insight_polarity = 0
    if materiality in {"CRITICAL", "HIGH"} and key_risk and len(key_risk) > 20:
        insight_polarity = -1
    elif materiality in {"LOW", "MODERATE", "NON-MATERIAL"} and top_opportunity:
        insight_polarity = +1

    # Sentiment polarity from NLP (scale -2..+2)
    sent_sign = 0
    if isinstance(nlp_sentiment, (int, float)):
        if nlp_sentiment >= 1:
            sent_sign = +1
        elif nlp_sentiment <= -1:
            sent_sign = -1

    # The critical hallucination pattern: POSITIVE event + NEGATIVE insight.
    # (The reverse — negative event framed as positive — is a real concern
    # too, but less common from the current LLM.)
    is_hallucinated = (
        event_sign == +1 and insight_polarity == -1
    ) or (
        sent_sign == +1 and insight_polarity == -1 and event_sign != -1
    )

    if is_hallucinated:
        # Downgrade materiality by one step so the output doesn't trigger
        # alarm-level drip/share behaviour.
        _downgrade = {
            "CRITICAL": "HIGH",
            "HIGH": "MODERATE",
            "MODERATE": "LOW",
            "LOW": "LOW",
            "NON-MATERIAL": "NON-MATERIAL",
            "": "",
        }
        new_materiality = _downgrade.get(materiality, materiality)
        if new_materiality and new_materiality != materiality:
            decision["materiality"] = new_materiality
            out["decision_summary"] = decision
            report.corrections.append(
                f"narrative coherence mismatch (event={event_sign:+d}, "
                f"sentiment={sent_sign:+d}, insight={insight_polarity:+d}); "
                f"materiality downgraded {materiality} → {new_materiality}"
            )
            report.math_ok = False  # piggyback on existing flag to surface warning
    return out, report


def verify_and_correct(
    deep_insight: dict,
    revenue_cr: float,
    article_excerpts: list[str] | None = None,
    rationale_lookup: dict[str, str] | None = None,
    event_id: str = "",
    nlp_sentiment: int | None = None,
    event_matched_keywords: list[str] | None = None,
    has_financial_quantum: bool = True,
) -> tuple[dict, VerifierReport]:
    """Run all verifier checks in order. Returns (corrected_insight, report).

    Idempotent — safe to call twice on the same dict.

    Phase 12.4 added the narrative-coherence check. Phase 13 S4 adds the
    low-confidence classification check (event matched only via theme
    fallback OR weak signal + neutral sentiment + no ₹ in article).
    Callers that don't have these signals can omit them; the checks
    skip cleanly in that case (back-compat with Phase 3 signature).
    """
    # 1. Margin math
    out, report = verify_margin_math(deep_insight, revenue_cr)

    # 1.5. Phase 12.7 — hallucination audit. Downgrade "(from article)" tags
    # the LLM invented for figures not actually present in the article. Must
    # run BEFORE enforce_source_tags so downgraded figures are re-inspected.
    out, tags_downgraded = audit_source_tags(out, article_excerpts)
    if tags_downgraded > 0:
        report.corrections.append(
            f"hallucination audit: downgraded {tags_downgraded} unsupported "
            f"'(from article)' claim(s) to '(engine estimate)'"
        )

    # 1.6. Phase 18 — reused-number hallucination audit. Catches the
    # complementary failure mode where the same article-figure value (within
    # ±5%) gets tagged "(from article)" across THREE+ semantically
    # unrelated claims (e.g. ₹503 Cr Q3 profit recycled as "₹500 Cr at risk"
    # + "₹500 Cr green bond" + "₹500 Cr P/E expansion"). Downgrades the
    # extras since at most one such tag can be the genuine source claim.
    out, reused_downgraded = audit_reused_article_figures(out, article_excerpts)
    if reused_downgraded > 0:
        report.corrections.append(
            f"reused-number audit: downgraded {reused_downgraded} duplicated "
            f"'(from article)' claim(s) — same value paired with distinct contexts"
        )

    # 2. Source tags
    out, tags_added = enforce_source_tags(out, article_excerpts)
    report.source_tags_added = tags_added
    if tags_added > 0:
        report.corrections.append(f"added {tags_added} source tags on ₹ figures")

    # 3. CFO headline hygiene (only touches `perspectives.cfo.headline` if present)
    perspectives = out.get("perspectives") or {}
    cfo = perspectives.get("cfo") if isinstance(perspectives, dict) else None
    if isinstance(cfo, dict) and "headline" in cfo:
        clean, was_modified = sanitise_cfo_headline(cfo["headline"])
        if was_modified:
            cfo["headline"] = clean
            report.headline_truncated = True
            report.corrections.append("CFO headline sanitised (stripped Greek/framework IDs or truncated)")

    # 4. Framework rationales
    out, rationales_added = inject_framework_rationales(out, rationale_lookup)
    report.framework_rationales_added = rationales_added
    if rationales_added > 0:
        report.corrections.append(f"injected {rationales_added} framework rationales")

    # 5. Phase 12.4: narrative-data coherence check. Runs last so it sees
    # final materiality after other corrections. Only emits a warning when
    # event_id is known (old callers without the kwarg are skipped).
    if event_id:
        out, coherence = verify_narrative_coherence(out, event_id, nlp_sentiment)
        # Merge coherence corrections into the main report
        report.corrections.extend(coherence.corrections)
        if not coherence.math_ok:
            report.math_ok = False  # preserve the "hallucination detected" signal

    # 6. Phase 12.5: cross-section canonical-exposure drift check.
    # Does NOT rewrite numbers (that would mask root-cause prompt drift) —
    # just surfaces the inconsistency so it's visible in the verifier report.
    _canonical, drift_warnings = verify_cross_section_consistency(out)
    if drift_warnings:
        report.warnings.extend(drift_warnings)
        report.corrections.append(
            f"cross-section consistency: {len(drift_warnings)} drift warning(s)"
        )

    # 6.5. Phase 18: semantic ₹ drift. Same value used in unrelated contexts
    # — surfaces the "₹500 Cr market cap loss / ₹500 Cr green bond / ₹500 Cr
    # P/E expansion" pattern that the numerical drift check at step 6 misses
    # because the value is identical across all three uses.
    semantic_warnings = verify_semantic_consistency(out)
    if semantic_warnings:
        report.warnings.extend(semantic_warnings)
        report.corrections.append(
            f"semantic ₹ drift: {len(semantic_warnings)} reused-value cluster(s) "
            f"with distinct contexts"
        )

    # 7. Phase 13 S4: low-confidence classification check. Theme-fallback
    # events + neutral sentiment + no article ₹ figure → likely shaky
    # output. Surface a `low_confidence_classification: true` flag the UI
    # / email can render as a yellow badge.
    out, lc_warnings = verify_low_confidence_classification(
        out,
        event_matched_keywords=event_matched_keywords,
        nlp_sentiment=nlp_sentiment,
        has_financial_quantum=has_financial_quantum,
    )
    if lc_warnings:
        report.corrections.extend(lc_warnings)

    # 7. ROI caps (per-recommendation, handled by caller — expose helper)
    # We operate on deep_insight; recommendation_engine has its own wrapping

    return out, report
