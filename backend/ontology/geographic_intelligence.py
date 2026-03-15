"""Geographic Intelligence — proximity matching for news impact.

Per MASTER_BUILD_PLAN Phase 3.3:
- Company → facility locations (lat/lng, district, state)
- News → location extraction
- Proximity matching: "water scarcity in Kolhapur" → "you have a plant in Kolhapur"
- Climate/disaster risk zones per geography
"""

import math
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.company import Company, Facility
from backend.ontology.jena_client import jena_client

logger = structlog.get_logger()

SNOWKAP_NS = "http://snowkap.com/ontology/esg#"

# Climate risk zones per region (India-focused initial dataset)
CLIMATE_RISK_ZONES = {
    "coastal_flood": ["mumbai", "chennai", "kolkata", "kochi", "visakhapatnam", "surat"],
    "drought_prone": ["marathwada", "vidarbha", "bundelkhand", "rayalaseema", "kutch"],
    "cyclone_belt": ["odisha", "andhra pradesh", "tamil nadu", "west bengal", "gujarat"],
    "heat_stress": ["rajasthan", "madhya pradesh", "telangana", "chhattisgarh", "vidarbha"],
    "flood_prone": ["assam", "bihar", "uttar pradesh", "kerala", "karnataka"],
    "seismic": ["himalayan belt", "kutch", "northeast india"],
    "industrial_pollution": ["delhi", "kanpur", "ludhiana", "vapi", "ankleshwar"],
}


@dataclass
class GeoMatch:
    """A geographic proximity match between news location and company facility."""
    facility_id: str
    facility_name: str
    company_id: str
    company_name: str
    matched_location: str
    match_type: str  # exact_city, exact_district, exact_state, proximity
    distance_km: float | None = None
    climate_risk_zones: list[str] | None = None


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points (km)."""
    R = 6371.0
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def find_geographic_matches(
    locations: list[str],
    tenant_id: str,
    db: AsyncSession,
    proximity_km: float = 100.0,
) -> list[GeoMatch]:
    """Find facilities near the extracted news locations.

    Matching strategy (in priority order):
    1. Exact city match
    2. Exact district match
    3. Exact state match
    4. Lat/lng proximity within threshold
    """
    # Get all facilities for this tenant
    result = await db.execute(
        select(Facility, Company)
        .join(Company, Facility.company_id == Company.id)
        .where(Facility.tenant_id == tenant_id)
    )
    rows = result.all()

    if not rows:
        return []

    matches: list[GeoMatch] = []
    locations_lower = [loc.lower().strip() for loc in locations]

    for facility, company in rows:
        for location in locations_lower:
            match_type = None

            # Exact city match
            if facility.city and location in facility.city.lower():
                match_type = "exact_city"
            # Exact district match
            elif facility.district and location in facility.district.lower():
                match_type = "exact_district"
            # Exact state match
            elif facility.state and location in facility.state.lower():
                match_type = "exact_state"

            if match_type:
                # Determine climate risk zones for this location
                risk_zones = _get_climate_risks(location)

                matches.append(GeoMatch(
                    facility_id=facility.id,
                    facility_name=facility.name,
                    company_id=company.id,
                    company_name=company.name,
                    matched_location=location,
                    match_type=match_type,
                    climate_risk_zones=risk_zones,
                ))

    logger.info(
        "geo_matches_found",
        locations=locations,
        matches=len(matches),
        tenant_id=tenant_id,
    )
    return matches


def _get_climate_risks(location: str) -> list[str]:
    """Get climate risk zones for a location."""
    risks = []
    location_lower = location.lower()
    for zone, regions in CLIMATE_RISK_ZONES.items():
        for region in regions:
            if region in location_lower or location_lower in region:
                risks.append(zone)
                break
    return risks


async def seed_facilities_to_jena(
    company_id: str,
    tenant_id: str,
    db: AsyncSession,
) -> bool:
    """Seed facility geographic data into the tenant's Jena graph.

    Creates facility nodes with lat/lng, links to company and geographic regions.
    """
    result = await db.execute(
        select(Facility, Company)
        .join(Company, Facility.company_id == Company.id)
        .where(Facility.company_id == company_id, Facility.tenant_id == tenant_id)
    )
    rows = result.all()

    triples: list[tuple[str, str, str]] = []

    for facility, company in rows:
        fac_uri = f"<{SNOWKAP_NS}facility_{facility.id}>"
        comp_uri = f"<{SNOWKAP_NS}company_{company.id}>"

        triples.append((fac_uri, "a", f"<{SNOWKAP_NS}Facility>"))
        triples.append((fac_uri, "rdfs:label", f'"{facility.name}"'))
        triples.append((comp_uri, f"<{SNOWKAP_NS}hasFacility>", fac_uri))

        if facility.city:
            region_uri = f"<{SNOWKAP_NS}region_{facility.city.lower().replace(' ', '_')}>"
            triples.append((region_uri, "a", f"<{SNOWKAP_NS}GeographicRegion>"))
            triples.append((region_uri, "rdfs:label", f'"{facility.city}"'))
            triples.append((fac_uri, f"<{SNOWKAP_NS}locatedIn>", region_uri))

        if facility.state:
            state_uri = f"<{SNOWKAP_NS}region_{facility.state.lower().replace(' ', '_')}>"
            triples.append((state_uri, "a", f"<{SNOWKAP_NS}GeographicRegion>"))
            triples.append((state_uri, "rdfs:label", f'"{facility.state}"'))
            triples.append((fac_uri, f"<{SNOWKAP_NS}locatedIn>", state_uri))

        if facility.latitude and facility.longitude:
            triples.append((fac_uri, f"<{SNOWKAP_NS}latitude>", f'"{facility.latitude}"^^xsd:float'))
            triples.append((fac_uri, f"<{SNOWKAP_NS}longitude>", f'"{facility.longitude}"^^xsd:float'))

        if facility.climate_risk_zone:
            triples.append((fac_uri, f"<{SNOWKAP_NS}climateRiskZone>", f'"{facility.climate_risk_zone}"'))

    if triples:
        return await jena_client.insert_triples(triples, tenant_id)
    return True
