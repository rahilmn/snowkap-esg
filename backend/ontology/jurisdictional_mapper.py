"""Jurisdictional Framework Mapper — maps geographic locations to regulatory regimes.

Per System Prompt Module 1: Every location must be mapped to applicable regulatory
frameworks (EU Taxonomy, UK SDR, SFDR, SEC Climate Rules, ISSB, CSRD, local equivalents).
"""

# Country/region → applicable ESG regulatory frameworks
JURISDICTION_FRAMEWORKS: dict[str, list[str]] = {
    # European Union (27 member states)
    "eu": ["EU Taxonomy", "CSRD", "SFDR", "ESRS"],
    "germany": ["EU Taxonomy", "CSRD", "SFDR", "ESRS"],
    "france": ["EU Taxonomy", "CSRD", "SFDR", "ESRS"],
    "netherlands": ["EU Taxonomy", "CSRD", "SFDR", "ESRS"],
    "italy": ["EU Taxonomy", "CSRD", "SFDR", "ESRS"],
    "spain": ["EU Taxonomy", "CSRD", "SFDR", "ESRS"],
    "belgium": ["EU Taxonomy", "CSRD", "SFDR", "ESRS"],
    "austria": ["EU Taxonomy", "CSRD", "SFDR", "ESRS"],
    "ireland": ["EU Taxonomy", "CSRD", "SFDR", "ESRS"],
    "luxembourg": ["EU Taxonomy", "CSRD", "SFDR", "ESRS"],
    "sweden": ["EU Taxonomy", "CSRD", "SFDR", "ESRS"],
    "denmark": ["EU Taxonomy", "CSRD", "SFDR", "ESRS"],
    "finland": ["EU Taxonomy", "CSRD", "SFDR", "ESRS"],
    "poland": ["EU Taxonomy", "CSRD", "SFDR", "ESRS"],
    # United Kingdom
    "uk": ["UK SDR", "UK Taxonomy", "TCFD"],
    "united kingdom": ["UK SDR", "UK Taxonomy", "TCFD"],
    "england": ["UK SDR", "UK Taxonomy", "TCFD"],
    "london": ["UK SDR", "UK Taxonomy", "TCFD"],
    # United States
    "us": ["SEC Climate Rules", "ISSB"],
    "usa": ["SEC Climate Rules", "ISSB"],
    "united states": ["SEC Climate Rules", "ISSB"],
    "california": ["SEC Climate Rules", "ISSB", "CA SB 253/261"],
    "new york": ["SEC Climate Rules", "ISSB"],
    # India
    "india": ["BRSR", "SEBI ESG", "BRSR Core"],
    "mumbai": ["BRSR", "SEBI ESG", "BRSR Core"],
    "delhi": ["BRSR", "SEBI ESG", "BRSR Core"],
    "bangalore": ["BRSR", "SEBI ESG", "BRSR Core"],
    "chennai": ["BRSR", "SEBI ESG", "BRSR Core"],
    "hyderabad": ["BRSR", "SEBI ESG", "BRSR Core"],
    "kolkata": ["BRSR", "SEBI ESG", "BRSR Core"],
    "ahmedabad": ["BRSR", "SEBI ESG", "BRSR Core"],
    "pune": ["BRSR", "SEBI ESG", "BRSR Core"],
    "surat": ["BRSR", "SEBI ESG", "BRSR Core"],
    "gujarat": ["BRSR", "SEBI ESG", "BRSR Core"],
    "maharashtra": ["BRSR", "SEBI ESG", "BRSR Core"],
    "rajasthan": ["BRSR", "SEBI ESG", "BRSR Core"],
    "karnataka": ["BRSR", "SEBI ESG", "BRSR Core"],
    "tamil nadu": ["BRSR", "SEBI ESG", "BRSR Core"],
    # China
    "china": ["ISSB", "CSRC ESG", "CBAM"],
    "beijing": ["ISSB", "CSRC ESG", "CBAM"],
    "shanghai": ["ISSB", "CSRC ESG", "CBAM"],
    # Japan
    "japan": ["ISSB", "TCFD", "TNFD"],
    "tokyo": ["ISSB", "TCFD", "TNFD"],
    # Singapore
    "singapore": ["ISSB", "SGX ESG", "MAS Green Finance"],
    # Australia
    "australia": ["ISSB", "ASRS"],
    # Brazil
    "brazil": ["ISSB", "CVM ESG"],
    # South Korea
    "south korea": ["ISSB", "K-ESG"],
    # Global frameworks (always applicable)
    "_global": ["GRI", "ISSB", "TCFD", "CDP"],
}

