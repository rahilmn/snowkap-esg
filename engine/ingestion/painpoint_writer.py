"""W3 — Serialise a PainpointReport into a per-tenant `painpoints.ttl`.

The output file lives alongside the tenant's `extension.ttl` in
``data/ontology/tenants/<slug>/painpoints.ttl`` and is loaded by
``engine.ontology.graph`` AFTER the extension TTL so painpoints with
``confidence >= 0.7`` override the industry-default materiality weights
for overlapping topics.

Every triple carries:
  * ``dc:source``           — domain we ran discovery against (audit trail)
  * ``dc:created``          — ISO timestamp of the LLM run
  * ``prov:wasGeneratedBy`` — model name (e.g. ``gpt-4.1``) for re-run on
                              model upgrade
  * ``snowkap:confidence``  — LLM self-rated certainty (0.0-1.0)

Idempotency: ``write_painpoints_ttl`` returns the resolved Path so the
caller can stat it; ``onboard_company`` skips the LLM call if the file
exists and is < 90 days old (`needs_refresh` helper below).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from engine.ingestion.painpoint_discoverer import PainpointReport
from engine.ontology.tenant_resolver import ensure_tenant_dir

logger = logging.getLogger(__name__)


PAINPOINTS_FILENAME = "painpoints.ttl"
PAINPOINTS_TTL_AGE_DAYS = 90  # re-run discovery after this many days


def tenant_painpoints_path(tenant_id: str) -> Path:
    """Path to a tenant's painpoints TTL file."""
    return ensure_tenant_dir(tenant_id) / PAINPOINTS_FILENAME


def needs_refresh(tenant_id: str, max_age_days: int = PAINPOINTS_TTL_AGE_DAYS) -> bool:
    """True iff `painpoints.ttl` is missing OR older than ``max_age_days``."""
    p = tenant_painpoints_path(tenant_id)
    if not p.exists():
        return True
    try:
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return True
    return (datetime.now(timezone.utc) - mtime) > timedelta(days=max_age_days)


def write_painpoints_ttl(
    *,
    tenant_id: str,
    report: PainpointReport,
    domain: str,
    company_name: str,
    industry: str,
    region: str,
    model: str = "gpt-4.1",
) -> Path:
    """Serialise the report into the tenant's painpoints.ttl. Returns path.

    Always writes — even if the report is empty — so callers can stat the
    file age and decide whether to re-run.
    """
    path = tenant_painpoints_path(tenant_id)
    iso_now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    safe_slug = "".join(c if c.isalnum() else "_" for c in tenant_id)

    lines: list[str] = []
    lines.append("@prefix owl:     <http://www.w3.org/2002/07/owl#> .")
    lines.append("@prefix rdf:     <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .")
    lines.append("@prefix rdfs:    <http://www.w3.org/2000/01/rdf-schema#> .")
    lines.append("@prefix xsd:     <http://www.w3.org/2001/XMLSchema#> .")
    lines.append("@prefix dc:      <http://purl.org/dc/terms/> .")
    lines.append("@prefix prov:    <http://www.w3.org/ns/prov#> .")
    lines.append("@prefix snowkap: <http://snowkap.com/ontology/esg#> .")
    lines.append("")
    lines.append("# >>> LAYER 3 — TENANT PAINPOINTS (W3 LLM-discovered) <<<")
    lines.append(f"# Tenant slug:  {tenant_id}")
    lines.append(f"# Company:      {company_name}")
    lines.append(f"# Industry:     {industry}")
    lines.append(f"# Region:       {region}")
    lines.append(f"# Source:       {model}")
    lines.append(f"# Generated at: {iso_now}")
    lines.append(f"# Painpoints:   {len(report.painpoints)}")
    lines.append(
        "# Override:     Loaded AFTER extension.ttl; entries with "
        "snowkap:confidence >= 0.7 override industry-default MaterialityWeights"
    )
    lines.append("")

    if report.is_empty():
        lines.append("# (LLM discovery returned no painpoints — keeping industry defaults.)")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("painpoint_writer: wrote empty painpoints.ttl for %s", tenant_id)
        return path

    # 1. MaterialityWeight overrides — one per painpoint with confidence >= 0.5.
    #    Loaded AFTER extension.ttl so the same predicate slot wins by
    #    ORDER BY in the SPARQL layer.
    for i, pp in enumerate(report.painpoints):
        uri_local = f"tenant_{safe_slug}_painpoint_{i}_{pp.topic_slug}"
        evidence_escaped = pp.evidence.replace('"', '\\"').replace("\n", " ")
        lines.append(f"snowkap:{uri_local} a snowkap:MaterialityWeight ;")
        lines.append(f"    snowkap:weightForTopic snowkap:topic_{pp.topic_slug} ;")
        lines.append(f'    snowkap:weightForIndustry "{industry}" ;')
        lines.append(f"    snowkap:weightValue {pp.severity:.2f} ;")
        lines.append(f"    snowkap:confidence {pp.confidence:.2f} ;")
        lines.append(f'    snowkap:weightSource "W3 LLM painpoint discovery" ;')
        lines.append(f'    dc:source "{domain}" ;')
        lines.append(f'    dc:created "{iso_now}"^^xsd:dateTime ;')
        lines.append(f'    prov:wasGeneratedBy "{model}" ;')
        lines.append(f'    rdfs:comment "{evidence_escaped}" .')
        lines.append("")

    # 2. Tenant-level metadata so SPARQL queries can find the company's
    #    primary_frameworks + stakeholder_concerns + headline_painpoints.
    lines.append(f"snowkap:tenant_{safe_slug}_profile a snowkap:TenantPainpointProfile ;")
    lines.append(f'    snowkap:forTenant "{tenant_id}" ;')
    lines.append(f'    snowkap:forCompany "{company_name}" ;')
    lines.append(f'    snowkap:tenantIndustry "{industry}" ;')
    lines.append(f'    snowkap:tenantRegion "{region}" ;')
    lines.append(f'    dc:source "{domain}" ;')
    lines.append(f'    dc:created "{iso_now}"^^xsd:dateTime ;')
    lines.append(f'    prov:wasGeneratedBy "{model}"')
    if report.primary_frameworks:
        frameworks = " , ".join(f'"{f}"' for f in report.primary_frameworks)
        lines.append(f"    ;")
        lines.append(f"    snowkap:primaryFramework {frameworks}")
    if report.stakeholder_concerns:
        concerns = " , ".join(f'"{c.replace(chr(34), chr(92)+chr(34))}"' for c in report.stakeholder_concerns)
        lines.append(f"    ;")
        lines.append(f"    snowkap:stakeholderConcern {concerns}")
    if report.headline_painpoints:
        headlines = " , ".join(f'"{h.replace(chr(34), chr(92)+chr(34))}"' for h in report.headline_painpoints)
        lines.append(f"    ;")
        lines.append(f"    snowkap:headlinePainpoint {headlines}")
    lines.append("    .")
    lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(
        "painpoint_writer: wrote %d painpoints to %s",
        len(report.painpoints), path,
    )
    return path
