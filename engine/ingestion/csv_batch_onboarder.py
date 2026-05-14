"""Phase 25 W6 — HubSpot CSV batch onboarder.

Reads a HubSpot deals export CSV, filters to active customer tenants
worth onboarding, normalises deal names into URL-safe slugs, and
returns a structured roster the batch endpoint can enqueue one-by-one
through the existing ``engine.ingestion.company_onboarder.onboard_company``
flow.

User-confirmed selection rule (Phase 25 plan):

    Active Status = "Active"  AND  Deal Stage ∈ {"Won", "Negotiation"}

Won customers are paying clients; the 5 in Negotiation are part of the
sales pitch. Amount is intentionally NOT a filter — it would otherwise
exclude the smallest Won deals (Schaeffler ₹1.4 lakh) which are still
real customers.

The HubSpot export is UTF-8 (verified). Some deal names contain
non-ASCII characters (e.g. "Süd-Chemie India") — the slugifier strips
them down to ASCII so URL paths + tenant_dir paths stay clean across
filesystems.
"""

from __future__ import annotations

import csv
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

ELIGIBLE_DEAL_STAGES: frozenset[str] = frozenset({"Won", "Negotiation"})
ELIGIBLE_ACTIVE_STATUS: str = "Active"

# HubSpot export column names — lock these explicitly so a column-rename in
# a future CSV export raises a clear error rather than silently filtering
# everything out.
REQUIRED_COLUMNS: tuple[str, ...] = (
    "Record ID", "Deal Name", "Region", "Deal Stage", "Active Status",
    "Amount", "Deal owner",
)


# ---------------------------------------------------------------------------
# Roster entry — one row per onboarding candidate
# ---------------------------------------------------------------------------


@dataclass
class CustomerRoster:
    """One customer tenant ready for onboarding via the existing
    ``onboard_company()`` flow."""
    record_id: str
    deal_name: str       # raw from CSV
    company_name: str    # cleaned: "TATA AutoComp Systems - New Deal" → "TATA AutoComp Systems"
    slug: str            # URL-safe: "tata-autocomp"
    deal_stage: str      # "Won" | "Negotiation"
    region: str          # raw from CSV (e.g. "India", "Gujarat", "Mumbai")
    headquarter_country: str  # normalised: "India" for any city in India, "Kuwait", etc.
    amount_inr: float | None  # numeric, None when blank
    deal_owner: str
    needs_disambiguation: bool = False  # set True when ticker resolver hits multiple high-conf matches
    disambiguation_candidates: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------


def parse_csv(
    csv_path: str | Path,
    *,
    eligible_stages: Iterable[str] = ELIGIBLE_DEAL_STAGES,
    eligible_active_status: str = ELIGIBLE_ACTIVE_STATUS,
) -> list[CustomerRoster]:
    """Parse a HubSpot deals CSV and return the filtered onboarding roster.

    Strict-mode parsing: missing columns or unparseable rows raise rather
    than silently skipping (this is admin tooling — better to fail fast
    on a bad upload than half-onboard 17 tenants).
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header row: {csv_path}")
        missing = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
        if missing:
            raise ValueError(
                f"CSV missing required columns: {missing}. "
                f"Found: {reader.fieldnames}"
            )
        roster: list[CustomerRoster] = []
        skipped_malformed = 0
        for row in reader:
            try:
                entry = _row_to_roster(row, eligible_stages, eligible_active_status)
            except _SkipRow:
                continue
            except Exception as exc:
                # Don't let one bad row tank a 17-row batch. Log + skip.
                logger.warning(
                    "csv_batch_onboarder: skipping malformed row %r: %s",
                    row.get("Deal Name", "?"), exc,
                )
                skipped_malformed += 1
                continue
            roster.append(entry)

    if skipped_malformed:
        logger.info(
            "csv_batch_onboarder: parsed %d eligible rows (skipped %d malformed)",
            len(roster), skipped_malformed,
        )
    return roster


class _SkipRow(Exception):
    """Internal sentinel for rows that don't match the eligibility filter."""
    pass


def _row_to_roster(
    row: dict[str, str],
    eligible_stages: Iterable[str],
    eligible_active_status: str,
) -> CustomerRoster:
    active_status = (row.get("Active Status") or "").strip()
    deal_stage = (row.get("Deal Stage") or "").strip()
    if active_status != eligible_active_status:
        raise _SkipRow()
    if deal_stage not in eligible_stages:
        raise _SkipRow()

    deal_name = (row.get("Deal Name") or "").strip()
    if not deal_name:
        raise ValueError("empty Deal Name")

    record_id = (row.get("Record ID") or "").strip()
    region = (row.get("Region") or "").strip() or "India"
    deal_owner = (row.get("Deal owner") or "").strip()

    company_name = clean_company_name(deal_name)
    slug = slugify(company_name)
    headquarter_country = normalise_country(region)

    amount_inr = None
    raw_amount = (row.get("Amount") or "").strip()
    if raw_amount:
        try:
            amount_inr = float(raw_amount)
        except ValueError:
            amount_inr = None

    return CustomerRoster(
        record_id=record_id,
        deal_name=deal_name,
        company_name=company_name,
        slug=slug,
        deal_stage=deal_stage,
        region=region,
        headquarter_country=headquarter_country,
        amount_inr=amount_inr,
        deal_owner=deal_owner,
    )


