"""Phase 25 W6 — per-industry MaterialityWeight overrides for new tenants.

When the CSV batch onboarder creates a new tenant
(``data/ontology/tenants/<slug>/extension.ttl``), it pre-populates the
extension with industry-specific materiality weights so the tenant's
intelligence pipeline emphasises the right ESG themes from day one
without waiting for the analyst to run ``/discover-tenant-config``
manually.

Layer 1 already has theme×industry materiality weights via
``query_materiality_weight``. These per-tenant overrides BUMP the Layer
1 weight when the customer's specific industry skews the priority —
e.g. cement's water dependence makes water materiality 0.95 (vs Layer 1
default of ~0.7); pharma's product safety obligation makes that 0.95.

Conservative philosophy: we only override weights where the industry
makes a measurable difference. Themes left out inherit Layer 1's value.

Each entry generates a small TTL fragment via ``build_extension_ttl()``:

    snowkap:tenant_<slug>_water_weight a snowkap:MaterialityWeight ;
        snowkap:weightForTopic snowkap:topic_water ;
        snowkap:weightForIndustry "Cement" ;
        snowkap:weightValue 0.95 ;
        snowkap:weightSource "Phase 25 W6 industry-default seeding" .
"""

from __future__ import annotations

from typing import Iterable


# ---------------------------------------------------------------------------
# 12 industries × theme overrides
# ---------------------------------------------------------------------------

