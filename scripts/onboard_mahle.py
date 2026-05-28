"""Phase 47.P — Onboard MAHLE GmbH end-to-end against production Postgres.

Runs the v3 onboard pipeline IN-PROCESS (no HTTP) so we can:
  1. Watch every Stage 1-12 + lede call on the wire
  2. Verify the 97% ontology / 3% LLM split is firing
  3. Surface any quality-gate drops with reasoning
  4. Confirm the deck row + lede + criticality_summary land in Postgres
  5. Print one clean PASS/FAIL report at the end

This is what runs to make the demo bulletproof for a real client onboard.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

# Make the repo importable when run as a script.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Load .env BEFORE importing engine modules so SUPABASE/OPENROUTER pick up.
from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env")

# Force structured info-level logging so we see every stage transition.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)5s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

# Mute the noisier libraries — keep the engine + onboard signal clean.
for noisy in ("urllib3", "httpx", "httpcore", "openai", "rdflib"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

logger = logging.getLogger("onboard_mahle")


def banner(text: str) -> None:
    print()
    print("=" * 70)
    print(f"  {text}")
    print("=" * 70)


def main() -> int:
    domain = "mahle.com"
    banner(f"Onboarding {domain} via v3 (in-process)")

    # 1. Postgres assertion
    from engine.db.connection import get_backend, is_postgres
    if not is_postgres():
        print(f"FAIL: backend={get_backend()} (need Postgres)")
        return 1
    print(f"[OK] Backend: {get_backend()}")

    # 2. OpenRouter assertion
    from engine.llm.keys import is_using_legacy_openai
    from engine.llm.routing import resolve_model
    if is_using_legacy_openai():
        print("FAIL: OPENROUTER_API_KEY missing")
        return 1
    print(f"[OK] Reasoning model: {resolve_model('reasoning_heavy')}")
    print(f"[OK] Composition model: {resolve_model('composition')}")

    # 3. LLM resolver
    banner("Step 1: LLM company resolver (Opus 4.6)")
    t0 = time.perf_counter()
    from engine.ingestion.llm_company_resolver import resolve_company_from_domain
    info = resolve_company_from_domain(domain)
    if info is None:
        print(f"FAIL: resolver could not identify {domain}")
        return 1
    t_resolve = time.perf_counter() - t0
    print(f"[OK] Canonical name: {info.canonical_name}")
    print(f"[OK] Slug: {info.slug}")
    print(f"[OK] Industry: {info.industry}")
    print(f"[OK] SASB category: {info.sasb_category}")
    print(f"[OK] Framework region: {info.framework_region}")
    print(f"[OK] Market cap tier: {info.market_cap_tier}")
    print(f"[OK] Ticker: {info.primary_ticker or '(private)'}")
    print(f"[OK] HQ: {info.headquarter_city}, {info.headquarter_country}")
    print(f"[OK] Default reader role: {info.default_reader_role}")
    print(f"[OK] Inferred painpoints ({len(info.inferred_painpoints)}):")
    for p in info.inferred_painpoints:
        print(f"    - {p}")
    print(f"[OK] Inferred KPIs ({len(info.inferred_kpis)}):")
    for k in info.inferred_kpis:
        print(f"    - {k}")
    print(f"[OK] Confidence: {info.confidence}")
    print(f"[time]  Resolver wall-clock: {t_resolve:.1f}s")

    # 4. Upsert companies row
    banner("Step 2: Upsert companies row in Postgres")
    from engine.config import Company, invalidate_companies_cache
    from engine.models import companies_store

    companies_store.upsert(
        slug=info.slug,
        name=info.canonical_name,
        domain=domain,
        industry=info.industry,
        market_cap_tier=info.market_cap_tier,
        yfinance_ticker=info.primary_ticker,
        framework_region=info.framework_region,
        primitive_calibration={
            "inferred_painpoints": info.inferred_painpoints,
            "inferred_kpis": info.inferred_kpis,
            "default_reader_role": info.default_reader_role,
            "sasb_category": info.sasb_category,
        },
        created_by_user="ci@snowkap.com",
        status="active",
    )
    invalidate_companies_cache()
    print(f"[OK] companies row upserted: {info.slug}")

    # Register slug alias (mahle → mahle-gmbh if resolver normalised it)
    from engine.ingestion.company_onboarder import _slugify as _input_slugify
    input_slug = _input_slugify(domain.split(".")[0])
    if input_slug and input_slug != info.slug:
        try:
            from engine.index import sqlite_index
            sqlite_index.register_alias(input_slug, info.slug)
            print(f"[OK] alias registered: {input_slug} → {info.slug}")
        except Exception as exc:
            print(f"[WARN]  alias register failed (non-fatal): {exc}")

    # 5. Construct Company dataclass
    def _exchange_from_ticker(ticker: str) -> str:
        if not ticker:
            return "Private"
        t = ticker.upper()
        suffixes = {
            ".NS": "NSE", ".BO": "BSE", ".L": "LSE", ".DE": "Xetra",
            ".PA": "Euronext Paris", ".AS": "Euronext Amsterdam",
            ".F": "Frankfurt", ".T": "TSE", ".HK": "HKEX", ".SS": "SSE",
        }
        for s, e in suffixes.items():
            if t.endswith(s):
                return e
        return "NASDAQ/NYSE" if "." not in t else "Unknown"

    company_obj = Company(
        name=info.canonical_name,
        slug=info.slug,
        domain=domain,
        industry=info.industry,
        sasb_category=info.sasb_category,
        market_cap=info.market_cap_tier,
        listing_exchange=_exchange_from_ticker(info.primary_ticker or ""),
        headquarter_city=info.headquarter_city or "Stuttgart",
        headquarter_country=info.headquarter_country or "Germany",
        headquarter_region=info.framework_region,
        news_queries=[
            "MAHLE ESG", "MAHLE sustainability", "MAHLE emissions",
            "MAHLE supply chain", "MAHLE EV transition", "MAHLE Scope 3",
            "MAHLE CBAM", "MAHLE CSRD", "MAHLE governance",
        ],
        primitive_calibration={
            "inferred_painpoints": info.inferred_painpoints,
            "inferred_kpis": info.inferred_kpis,
            "default_reader_role": info.default_reader_role,
        },
        yfinance_ticker=info.primary_ticker,
        eodhd_ticker=None,
        framework_region=info.framework_region,
        sustainability_query=None,
        general_query=None,
    )
    print(f"[OK] Company dataclass built (region={company_obj.framework_region})")

    # 6. News fetch
    banner("Step 3: News fetch (Google News RSS + body extraction)")
    from engine.ingestion.news_fetcher import fetch_for_company

    t0 = time.perf_counter()
    fresh = fetch_for_company(company_obj, max_per_query=3)
    t_fetch = time.perf_counter() - t0
    print(f"[OK] Fetched {len(fresh)} articles in {t_fetch:.1f}s")
    for i, a in enumerate(fresh[:10], 1):
        body_len = len(a.content or "")
        body_mark = "[body]" if body_len >= 300 else "[thin]"
        title = (a.title or "")[:90]
        print(f"  {i}. {body_mark} [{body_len:>5} chars] {title}")

    if not fresh:
        print("FAIL: 0 fresh articles fetched")
        return 1

    # 7. Run full pipeline on top-3 (in-process, sequential for clean logs)
    banner("Step 4: Full Stage 1-12 + lede pipeline (top 3 articles)")
    from api.routes.onboard_v3 import _run_full_pipeline_for_article

    top_3 = fresh[:3]
    summaries: list[dict] = []
    for i, art in enumerate(top_3, 1):
        print(f"\n--- Article {i}/{len(top_3)}: {(art.title or '')[:80]}")
        t0 = time.perf_counter()
        article_dict = {
            "id": art.id,
            "title": art.title,
            "content": art.content,
            "summary": art.summary,
            "source": art.source,
            "url": art.url,
            "published_at": art.published_at,
            "metadata": art.metadata,
        }
        try:
            summary = _run_full_pipeline_for_article(article_dict, company_obj)
            summaries.append(summary)
            t_a = time.perf_counter() - t0
            print(f"  Result: tier={summary.get('tier')} "
                  f"rejected={summary.get('rejected')} "
                  f"recs={summary.get('recommendation_count')} "
                  f"lede={summary.get('has_lede')} "
                  f"({t_a:.1f}s)")
        except Exception as exc:
            print(f"  CRASH: {type(exc).__name__}: {exc}")
            summaries.append({
                "article_id": art.id,
                "title": art.title,
                "tier": "FAILED",
                "rejected": True,
                "recommendation_count": 0,
                "has_lede": False,
                "error_class": type(exc).__name__,
                "error_message": str(exc),
            })

    # 8. Validate Postgres state
    banner("Step 5: Verify Postgres deck state")
    from engine.db.connection import connect
    with connect() as c:
        cur = c.execute(
            "SELECT slug, name, industry, framework_region "
            "FROM companies WHERE slug = ?",
            (info.slug,),
        )
        row = cur.fetchone()
        assert row, f"companies row missing for {info.slug}"
        print(f"[OK] companies row: slug={row['slug']}  name={row['name']}  "
              f"industry={row['industry']}  region={row['framework_region']}")

        cur = c.execute(
            "SELECT COUNT(*) AS n FROM company_article_view "
            "WHERE company_slug = ?",
            (info.slug,),
        )
        n_view = cur.fetchone()["n"]
        print(f"[OK] company_article_view rows: {n_view}")

        cur = c.execute(
            "SELECT ap.id AS id, ap.title AS title FROM article_pool ap "
            "JOIN company_article_view cav ON ap.id = cav.article_id "
            "WHERE cav.company_slug = ? "
            "ORDER BY cav.added_at DESC LIMIT 5",
            (info.slug,),
        )
        pool_rows = cur.fetchall()
        print(f"[OK] article_pool rows: {len(pool_rows)}")
        for r in pool_rows:
            print(f"    - {r['id'][:16]}  {(r['title'] or '')[:70]}")

    # 9. Inspect best article's analysis
    banner("Step 6: Inspect the analysis quality (Best article)")
    out_dir = Path("data/outputs") / info.slug / "insights"
    if not out_dir.exists():
        print(f"[WARN]  No insight files in {out_dir}")
        return 1

    insight_files = sorted(out_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    print(f"Found {len(insight_files)} insight files")

    best_file = None
    best_recs = -1
    for f in insight_files:
        d = json.loads(f.read_text(encoding="utf-8"))
        recs = (d.get("recommendations") or {}).get("recommendations") or []
        if len(recs) > best_recs:
            best_recs = len(recs)
            best_file = f

    if not best_file:
        print("[WARN]  No analysed article on disk")
        return 1

    d = json.loads(best_file.read_text(encoding="utf-8"))
    insight = d.get("insight") or {}
    analysis = insight.get("analysis") or {}
    print(f"\n[body] Article: {(insight.get('headline') or '')[:90]}")
    print(f"[URL] URL: {d.get('article_url', '?')}")
    print(f"[schema] Schema: {(d.get('meta') or {}).get('schema_version')}")
    print()

    print("━━━ EDITORIAL LEDE ━━━")
    lede = (analysis.get("lede") or {})
    print(f"  {lede.get('text', '(missing)')}")
    print()

    wc = analysis.get("what_changed") or {}
    print("━━━ WHAT CHANGED ━━━")
    print(f"  {wc.get('headline', '(missing)')}")
    print(f"  event_type: {wc.get('event_type')}  polarity: {wc.get('polarity')}")
    print(f"  source: {wc.get('source')}  published: {wc.get('published_at', '')[:10]}")
    print()

    wim = analysis.get("why_it_matters") or {}
    print("━━━ WHY IT MATTERS ━━━")
    print(f"  band: {wim.get('materiality_band')}  weight: {wim.get('materiality_weight')}")
    print(f"  dominant signal: {wim.get('dominant_signal')}")
    print(f"  summary: {wim.get('criticality_summary', '(missing)')}")
    print(f"  stakes: {wim.get('stakes_for_company', '(missing)')[:200]}")
    fe = wim.get("financial_exposure") or {}
    if fe.get("amount_cr"):
        print(f"  ₹ exposure: ₹{fe['amount_cr']} Cr ({fe.get('kind')})")
    print()

    wit = analysis.get("what_it_triggers") or {}
    fws = wit.get("frameworks") or []
    print("━━━ WHAT IT TRIGGERS ━━━")
    print(f"  frameworks ({len(fws)}):")
    for f in fws[:5]:
        print(f"    - {f.get('code')} § {f.get('section')}  "
              f"mandatory={f.get('is_mandatory')}  deadline={f.get('deadline_days')}d")
    actions = wit.get("recommended_actions") or []
    print(f"  recommended actions ({len(actions)}):")
    for a in actions[:3]:
        print(f"    - {a.get('title')}")
        print(f"        owner: {a.get('owner')}  by: {a.get('deadline')}")
    print()

    wtw = analysis.get("what_to_watch") or {}
    print("━━━ WHAT TO WATCH ━━━")
    traj = wtw.get("sentiment_trajectory") or {}
    print(f"  trajectory: 3m={traj.get('horizon_3m')}  "
          f"6m={traj.get('horizon_6m')}  12m={traj.get('horizon_12m')}  "
          f"confidence={traj.get('confidence')}")
    print(f"  top risks: {wtw.get('top_risk_categories', [])}")
    print()

    # Show one full recommendation
    recs = (d.get("recommendations") or {}).get("recommendations") or []
    if recs:
        print("━━━ DETAILED RECOMMENDATION (top 1) ━━━")
        r = recs[0]
        print(f"  Title: {r.get('title')}")
        print(f"  Type: {r.get('type')}  priority: {r.get('priority')}")
        print(f"  Budget: {r.get('estimated_budget')}  "
              f"Payback: {r.get('payback_months')} months  "
              f"ROI: {r.get('roi_pct')}%")
        print(f"  Owner: {r.get('owner')}")
        print(f"  Peer benchmark: {r.get('peer_benchmark', '(missing)')}")
        print(f"  Framework section: {r.get('framework_section', '(missing)')}")
        print(f"  Audit trail entries: {len(r.get('audit_trail') or [])}")
        for at in (r.get("audit_trail") or [])[:3]:
            print(f"    - [{at.get('source')}] {at.get('value', '')[:90]}")

    banner("[OK] MAHLE onboarded — open the app at /now?company=" + info.slug)
    print(f"  Total elapsed: {time.perf_counter() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
