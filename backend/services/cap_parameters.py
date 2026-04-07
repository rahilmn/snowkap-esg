"""Centralized cap-driven calibration table for all intelligence modules."""

CAP_PARAMETERS = {
    "Large Cap": {
        "financial_impact_floor": 50,  # ₹ Cr
        "budget_range": "₹10-100 Cr",
        "budget_min": 10, "budget_max": 100,
        "investor_sensitivity": 1.5,
        "regulatory_scrutiny": 1.3,
        "timeline_compression": 1.0,
    },
    "Mid Cap": {
        "financial_impact_floor": 5,
        "budget_range": "₹1-10 Cr",
        "budget_min": 1, "budget_max": 10,
        "investor_sensitivity": 1.0,
        "regulatory_scrutiny": 1.0,
        "timeline_compression": 1.0,
    },
    "Small Cap": {
        "financial_impact_floor": 0.5,
        "budget_range": "₹10L-1 Cr",
        "budget_min": 0.1, "budget_max": 1,
        "investor_sensitivity": 0.7,
        "regulatory_scrutiny": 0.8,
        "timeline_compression": 0.8,
    },
}


def get_cap_params(market_cap: str | None) -> dict:
    """Get calibration parameters for a market cap category."""
    if not market_cap:
        return CAP_PARAMETERS["Mid Cap"]
    for key in CAP_PARAMETERS:
        if key.lower() in market_cap.lower():
            return CAP_PARAMETERS[key]
    return CAP_PARAMETERS["Mid Cap"]