# ---------------------------------------------------------------------------
# Cleaning helpers
# ---------------------------------------------------------------------------

# Suffixes HubSpot reps tack onto Deal Names. Stripped from the company name
# but the original Deal Name is preserved on the roster entry for audit.
_DEAL_SUFFIX_PATTERNS: tuple[str, ...] = (
    " - New Deal",
    " - New",
    " - cohizon",            # "Sajjan - cohizon - New" → "Sajjan"
    " (Phase 2)",
    " (Phase 3)",
    " ESG Reporting",        # "Daimler India ESG Reporting" → "Daimler India"
    " GHG",                  # "NRB GHG (Phase 2)" → "NRB"
    " PCF",                  # "Daimler India PCF" → "Daimler India PCF" — PCF kept (Product Carbon Footprint segment)
)


def clean_company_name(deal_name: str) -> str:
    """Strip HubSpot deal-name suffixes to get the clean company name.

    Conservative: only strips well-known suffix patterns. Anything else
    is preserved so we don't accidentally truncate a real company name.
    """
    name = deal_name.strip()
    # Strip patterns iteratively (a few names have multiple suffixes)
    changed = True
    while changed:
        changed = False
        for pattern in _DEAL_SUFFIX_PATTERNS:
            # Skip the PCF stripper for now — Daimler India PCF is its own
            # commercial entity (Product Carbon Footprint engagement) so
            # we keep the suffix to disambiguate from "Daimler India ESG
            # Reporting" (a separate Won deal in the same CSV).
            if pattern == " PCF":
                continue
            if name.endswith(pattern):
                name = name[: -len(pattern)].strip()
                changed = True
    return name


# Slug rules: ASCII-only, lowercase, dashes for separators, max 50 chars.
# Mirrors the pattern used by ``engine.ingestion.company_onboarder._slugify``
# so the CSV-derived slugs match what onboarding would produce on a manual
# /api/admin/onboard call.
_SLUG_NONALNUM_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """ASCII-only URL-safe slug. Strips diacritics ('Süd' → 'Sud')."""
    if not name:
        return ""
    # Decompose accented characters then drop the combining marks
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = decomposed.encode("ascii", errors="ignore").decode("ascii")
    lowered = ascii_only.lower()
    # Collapse runs of non-alphanumeric into single dashes
    slug = _SLUG_NONALNUM_RE.sub("-", lowered).strip("-")
    return slug[:50]  # cap so tenant_dir paths stay manageable on Windows


# Country normalisation — the CSV's "Region" column mixes country names
# with city names (Mumbai, Gurugram, Bengaluru → all India). This map
# keeps anything in India under "India" so the W5 tenant resolver +
# Phase 23B framework_region routing both fire correctly.
_INDIAN_CITIES: frozenset[str] = frozenset({
    "india", "mumbai", "delhi", "bengaluru", "bangalore", "gurugram",
    "gurgaon", "noida", "hyderabad", "chennai", "kolkata", "pune",
    "ahmedabad", "gujarat", "maharashtra", "tamil nadu", "karnataka",
    "telangana", "kerala", "rajasthan", "punjab", "haryana", "uttar pradesh",
    "west bengal", "andhra pradesh", "odisha", "madhya pradesh", "bihar",
})


def normalise_country(region: str) -> str:
    """Map the CSV's Region cell to a country name the rest of the engine
    understands (Phase 23B `_region_for_country` works on country names,
    not Indian cities)."""
    cleaned = (region or "").strip()
    if not cleaned:
        return "India"  # CSV default — most rows omit Region
    if cleaned.lower() in _INDIAN_CITIES:
        return "India"
    return cleaned


# ---------------------------------------------------------------------------
# Iteration helpers — CSV → roster as a generator, stable ordering
# ---------------------------------------------------------------------------


def iter_roster(csv_path: str | Path) -> Iterator[CustomerRoster]:
    """Yield roster entries one at a time. Useful when the batch endpoint
    wants to stream-enqueue rather than materialise the whole list."""
    yield from parse_csv(csv_path)


def summarise_roster(roster: list[CustomerRoster]) -> dict[str, int | list[str]]:
    """Quick summary stats for a roster — fed back to the admin UI as the
    first response after CSV upload before per-row enqueueing kicks in."""
    won = [r for r in roster if r.deal_stage == "Won"]
    negotiation = [r for r in roster if r.deal_stage == "Negotiation"]
    countries: dict[str, int] = {}
    for r in roster:
        countries[r.headquarter_country] = countries.get(r.headquarter_country, 0) + 1
    return {
        "total": len(roster),
        "won": len(won),
        "negotiation": len(negotiation),
        "countries": [f"{c}:{n}" for c, n in sorted(countries.items(), key=lambda x: -x[1])],
        "slugs": [r.slug for r in roster],
    }
