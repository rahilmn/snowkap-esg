"""Deterministic causal cascade computation engine (Level 2).

Traverses the Primitives ontology edges and COMPUTES financial impact
using company-specific calibration data, 6 functional forms, and
5 aggregation rules. Returns hard ₹ figures that are injected as
constraints into the LLM prompt — the LLM writes prose around
these numbers but cannot override them.

Usage::

    from engine.analysis.primitive_engine import compute_cascade
    result = compute_cascade("event_heavy_penalty", company, delta_source=50.38)
    # result.total_exposure_cr = 59.45
    # result.computation_trace = "CL→OX: +₹5.04 Cr (β=0.10, step)\\n..."
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from engine.config import Company

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CascadeHop:
    edge_id: str
    source_slug: str
    target_slug: str
    direction: str          # +, −, mixed
    functional_form: str    # linear, log-linear, threshold, ratio, step, composite
    beta_range: str         # ontology β range string (e.g. "0.05–0.25")
    beta_used: float        # actual β applied after sector calibration
    delta_pct: float        # % change at this hop
    delta_cr: float         # ₹ Cr impact at this hop
    lag: str                # e.g. "0–2q"
    confidence: str         # high, medium, low
    notes: str


@dataclass
class CascadeResult:
    event_id: str
    primary_primitive: str
    primary_label: str
    delta_source_cr: float     # article's ₹ quantum (or estimated)
    hops: list[CascadeHop] = field(default_factory=list)
    total_exposure_cr: float = 0.0
    margin_bps: float = 0.0
    confidence: str = "medium"
    computation_trace: str = ""

    source_is_from_article: bool = False  # True if delta_source_cr came from NLP extraction

    def to_prompt_block(self) -> str:
        """Format as a text block for LLM prompt injection."""
        source_tag = "from article" if self.source_is_from_article else "engine estimate"
        lines = [
            "COMPUTED FINANCIAL CASCADE (verified — use these exact numbers):",
            f"  Event: {self.event_id}",
            f"  Primary primitive: {self.primary_label} ({self.primary_primitive})",
            f"  Direct quantum: ₹{self.delta_source_cr:.1f} Cr ({source_tag})",
        ]
        for hop in self.hops:
            sign = "+" if hop.direction != "−" else "-"
            lines.append(
                f"  {hop.edge_id}: {sign}₹{abs(hop.delta_cr):.1f} Cr "
                f"(β={hop.beta_used:.3f}, {hop.functional_form}, lag={hop.lag})"
            )
        lines.append(f"  Margin impact: {self.margin_bps:.1f} bps")
        lines.append(f"  Total exposure: ₹{self.total_exposure_cr:.1f} Cr ({self.confidence} confidence)")
        lines.append("")
        lines.append(
            "  financial_exposure MUST use these numbers. "
            "margin_pressure MUST be {:.1f} bps. Do NOT estimate different figures.".format(
                self.margin_bps
            )
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Functional forms
# ---------------------------------------------------------------------------


def _apply_form(
    form: str, delta: float, beta: float, threshold: float | None = None
) -> float:
    """Apply a functional form to compute the output delta.

    Args:
        form: One of linear, log-linear, threshold, ratio, step, composite.
        delta: Input change (absolute ₹ Cr or % depending on context).
        beta: Calibrated elasticity/weight.
        threshold: τ value for threshold/step forms (None if not applicable).

    Returns:
        Output delta (₹ Cr).
    """
    if form == "linear":
        return beta * delta

    if form == "log-linear":
        # Protect against negative log: ln(1 + |x|) × sign(x)
        sign = 1.0 if delta >= 0 else -1.0
        return beta * math.log(1 + abs(delta)) * sign

    if form == "threshold":
        tau = threshold or 0.0
        if abs(delta) > tau:
            return beta * delta
        return 0.0  # Below threshold — no impact

    if form == "step":
        tau = threshold or 0.0
        if abs(delta) >= tau:
            return beta * abs(delta)  # Step fires: full β × magnitude
        return 0.0

    if form == "ratio":
        # Deviation from baseline: β × (x/base - 1)
        # Here delta IS the deviation already, so just β × delta
        return beta * delta

    # composite or unknown: fall back to linear
    return beta * delta


# ---------------------------------------------------------------------------
# β calibration
# ---------------------------------------------------------------------------


def _calibrate_beta(
    beta_range: str,
    company: Company,
    source_slug: str,
    target_slug: str,
) -> float:
    """Calibrate β from ontology range using company-specific cost shares.

    Strategy:
    1. Parse β range (e.g. "0.15–0.35") → take midpoint
    2. Scale by company's cost share for the SOURCE primitive
    3. Clamp to original range bounds

    For edges where SOURCE is a cost driver (EP, LC, FR, CM):
      β_calibrated = β_midpoint × (company_share / industry_avg_share)

    For edges where SOURCE is a non-cost primitive (CL, RG, CY, XW):
      β_calibrated = β_midpoint (no cost-share scaling)
    """
    # Parse range
    try:
        parts = beta_range.replace("–", "-").replace("—", "-").split("-")
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) >= 2:
            lo = float(parts[0])
            hi = float(parts[1])
        elif len(parts) == 1:
            lo = hi = float(parts[0])
        else:
            return 0.1  # fallback
    except (ValueError, IndexError):
        return 0.1

    midpoint = (lo + hi) / 2.0

    # Cost-driver primitives: scale β by company's cost share
    cost_driver_primitives = {"EP", "EU", "LC", "WF", "FR", "LT", "WA", "CM"}
    if source_slug.upper() in cost_driver_primitives:
        company_share = company.get_cost_share(source_slug)
        # Industry average share assumed at 0.15 (15% default)
        industry_avg = 0.15
        if company_share > 0 and industry_avg > 0:
            scale = company_share / industry_avg
            calibrated = midpoint * scale
            return max(lo * 0.5, min(hi * 1.5, calibrated))  # clamp within 0.5×lo to 1.5×hi

    return midpoint


# ---------------------------------------------------------------------------
# Main cascade computation
# ---------------------------------------------------------------------------


def compute_cascade(
    event_id: str,
    company: Company,
    delta_source_cr: float | None = None,
    signal_unit: str = "cr",
    max_order: int = 2,
) -> CascadeResult | None:
    """Compute the quantitative financial cascade for an event × company.

    Args:
        event_id: Snowkap event type (e.g. "event_heavy_penalty").
        company: Company with primitive_calibration data.
        delta_source_cr: Magnitude from article. If signal_unit is "cr", this is ₹ Cr.
                         If signal_unit is "percent", this is a % which gets converted
                         to ₹ Cr using company revenue.
        signal_unit: "cr" for ₹ Crores (default), "percent" for percentage signals.
        max_order: Maximum cascade depth (2 = primary→secondary, 3 = tertiary).

    Returns:
        CascadeResult with computed ₹ figures, or None if no primitives mapped.
    """
    from engine.ontology.intelligence import (
        query_p2p_edges,
        query_primitives_for_event,
    )

    # 1. Get affected primitives
    prims = query_primitives_for_event(event_id)
    if not prims:
        logger.warning("compute_cascade: no primitives for event '%s'", event_id)
        return None

    primary = prims[0]

    # 2. Default delta if not provided; convert % to ₹ if needed
    source_from_article = delta_source_cr is not None and delta_source_cr > 0
    is_percentage = signal_unit == "percent"

    if not source_from_article:
        # Estimate from company scale: 0.1% of revenue for generic events
        delta_source_cr = company.revenue_cr * 0.001 if company.revenue_cr > 0 else 10.0
    elif is_percentage and company.revenue_cr > 0:
        # Convert percentage signal to ₹ Cr: e.g., 24% upside × revenue = potential ₹ impact
        # Use a dampened conversion: actual realized impact is ~10-20% of predicted % move
        pct_value = delta_source_cr
        dampening = 0.02  # analyst % targets: ~2% of revenue translates to actual benefit
        delta_source_cr = company.revenue_cr * (pct_value / 100.0) * dampening
        logger.info(
            "compute_cascade: converted %s%% signal to ₹%.1f Cr (%.0f%% × ₹%.0f Cr × %.0f%% dampening)",
            pct_value, delta_source_cr, pct_value, company.revenue_cr, dampening * 100,
        )

    result = CascadeResult(
        event_id=event_id,
        primary_primitive=primary.slug,
        primary_label=primary.label,
        delta_source_cr=delta_source_cr,
        source_is_from_article=source_from_article,
    )

    # 3. Get order-2 edges from primary primitive
    edges = query_p2p_edges(primary.slug)
    if not edges:
        # No edges — return with direct quantum only
        result.total_exposure_cr = delta_source_cr
        result.margin_bps = (
            delta_source_cr / company.revenue_cr * 10000
            if company.revenue_cr > 0
            else 0.0
        )
        result.confidence = "low"
        result.computation_trace = (
            f"Direct: ₹{delta_source_cr:.1f} Cr (no cascade edges for {primary.slug})"
        )
        return result

    # 4. Compute each hop
    total_cascade_cr = 0.0
    min_confidence = "high"
    confidence_rank = {"high": 3, "medium": 2, "low": 1}

    for edge in edges:
        beta = _calibrate_beta(
            edge.elasticity, company, edge.source_slug, edge.target_slug
        )

        # Determine base for this edge
        # For cost-transmission edges (→OX, →CX), base = relevant cost bucket
        # For revenue edges (→RV), base = revenue
        target = edge.target_slug.upper()
        if target in ("OX",):
            base_cr = company.opex_cr
        elif target in ("CX",):
            base_cr = company.capex_cr
        elif target in ("RV",):
            base_cr = company.revenue_cr
        else:
            base_cr = company.revenue_cr  # default

        # Apply functional form
        delta_cr = _apply_form(
            edge.functional_form,
            delta_source_cr,
            beta,
            threshold=None,  # TODO: query τ from ontology
        )

        # Direction adjustment
        if edge.direction == "−":
            delta_cr = -abs(delta_cr)

        hop = CascadeHop(
            edge_id=edge.edge_id,
            source_slug=edge.source_slug,
            target_slug=edge.target_slug,
            direction=edge.direction,
            functional_form=edge.functional_form,
            beta_range=edge.elasticity,
            beta_used=beta,
            delta_pct=(delta_cr / base_cr * 100) if base_cr > 0 else 0.0,
            delta_cr=delta_cr,
            lag=edge.lag,
            confidence=edge.confidence,
            notes=edge.notes,
        )
        result.hops.append(hop)
        total_cascade_cr += abs(delta_cr)

        # Track minimum confidence
        edge_rank = confidence_rank.get(edge.confidence, 1)
        result_rank = confidence_rank.get(min_confidence, 3)
        if edge_rank < result_rank:
            min_confidence = edge.confidence

    # 5. Compute totals
    result.total_exposure_cr = delta_source_cr + total_cascade_cr
    result.margin_bps = (
        delta_source_cr / company.revenue_cr * 10000
        if company.revenue_cr > 0
        else 0.0
    )
    result.confidence = min_confidence

    # 6. Build human-readable trace
    trace_lines = [f"Direct: ₹{delta_source_cr:.1f} Cr ({primary.label})"]
    for hop in result.hops:
        sign = "+" if hop.direction != "−" else "-"
        trace_lines.append(
            f"  {hop.edge_id}: {sign}₹{abs(hop.delta_cr):.1f} Cr "
            f"(β={hop.beta_used:.3f}, {hop.functional_form}, {hop.confidence})"
        )
    trace_lines.append(f"Total: ₹{result.total_exposure_cr:.1f} Cr | Margin: {result.margin_bps:.1f} bps")
    result.computation_trace = "\n".join(trace_lines)

    logger.info(
        "compute_cascade: %s × %s → ₹%.1f Cr total (%d hops, %s confidence)",
        event_id,
        company.slug,
        result.total_exposure_cr,
        len(result.hops),
        result.confidence,
    )

    return result
