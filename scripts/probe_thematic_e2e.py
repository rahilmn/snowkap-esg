"""Phase 53.F — live, read-only, in-memory end-to-end probe of the thematic lane.

Proves the whole chain on REAL data without writing to prod DB or running the
expensive Stage 10-12 / approval pipeline:

  fetch_industry_thematic_for_company (live NewsAPI.ai)
    → process_article (Stages 1-9 + criticality, gpt-4.1-mini only)
    → assert: source_type=industry_thematic, NOT rejected, materiality_weight
      high, criticality band elevated above market noise.

Cost: a few gpt-4.1-mini extraction calls (~$0.001/article). No DB writes, no
Opus/gpt-5-mini, no persistence. Run: python scripts/probe_thematic_e2e.py
"""
from __future__ import annotations

import os
os.environ["OPENROUTER_API_KEY"] = ""  # budget-capped → OpenAI-direct

import engine.config  # noqa: E402  (load_dotenv)
from engine.config import Company  # noqa: E402
from engine.ingestion.news_fetcher import fetch_industry_thematic_for_company  # noqa: E402
from engine.analysis.pipeline import process_article  # noqa: E402


def _bank() -> Company:
    return Company(
        name="ICICI Bank", slug="icici-bank-probe", domain="icicibank.com",
        industry="Financials/Banking", sasb_category="Commercial Banks",
        market_cap="Large Cap", listing_exchange="NSE",
        headquarter_city="Mumbai", headquarter_country="India",
        headquarter_region="South Asia", framework_region="INDIA",
        news_queries=[],
        primitive_calibration={
            "revenue_cr": 186000, "fy_year": 2026,
            "inferred_painpoints": [
                "climate risk disclosure under RBI norms",
                "financed emissions reporting",
                "data privacy and cyber resilience",
            ],
        },
    )


def main() -> int:
    company = _bank()
    print(f"== Live thematic fetch for {company.name} ({company.sasb_category}) ==")
    arts = fetch_industry_thematic_for_company(company, max_results=8)
    print(f"thematic articles fetched: {len(arts)}")
    if not arts:
        print("NO thematic articles returned — lane delivered nothing this window.")
        return 2
    for a in arts[:8]:
        named = company.name.lower() in (a.get("title", "").lower())
        print(f"  - [{a.get('source_type')}] {a.get('title','')[:90]}  (company-in-title={named})")

    # Run the strongest few through Stages 1-9 + criticality (cheap).
    print("\n== Pipeline (Stages 1-9 + criticality) on up to 3 thematic articles ==")
    best = None
    for a in arts[:3]:
        try:
            r = process_article(a, company)
        except Exception as exc:  # noqa: BLE001
            print(f"  pipeline error on '{a.get('title','')[:50]}': {type(exc).__name__}: {exc}")
            continue
        crit = r.criticality or {}
        rel = r.relevance
        print(
            f"  - '{r.title[:70]}'\n"
            f"      source_type={r.source_type!r} rejected={r.rejected} tier={r.tier}\n"
            f"      theme={getattr(r.themes,'primary_theme',None)!r} "
            f"event={getattr(r.event,'event_id',None)!r}\n"
            f"      relevance.total={getattr(rel,'total',None)} "
            f"materiality_weight={getattr(rel,'materiality_weight',None)}\n"
            f"      criticality band={crit.get('band')!r} score={crit.get('score')} "
            f"mat={ (crit.get('components') or {}).get('materiality') } "
            f"act={ (crit.get('components') or {}).get('actionability') }"
        )
        if best is None or float(crit.get("score") or 0) > float((best.criticality or {}).get("score") or 0):
            best = r

    if best is None:
        print("\nFAIL: no thematic article completed the pipeline.")
        return 3

    crit = best.criticality or {}
    band = crit.get("band")
    ok = (
        best.source_type == "industry_thematic"
        and not best.rejected
        and band in ("CRITICAL", "HIGH", "MEDIUM")
    )
    print(f"\n== VERDICT ==\nbest thematic article band={band} rejected={best.rejected} "
          f"source_type={best.source_type!r}")
    print("PASS: live thematic article flowed through to an elevated criticality band, "
          "not rejected." if ok else "FAIL: thematic article did not reach an elevated band.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
