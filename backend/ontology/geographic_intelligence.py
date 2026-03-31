"""Geographic Intelligence — proximity matching for news impact.

Per MASTER_BUILD_PLAN Phase 3.3:
- Company → facility locations (lat/lng, district, state)
- News → location extraction
- Proximity matching: "water scarcity in Kolhapur" → "you have a plant in Kolhapur"
- Climate/disaster risk zones per geography

Stage 2.3: Wire haversine_distance() into entity matching. Add international zones.
Add risk types: water_stress, air_quality, landslide, coastal_erosion.
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

# Climate risk zones — India + international (Stage 2.3)
CLIMATE_RISK_ZONES: dict[str, list[str]] = {
    # India
    "coastal_flood": [
        "mumbai", "chennai", "kolkata", "kochi", "visakhapatnam", "surat",
        "mangalore", "goa", "puducherry",
    ],
    "drought_prone": [
        "marathwada", "vidarbha", "bundelkhand", "rayalaseema", "kutch",
        "anantapur", "kurnool", "barmer", "baran",
    ],
    "cyclone_belt": [
        "odisha", "andhra pradesh", "tamil nadu", "west bengal", "gujarat",
    ],
    "heat_stress": [
        "rajasthan", "madhya pradesh", "telangana", "chhattisgarh", "vidarbha",
        "nagpur", "jaisalmer", "kutch", "mundra", "gondia", "barmer", "baran",
    ],
    "flood_prone": [
        "assam", "bihar", "uttar pradesh", "kerala", "karnataka",
        "brahmaputra", "ganga",
    ],
    "seismic": ["himalayan belt", "kutch", "northeast india", "uttarakhand"],
    "industrial_pollution": [
        "delhi", "kanpur", "ludhiana", "vapi", "ankleshwar",
        "noida", "ghaziabad", "faridabad", "surat",
    ],
    # Stage 2.3: New risk types
    "water_stress": [
        "chennai", "bangalore", "hyderabad", "jaipur", "cape town",
        "mexico city", "sao paulo", "karachi", "lima", "cairo",
        "delhi", "pune", "ahmedabad",
    ],
    "air_quality": [
        "delhi", "lahore", "dhaka", "beijing", "mumbai", "kolkata",
        "kanpur", "lucknow", "patna", "gurgaon",
    ],
    "landslide": [
        "uttarakhand", "himachal pradesh", "sikkim", "meghalaya",
        "nilgiris", "kodagu", "wayanad",
    ],
    "coastal_erosion": [
        "sundarbans", "kochi", "mumbai", "chennai", "goa",
        "alibag", "ratnagiri",
    ],
    # International zones
    "typhoon_belt": [
        "philippines", "taiwan", "japan", "vietnam", "hong kong",
        "guangdong", "fujian",
    ],
    "wildfire_zone": [
        "california", "australia", "portugal", "greece", "turkey",
        "british columbia", "amazon",
    ],
    "permafrost_thaw": [
        "siberia", "alaska", "northern canada", "greenland", "scandinavia",
    ],
    "sea_level_rise": [
        "maldives", "tuvalu", "bangladesh", "netherlands", "jakarta",
        "miami", "venice", "shanghai",
    ],
    "desertification": [
        "sahel", "gobi", "thar", "kalahari", "patagonia",
    ],
}


@dataclass
class GeoMatch:
    """A geographic proximity match between news location and company facility."""
    facility_id: str
    facility_name: str
    company_id: str
    company_name: str
    matched_location: str
    match_type: str  # exact_city, exact_district, exact_state, proximity, haversine
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


# Known city coordinates for haversine matching when no lat/lng on facility
CITY_COORDINATES: dict[str, tuple[float, float]] = {
    "mumbai": (19.076, 72.8777), "delhi": (28.6139, 77.209),
    "bangalore": (12.9716, 77.5946), "hyderabad": (17.385, 78.4867),
    "chennai": (13.0827, 80.2707), "kolkata": (22.5726, 88.3639),
    "pune": (18.5204, 73.8567), "ahmedabad": (23.0225, 72.5714),
    "jaipur": (26.9124, 75.7873), "surat": (21.1702, 72.8311),
    "lucknow": (26.8467, 80.9462), "kanpur": (26.4499, 80.3319),
    "nagpur": (21.1458, 79.0882), "kochi": (9.9312, 76.2673),
    "visakhapatnam": (17.6868, 83.2185), "goa": (15.2993, 74.124),
    # International
    "singapore": (1.3521, 103.8198), "dubai": (25.2048, 55.2708),
    "london": (51.5074, -0.1278), "new york": (40.7128, -74.006),
    "tokyo": (35.6762, 139.6503), "shanghai": (31.2304, 121.4737),
    "hong kong": (22.3193, 114.1694), "sydney": (-33.8688, 151.2093),
    "cape town": (-33.9249, 18.4241), "sao paulo": (-23.5505, -46.6333),
    # Beta company facility locations
    "mundra": (22.8333, 69.7167), "vijayanagar": (15.4289, 76.6172),
    "ratnagiri": (16.9944, 73.3), "barmer": (25.7522, 71.3967),
    "gondia": (21.4559, 80.1962), "baran": (25.1, 76.5167),
    "tumb": (20.5, 72.7), "chikhli": (20.7581, 73.0614),
    "salboni": (22.35, 87.05), "udupi": (13.3409, 74.7421),
    "beaverton": (45.4871, -122.8037), "laakdal": (51.0833, 4.9833),
    "gurugram": (28.4595, 77.0266),
}


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
    4. Haversine proximity within threshold (Stage 2.3: now wired)
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
            distance = None

            # Exact city match
            if facility.city and location in facility.city.lower():
                match_type = "exact_city"
            # Exact district match
            elif facility.district and location in facility.district.lower():
                match_type = "exact_district"
            # Exact state match
            elif facility.state and location in facility.state.lower():
                match_type = "exact_state"
            # Stage 2.3: Haversine proximity check
            elif facility.latitude and facility.longitude:
                # Get coordinates for the news location
                loc_coords = CITY_COORDINATES.get(location)
                if loc_coords:
                    distance = haversine_distance(
                        loc_coords[0], loc_coords[1],
                        float(facility.latitude), float(facility.longitude),
                    )
                    if distance <= proximity_km:
                        match_type = "haversine"

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
                    distance_km=round(distance, 1) if distance else None,
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
        # Reverse edge: facility belongs to company (enables BFS from facility to company)
        triples.append((fac_uri, f"<{SNOWKAP_NS}belongsToCompany>", comp_uri))

        if facility.city:
            region_uri = f"<{SNOWKAP_NS}region_{facility.city.lower().replace(' ', '_')}>"
            triples.append((region_uri, "a", f"<{SNOWKAP_NS}GeographicRegion>"))
            triples.append((region_uri, "rdfs:label", f'"{facility.city}"'))
            triples.append((fac_uri, f"<{SNOWKAP_NS}locatedIn>", region_uri))
            # Reverse: region has facility (enables BFS from region to company)
            triples.append((region_uri, f"<{SNOWKAP_NS}hasFacilityIn>", fac_uri))

            # Seed climate risks for the city
            risks = _get_climate_risks(facility.city)
            for risk in risks:
                triples.append((region_uri, f"<{SNOWKAP_NS}climateRiskZone>", f'"{risk}"'))

        if facility.state:
            state_uri = f"<{SNOWKAP_NS}region_{facility.state.lower().replace(' ', '_')}>"
            triples.append((state_uri, "a", f"<{SNOWKAP_NS}GeographicRegion>"))
            triples.append((state_uri, "rdfs:label", f'"{facility.state}"'))
            triples.append((fac_uri, f"<{SNOWKAP_NS}locatedIn>", state_uri))

        if facility.latitude and facility.longitude:
            triples.append((fac_uri, f"<{SNOWKAP_NS}latitude>", f'"{facility.latitude}"^^<http://www.w3.org/2001/XMLSchema#float>'))
            triples.append((fac_uri, f"<{SNOWKAP_NS}longitude>", f'"{facility.longitude}"^^<http://www.w3.org/2001/XMLSchema#float>'))

        if facility.climate_risk_zone:
            triples.append((fac_uri, f"<{SNOWKAP_NS}climateRiskZone>", f'"{facility.climate_risk_zone}"'))

    if triples:
        return await jena_client.insert_triples(triples, tenant_id)
    return True
