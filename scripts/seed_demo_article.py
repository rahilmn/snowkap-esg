"""Seed a high-quality demo article for Reliance Industries that exercises
the full intelligence stack end-to-end and produces 10/10 perspectives
across ESG Analyst, CFO and CEO lenses.

Usage:
    python scripts/seed_demo_article.py [--dry-run]

What it does:
1. Builds a rich, realistic article body (~900 words) about Reliance's
   green hydrogen + ammonia mega-capex with all the right hooks:
     - ₹ amount (capex), supply chain (Saudi Aramco), workforce (Jamnagar),
       regulatory refs (BRSR P9, MNRE, Paris Article 6, EU Taxonomy, ISSB),
       decarbonisation metrics (MTPA, Mt CO2e), competitive context.
2. Writes input JSON under data/inputs/news/reliance-industries-limited/.
3. Removes any prior cached output for the same article id.
4. Runs the full 12-stage pipeline (process_article → insight → 3
   perspectives → recommendations → write_insight) which also upserts
   the SQLite article_index row.
5. Prints a summary of tier, scores, recommendations, and per-perspective
   crisp output for verification.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.config import get_company, get_data_path  # noqa: E402
from engine.ingestion.news_fetcher import IngestedArticle, _write_article, _url_hash  # noqa: E402

DEMO_TITLE = (
    "Reliance Commits ₹75,000 Cr to Green Hydrogen and Ammonia Mega-Hub at "
    "Jamnagar; Inks 20-Year Offtake with Saudi Aramco and Triggers BRSR P9 "
    "Disclosure Refresh"
)

DEMO_BODY = """\
MUMBAI, April 28 2026 — Reliance Industries Limited (NSE: RELIANCE) today
announced a final investment decision of ₹75,000 crore (US$ 9.0 billion)
to build the world's largest integrated green hydrogen and green ammonia
production complex at its Jamnagar refining hub, marking the single
largest decarbonisation capex commitment by an Indian conglomerate. The
project, branded "Surya-1", will deliver 1.2 million tonnes per annum
(MTPA) of green ammonia and 200 kilotonnes of green hydrogen by FY30,
abating an estimated 4.6 million tonnes of CO2-equivalent emissions per
year across Reliance's Scope 1, Scope 2 and Scope 3 supplier value chain
against the company's 2024 baseline. The project will create 28,000
direct construction worker positions and 3,200 permanent employee roles
in the Jamnagar community, with binding workforce safety commitments
under ISO 45001 and a Tier 1 supplier code refresh covering 1,200
upstream and downstream vendors across the green ammonia supply chain.

Chairman and Managing Director Mukesh Ambani disclosed the commitment at
Reliance's Capital Markets Day in Mumbai. "Surya-1 is our largest single
energy-transition investment to date and structurally repositions
Reliance from a hydrocarbon refiner to a vertically integrated low-carbon
molecule exporter," Ambani said. The capex will be funded through a
combination of internal accruals (₹40,000 Cr), a green bond issuance
under the EU Taxonomy / ICMA Green Bond Principles aligned framework
(₹25,000 Cr) and a strategic equity infusion from sovereign wealth
partners (₹10,000 Cr). Citi and HSBC have been mandated as joint global
coordinators for the green bond, expected to price in Q2 FY27.

Alongside the announcement, Reliance signed a binding 20-year offtake
agreement with Saudi Aramco for 1.0 MTPA of green ammonia, valued at
approximately US$ 18 billion over the contract term at indexed pricing.
The ammonia will be shipped from a dedicated jetty at Sikka port to
Aramco's blue/green ammonia blending facility at Yanbu, where it will
substitute grey ammonia feedstock in fertiliser, refining hydrotreating
and bunker fuel applications. Saudi Aramco president Amin H. Nasser said
the partnership "anchors a transcontinental low-carbon molecule corridor
between India and the Kingdom and validates Aramco's 2050 net-zero
roadmap." A second non-binding term sheet was signed with German
chemicals major BASF SE for 250 kilotonnes per annum of green ammonia
into the Ludwigshafen Verbund.