# Mapping: canonical industry name → list of (topic_id, weight, rationale).
# Only LIST themes that materially diverge from Layer 1 defaults; themes
# not listed inherit Layer 1's weight.
INDUSTRY_THEME_DEFAULTS: dict[str, list[tuple[str, float, str]]] = {
    "Cement": [
        ("topic_water", 0.95,
         "Cement is the second-largest industrial water consumer per tonne; CSRD water-stress disclosure mandatory."),
        ("topic_carbon", 0.95,
         "Calcination chemistry produces ~50% of CO2 per tonne (process emissions, not energy); SBTi 1.5C pathway is binding."),
        ("topic_air_pollution", 0.90,
         "PM2.5 and SOx co-emissions are the primary regulatory exposure (CPCB India, EU IED)."),
        ("topic_supply_chain_labor", 0.85,
         "Limestone quarrying involves third-party labour with documented safety + child-labour incidents."),
    ],
    "Automotive": [
        ("topic_supply_chain_labor", 0.90,
         "Tier-2/3 supplier labour rights (cobalt, lithium, leather) carry GRI:408 + GRI:409 disclosure exposure."),
        ("topic_carbon", 0.90,
         "Scope 3 (use-phase) dominates automotive emissions; SBTi requires WTW pathway."),
        ("topic_product_safety", 0.85,
         "Vehicle recalls + battery fires are the primary recurring liability; OEMs file ~5 SEBI/SEC disclosures/year."),
        ("topic_innovation", 0.85,
         "EV transition is existential; capex allocation is the primary CFO question."),
    ],
    "Auto Parts": [
        ("topic_supply_chain_labor", 0.92,
         "Tier-1 suppliers face increasing OEM ESG audit pressure; CSRD cascades upstream from EU OEMs."),
        ("topic_resource_efficiency", 0.88,
         "Material recovery rates + closed-loop steel/aluminium are emerging buyer requirements."),
    ],
    "Chemicals": [
        ("topic_pollution", 0.95,
         "Chemicals industry has the highest pollution-incident frequency per revenue; CPCB / EPA enforcement is active."),
        ("topic_water", 0.92,
         "Wastewater treatment is the primary capex line; CSRD ESRS:E3 mandatory disclosure."),
        ("topic_carbon", 0.88,
         "Process emissions (HFCs, N2O) carry 100-1000x CO2 equivalence; regulatory exposure escalating."),
        ("topic_chemical_safety", 0.95,
         "REACH (EU), Toxic Substances Control Act (US), CICR (India) — every product has a disclosure obligation."),
    ],
    "Pharmaceuticals": [
        ("topic_product_safety", 0.95,
         "Drug recalls + adverse-event reporting are primary regulatory + reputational risk."),
        ("topic_water", 0.90,
         "API manufacturing is water-intensive (10-100 m3/kg API); CPCB consent-to-operate revocations recurring."),
        ("topic_supply_chain_labor", 0.85,
         "Active pharmaceutical ingredient (API) suppliers in India / China face ILO scrutiny."),
        ("topic_pollution", 0.92,
         "Antibiotic effluent = AMR risk (WHO priority); CSRD ESRS:E2 disclosure escalating."),
    ],
    "Information Technology": [
        ("topic_data_privacy", 0.95,
         "DPDPA (India), GDPR (EU), CCPA (US) — data-breach disclosure is the primary regulatory exposure."),
        ("topic_workforce_diversity", 0.85,
         "DEI metrics + pay-equity audits are board-level KPIs for institutional investors."),
        ("topic_carbon", 0.80,
         "Data-centre Scope 2 + supply-chain Scope 3 (hardware) are increasingly material; SBTi adoption accelerating."),
        ("topic_innovation", 0.85,
         "AI ethics + responsible-AI disclosure is emerging as material (EU AI Act 2026)."),
    ],
    "Steel": [
        ("topic_carbon", 0.95,
         "Steel is 7-8% of global CO2; SBTi 1.5C pathway requires ~50% reduction by 2030; CBAM (EU) applies from 2026."),
        ("topic_air_pollution", 0.90,
         "Coke-oven emissions + SOx/NOx are primary CPCB action items; integrated mills face most exposure."),
        ("topic_supply_chain_labor", 0.85,
         "Iron-ore mining (Goa, Karnataka, Odisha) has recurring labour + community displacement issues."),
    ],
    "Power/Energy": [
        ("topic_carbon", 0.95,
         "Coal IPPs face stranded-asset risk; SBTi + RBI Climate Stress Test set the pathway."),
        ("topic_water", 0.90,
         "Cooling water for thermal plants conflicts with agriculture / municipal use during droughts."),
        ("topic_air_pollution", 0.92,
         "PM2.5 + Hg + SO2 from coal generation; CPCB Phase II compliance ongoing."),
    ],
    "Renewable Energy": [
        ("topic_supply_chain_labor", 0.85,
         "Polysilicon + rare earths + lithium supply chain face Xinjiang Forced Labour Prevention Act + EU Forced Labour ban."),
        ("topic_resource_efficiency", 0.85,
         "End-of-life solar panel + wind blade recycling is emerging policy issue; EPR regulations forming."),
        ("topic_grid_integration", 0.80,
         "Curtailment + grid stability are operational risks affecting PPA economics."),
    ],
    "Logistics": [
        ("topic_carbon", 0.92,
         "Scope 1 (fleet diesel) + Scope 3 (multi-modal) are 8-12% of customer Scope 3; SBTi cascade pressure."),
        ("topic_workforce_safety", 0.85,
         "Driver fatigue + warehouse injury rates are primary regulatory + insurer exposure."),
        ("topic_supply_chain_labor", 0.80,
         "Last-mile gig labour rights (CCS code, EU Platform Work Directive) creating material risk."),
    ],
    "Real Estate": [
        ("topic_carbon", 0.85,
         "Embodied carbon (cement + steel) + operational energy = ~40% of global emissions; CSRD ESRS:E1 mandatory."),
        ("topic_resource_efficiency", 0.80,
         "Construction waste + water consumption during build phase carry CPCB consent-to-operate exposure."),
        ("topic_workforce_safety", 0.85,
         "Construction-site fatality rates remain ~10x manufacturing; ILO + state labour board scrutiny."),
    ],
    "Other / General": [
        # Default for unknown industries — no overrides, inherits Layer 1.
    ],
}


# ---------------------------------------------------------------------------
# Topic ID → human label (for the rationale strings in the TTL)
# ---------------------------------------------------------------------------

