"""Mandatory ESG framework detection by region and company characteristics."""

MANDATORY_FRAMEWORKS = {
    "India": [
        {"framework": "BRSR", "condition": "Top 1000 by market cap on NSE/BSE", "mandatory_from": "FY2023", "applies_to": ["Large Cap", "Mid Cap"]},
        {"framework": "BRSR_CORE", "condition": "Top 150 by market cap", "mandatory_from": "FY2024", "applies_to": ["Large Cap"]},
    ],
    "Europe": [
        {"framework": "CSRD", "condition": "Large undertakings (>250 employees OR >€40M revenue)", "mandatory_from": "FY2024", "applies_to": ["Large Cap", "Mid Cap"]},
        {"framework": "EU_TAXONOMY", "condition": "Financial market participants in EU", "mandatory_from": "2022", "applies_to": ["Large Cap", "Mid Cap"]},
    ],
    "North America": [
        {"framework": "SEC_CLIMATE", "condition": "SEC registrants", "mandatory_from": "TBD", "applies_to": ["Large Cap"]},
    ],
}


def get_mandatory_frameworks(region: str | None, market_cap: str | None, country: str | None = None) -> list[dict]:
    """Return mandatory frameworks. Checks both region and country."""
    if not region and not country:
        return []
    result = []

    # Map country to the right MANDATORY_FRAMEWORKS key
    COUNTRY_TO_KEY = {
        "india": "India",
        "in": "India",
        "united states": "North America",
        "usa": "North America",
        "us": "North America",
    }
    # EU countries
    for eu in ["germany","france","italy","spain","netherlands","belgium","austria","ireland","portugal","finland","sweden","denmark","poland","czech","romania","greece","hungary","croatia","bulgaria","slovakia","slovenia","luxembourg","malta","cyprus","estonia","latvia","lithuania"]:
        COUNTRY_TO_KEY[eu] = "Europe"

    keys_to_check = set()
    if region:
        for r, frameworks in MANDATORY_FRAMEWORKS.items():
            if r.lower() in region.lower() or region.lower() in r.lower():
                keys_to_check.add(r)
    if country:
        mapped = COUNTRY_TO_KEY.get(country.lower())
        if mapped:
            keys_to_check.add(mapped)

    for key in keys_to_check:
        for fw in MANDATORY_FRAMEWORKS.get(key, []):
            if not market_cap or any(cap.lower() in market_cap.lower() for cap in fw["applies_to"]):
                result.append(fw)
    return result


def is_framework_mandatory(framework_id: str, region: str | None, market_cap: str | None, country: str | None = None) -> bool:
    """Check if a specific framework is mandatory for this company."""
    mandatory = get_mandatory_frameworks(region, market_cap, country)
    return any(fw["framework"].upper() in framework_id.upper() or framework_id.upper() in fw["framework"].upper() for fw in mandatory)