The Jamnagar hub will be powered by a 6.5 GW captive renewable energy
park (4.0 GW solar + 2.5 GW wind + 8 GWh battery storage) being built
in Kutch district, Gujarat, with a separate ₹35,000 Cr capex envelope
already partially deployed under Reliance New Energy Limited (RNEL).
Engineering, procurement and construction (EPC) contracts have been
awarded to L&T Energy GreenTech for the electrolyser island and to
Larsen & Toubro / Toyo Engineering JV for the ammonia synthesis loop.
Cumulative direct employment during construction is estimated at 28,000
worker positions in Jamnagar, Sikka and Kutch districts, with 3,200
permanent employee O&M roles thereafter, of which 30% are reserved for
local Gujarat-domicile candidates under the company's social value
framework. The community impact envelope includes ₹450 Cr of CSR
commitments to host community skilling, women-led FPO partnerships,
and worker safety training. Reliance has also committed to a Tier 1
and Tier 2 supplier engagement programme covering 1,200 vendors with
mandatory Scope 3 emissions disclosure under the GHG Protocol Corporate
Value Chain Standard, and a supplier code of conduct refresh aligned
with the UN Guiding Principles on Business and Human Rights.

Regulatory disclosures and ESG implications. The announcement triggers
a refresh of Reliance's Business Responsibility and Sustainability
Report (BRSR) Principle 9 (consumer responsibility) and Principle 6
(environmental stewardship) disclosures and adds three new KPIs to the
mandatory BRSR Core assurance scope under SEBI's Top 1000 listed entity
regime. The project has been notified to the Ministry of New and
Renewable Energy (MNRE) for Strategic Interventions for Green Hydrogen
Transition (SIGHT) Mode 2A incentives, expected to crystallise ₹3,200
Cr of viability gap funding over five years. Reliance has also
voluntarily committed to ISSB IFRS S2 climate-related disclosures from
FY27 with limited assurance under ISAE 3410, two years ahead of the
likely Indian mandate, and to Paris Agreement Article 6.4 (PACM)
methodology for the cross-border carbon attribute of the Aramco
contract. The European Bank for Reconstruction and Development (EBRD)
and IFC are conducting a joint Environmental and Social Impact
Assessment (ESIA) per IFC Performance Standards 1-8 for the green bond
investor base, with public consultation scheduled for May 2026.

Financial framing and analyst reaction. The board approved an
incremental capex of ₹15,000 Cr in FY27 (versus the prior ₹1.22 lakh
crore consolidated capex guidance), with the balance phased FY28-FY30.
Reliance disclosed a project IRR range of 13.5%-15.0% (real, post-tax)
at the contracted ammonia price floor, with a payback of 8.4 years
unlevered. JPMorgan analyst Pinakin Parekh upgraded the stock to
Overweight with a ₹3,150 target (prior ₹2,750), citing "first-mover
margin in low-carbon molecules and a credible path to ESG-fund
re-rating." Macquarie estimates a 110-140 basis point uplift to
Reliance's ESG score with MSCI and Sustainalytics on next refresh,
which could lower the company's blended cost of debt by 35-50 bps on
fresh issuances. The stock closed up 4.7% at ₹2,883 on the BSE,
adding ₹91,000 Cr to market capitalisation.

Risk and execution flags. Independent analysts flagged execution risk
around electrolyser supply (global stack capacity is constrained
through 2027), sea-freight ammonia toxicity protocols (IMO 2030
ammonia bunker code is still in draft), and Indian green hydrogen
levelised cost competitiveness (currently US$ 4.2/kg vs. an Aramco
contract price implied at ~US$ 3.6/kg by 2028). Reliance has hedged
the electrolyser exposure via a 4 GW manufacturing JV with China's
LONGi (announced 2024) and is engaging with the Directorate General of
Shipping on bunker safety standards. Greenpeace India and Climate Risk
Horizons issued cautious-positive notes welcoming the scale but
calling for full lifecycle (well-to-wake) GHG accounting under the
GHG Protocol Scope 3 Category 11 and independent verification of the
captive renewables additionality claim.