_TOPIC_LABELS: dict[str, str] = {
    "topic_water": "Water",
    "topic_carbon": "Carbon / GHG Emissions",
    "topic_air_pollution": "Air Pollution",
    "topic_pollution": "Pollution / Effluent",
    "topic_supply_chain_labor": "Supply Chain Labour",
    "topic_product_safety": "Product Safety",
    "topic_data_privacy": "Data Privacy",
    "topic_workforce_diversity": "Workforce Diversity & Inclusion",
    "topic_workforce_safety": "Workforce Safety",
    "topic_resource_efficiency": "Resource Efficiency / Circularity",
    "topic_chemical_safety": "Chemical Safety",
    "topic_innovation": "Innovation / R&D",
    "topic_grid_integration": "Grid Integration",
}


def get_overrides_for_industry(industry: str) -> list[tuple[str, float, str]]:
    """Return the list of (topic_id, weight, rationale) for an industry.
    Returns empty list when industry is unknown — caller inherits Layer 1."""
    return INDUSTRY_THEME_DEFAULTS.get(industry, [])


def list_supported_industries() -> list[str]:
    """All industries with non-trivial overrides (excludes 'Other / General')."""
    return [k for k, v in INDUSTRY_THEME_DEFAULTS.items() if v]


# ---------------------------------------------------------------------------
# TTL fragment generator
# ---------------------------------------------------------------------------


def build_extension_ttl(
    tenant_slug: str,
    industry: str,
    *,
    extra_overrides: Iterable[tuple[str, float, str]] | None = None,
) -> str:
    """Generate the contents of a tenant's
    ``data/ontology/tenants/<slug>/extension.ttl`` file.

    Adds:
      * Standard TTL prefix block
      * One ``snowkap:MaterialityWeight`` instance per override
      * A header comment naming the seeding source so future analysts
        know these aren't hand-curated and CAN be overridden by
        ``/discover-tenant-config`` answers later.

    Empty-override industries (e.g. "Other / General") still get a
    valid TTL with the prefix block and a placeholder comment — the
    file must exist so the tenant resolver knows the tenant has been
    onboarded.
    """
    overrides = list(get_overrides_for_industry(industry))
    if extra_overrides:
        overrides.extend(extra_overrides)

    lines: list[str] = [
        "@prefix owl:     <http://www.w3.org/2002/07/owl#> .",
        "@prefix rdf:     <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "@prefix rdfs:    <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix xsd:     <http://www.w3.org/2001/XMLSchema#> .",
        "@prefix snowkap: <http://snowkap.com/ontology/esg#> .",
        "",
        "# >>> LAYER 3 — TENANT EXTENSION (Phase 25 W6 industry-default seeding) <<<",
        f"# Tenant slug:  {tenant_slug}",
        f"# Industry:     {industry}",
        "# Source:       Auto-generated from engine.ingestion.industry_materiality_defaults",
        "# Override:     Run /discover-tenant-config to replace these with analyst-curated values.",
        "",
    ]

    if not overrides:
        lines.append("# (No industry-specific overrides — tenant inherits Layer 1 defaults)")
        return "\n".join(lines) + "\n"

    for topic_id, weight, rationale in overrides:
        topic_label = _TOPIC_LABELS.get(topic_id, topic_id)
        weight_uri = f"snowkap:tenant_{tenant_slug}_{topic_id.replace('topic_', '')}_weight"
        # Escape any double-quotes in the rationale + industry name
        safe_rationale = rationale.replace('"', "'")
        safe_industry = industry.replace('"', "'")
        lines.extend([
            f"# {topic_label} materiality override",
            f"{weight_uri} a snowkap:MaterialityWeight ;",
            f"    snowkap:weightForTopic snowkap:{topic_id} ;",
            f'    snowkap:weightForIndustry "{safe_industry}" ;',
            f"    snowkap:weightValue {weight:.2f} ;",
            f'    snowkap:weightSource "Phase 25 W6 industry-default seeding" ;',
            f'    rdfs:comment "{safe_rationale}" .',
            "",
        ])

    return "\n".join(lines)