# Geo-political risk tags beyond climate
GEO_RISK_TAGS: dict[str, list[str]] = {
    # Sanctions exposure
    "russia": ["sanctions_exposure", "regulatory_transition"],
    "iran": ["sanctions_exposure"],
    "myanmar": ["sanctions_exposure", "political_instability"],
    "north korea": ["sanctions_exposure"],
    # Political instability
    "sudan": ["political_instability", "conflict_zone"],
    "yemen": ["political_instability", "conflict_zone"],
    "libya": ["political_instability"],
    "haiti": ["political_instability"],
    "ethiopia": ["political_instability"],
    # Regulatory transition
    "eu": ["regulatory_transition"],
    "uk": ["regulatory_transition"],
    "us": ["regulatory_transition"],
    "china": ["regulatory_transition"],
    "india": ["regulatory_transition"],
    # Supply chain concentration risk
    "taiwan": ["supply_chain_concentration", "geopolitical_tension"],
    "vietnam": ["supply_chain_concentration"],
    "bangladesh": ["supply_chain_concentration", "climate_vulnerability"],
    # Climate vulnerability (beyond climate_risk_zones)
    "maldives": ["climate_vulnerability"],
    "bangladesh": ["climate_vulnerability", "supply_chain_concentration"],
    "pacific islands": ["climate_vulnerability"],
}


def map_location_to_jurisdictions(location: str) -> list[str]:
    """Map a location string to applicable regulatory frameworks."""
    loc_lower = location.lower().strip()
    frameworks: set[str] = set()

    # Check exact match first
    if loc_lower in JURISDICTION_FRAMEWORKS:
        frameworks.update(JURISDICTION_FRAMEWORKS[loc_lower])

    # Check if location is within a known country (partial match)
    for key, fws in JURISDICTION_FRAMEWORKS.items():
        if key != "_global" and (key in loc_lower or loc_lower in key):
            frameworks.update(fws)

    # Always add global frameworks
    frameworks.update(JURISDICTION_FRAMEWORKS["_global"])

    return sorted(frameworks)


def map_location_to_geo_risks(location: str) -> list[str]:
    """Map a location to geopolitical/sanctions/instability risk tags."""
    loc_lower = location.lower().strip()
    risks: set[str] = set()

    for key, tags in GEO_RISK_TAGS.items():
        if key in loc_lower or loc_lower in key:
            risks.update(tags)

    return sorted(risks)


def build_geographic_signal(
    locations: list[str],
    supplier_locations: list[dict] | None = None,
) -> dict:
    """Build structured GEOGRAPHIC SIGNAL output per Module 1 spec.

    Returns:
        {
            "locations_detected": [...],
            "regulatory_jurisdictions": {...},
            "supply_chain_overlap": "Tier 1" | "No direct overlap",
            "geo_risk_flags": [...]
        }
    """
    all_jurisdictions: dict[str, list[str]] = {}
    all_risks: list[str] = []
    supply_chain_overlap = "No direct overlap"
    overlap_tier = None

    for loc in locations:
        jur = map_location_to_jurisdictions(loc)
        if jur:
            all_jurisdictions[loc] = jur
        risks = map_location_to_geo_risks(loc)
        all_risks.extend(risks)

        # Check supply chain overlap
        if supplier_locations:
            for sup in supplier_locations:
                sup_city = (sup.get("city") or "").lower()
                sup_country = (sup.get("country") or "").lower()
                tier = sup.get("tier", 99)
                if loc.lower() in sup_city or loc.lower() in sup_country or sup_city in loc.lower():
                    if overlap_tier is None or tier < overlap_tier:
                        overlap_tier = tier
                        supply_chain_overlap = f"Tier {tier} supplier overlap"

    return {
        "locations_detected": locations,
        "regulatory_jurisdictions": all_jurisdictions,
        "supply_chain_overlap": supply_chain_overlap,
        "geo_risk_flags": sorted(set(all_risks)),
    }