Reliance's energy transition pivot now represents committed capex of
approximately ₹1.85 lakh crore through FY30 (across solar, batteries,
electrolysers, and now Surya-1), or 28% of consolidated capex envelope,
making it the largest corporate decarbonisation programme in the
emerging-market universe.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed Reliance demo article")
    parser.add_argument("--dry-run", action="store_true", help="Print plan; do not run pipeline")
    args = parser.parse_args(argv)

    company = get_company("reliance-industries-limited")
    print(f"Company: {company.name} ({company.industry}, {company.market_cap})")

    # Stable URL → stable id (overwrites any prior demo seed without polluting list)
    url = "https://demo.snowkap.local/reliance/2026-04-28/surya-1-green-hydrogen-ammonia-jamnagar"
    article_id = _url_hash(url)
    published_at = "2026-04-28T09:00:00+00:00"
    print(f"Article id : {article_id}")
    print(f"Title      : {DEMO_TITLE[:90]}…")
    print(f"Body length: {len(DEMO_BODY)} chars")

    if args.dry_run:
        print("[dry-run] stopping before write/pipeline")
        return 0

    # 1. Drop input JSON (the same shape the news_fetcher writes)
    article = IngestedArticle(
        id=article_id,
        title=DEMO_TITLE,
        content=DEMO_BODY,
        summary=DEMO_BODY[:400],
        source="Snowkap Demo Wire",
        url=url,
        published_at=published_at,
        company_slug=company.slug,
        source_type="prompt",
        metadata={
            "kind": "demo",
            "demo_topic": "green_hydrogen_ammonia_capex",
            "demo_owner": "snowkap",
        },
    )
    input_path = _write_article(article)
    print(f"Input JSON : {input_path}")

    # 2. Wipe any prior cached output for this id so the run is clean
    out_root = get_data_path("outputs", company.slug)
    removed = 0
    if out_root.exists():
        for path in out_root.rglob(f"*{article_id}*"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    print(f"Cleared {removed} stale output file(s) for this article")

    # 3. Run the full pipeline using the same path the engine CLI uses
    from engine.main import _run_article  # noqa: WPS433 — local import for fast --help

    article_dict = {
        "id": article.id,
        "title": article.title,
        "content": article.content,
        "summary": article.summary,
        "source": article.source,
        "url": article.url,
        "published_at": article.published_at,
        "metadata": article.metadata,
    }
    print("\n--- Running pipeline (this calls OpenAI; ~30-60s) ---")
    summary = _run_article(article_dict, company)
    print()
    print(f"Tier            : {summary.tier}")
    print(f"Rejected        : {summary.rejected}")
    print(f"Impact score    : {summary.impact_score}")
    print(f"Recommendations : {summary.recommendations}")
    print(f"Ontology queries: {summary.ontology_queries}")
    print(f"Files written   : {summary.files_written}")
    print(f"Elapsed         : {summary.elapsed_seconds}s")

    if summary.tier != "HOME":
        print("\n[WARN] Demo article did not reach HOME tier — open the JSON to inspect.")
        return 1

    # 4. Inspect the perspectives
    insight_json_path = out_root / "insights" / f"{published_at[:10]}_{article_id}.json"
    if not insight_json_path.exists():
        print(f"[ERROR] insight json missing at {insight_json_path}")
        return 2
    payload = json.loads(insight_json_path.read_text(encoding="utf-8"))
    insight = payload.get("insight") or {}
    print()
    print("Headline    :", insight.get("headline"))
    print("Mechanism   :", (insight.get("core_mechanism") or "")[:200], "…")
    print("Net impact  :", (insight.get("net_impact_summary") or "")[:200], "…")

    print("\n--- Perspectives ---")
    for lens, crisp in (payload.get("perspectives") or {}).items():
        print(f"\n[{lens.upper()}] {crisp.get('headline')}")
        for k, v in (crisp.get("impact_grid") or {}).items():
            print(f"   grid {k}: {v}")
        print("   What matters:")
        for b in (crisp.get("what_matters") or [])[:3]:
            print(f"     - {b}")
        print("   Action:")
        for b in (crisp.get("action") or [])[:3]:
            print(f"     - {b}")
        print(f"   Materiality: {crisp.get('materiality')}")

    print("\n--- Recommendations ---")
    recs = (payload.get("recommendations") or {}).get("recommendations") or []
    for i, r in enumerate(recs, 1):
        print(f"  {i}. [{r.get('urgency'):11s}] {r.get('title')}")
        print(f"      type={r.get('type')} impact={r.get('estimated_impact')} "
              f"roi={r.get('roi_percentage')}% payback={r.get('payback_months')}mo")

    print(f"\n[OK] Demo article ready: id={article_id}")
    print(f"     Published: {published_at[:10]}")
    print(f"     Open in app: /article/{article_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
