"""Demo deck rebuild — the weekly-cron mechanism, on Sonnet, against prod.

Runs fetch_for_company + build_company_deck for each canonical company
(non-lossy: upsert, NO delete), on the Sonnet 4.6 routing. Promotes fresh
ESG-critical articles (e.g. idfc's new fraud coverage) to the critical tier
with real ledes + recs.

Per-company try/except so one failure never aborts the rest. Prints
before/after critical counts + the resolved reasoning model so the run is
auditable. Honours an optional ONLY_COMPANIES="idfc-first-bank,icici-bank"
env to scope the run.
"""
from __future__ import annotations

import os
import pathlib
import sys
import traceback

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass
from dotenv import load_dotenv  # noqa: E402

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")
os.environ["JWT_SECRET"] = "x" * 40
os.environ.setdefault("ENVIRONMENT", "production")
# Reasoning model: default to the legacy fallback (gpt-5-mini) unless DEMO_REASONING_MODEL
# is set — the demo run uses DEMO_REASONING_MODEL=gpt-5 (full) for grounded, low-
# hallucination Stage 10/12/lede output (gpt-5-mini fabricated dates/₹/events).
_demo_model = (os.environ.get("DEMO_REASONING_MODEL") or "").strip()
if _demo_model:
    os.environ["SNOWKAP_REASONING_MODEL"] = _demo_model
else:
    os.environ.pop("SNOWKAP_REASONING_MODEL", None)
# OpenRouter org budget cap (403) is blocking Sonnet — force direct OpenAI so
# reasoning_heavy resolves to gpt-5-mini (legacy map). Set KEEP_OPENROUTER=1 to
# override once the org cap is lifted.
# NOTE: set to EMPTY, do NOT pop — engine.config re-runs load_dotenv() at import,
# which would re-add a popped key from .env (verified). A present-but-empty value
# survives non-override load_dotenv and reads as "no OpenRouter" in keys.py.
if os.environ.get("KEEP_OPENROUTER") != "1":
    os.environ["OPENROUTER_API_KEY"] = ""

from engine.db import connect  # noqa: E402
from engine.db.connection import is_postgres  # noqa: E402
from engine.models.company_article_view import _from_jsonb_value  # noqa: E402
from engine.llm.health import routing_report  # noqa: E402


def _crit_count(slug: str) -> int:
    with connect() as c:
        rows = c.execute(
            "SELECT v.personalised_analysis FROM company_article_view v "
            "WHERE v.company_slug = ?", (slug,)).fetchall()
    n = 0
    for (pa,) in rows:
        p = _from_jsonb_value(pa) or {}
        if not isinstance(p, dict):
            continue
        lede = p.get("lede") if isinstance(p.get("lede"), dict) else {}
        tier = (p.get("tier") or "").lower() or ("critical" if (lede.get("text") or "").strip() else "light")
        if tier == "critical":
            n += 1
    return n


def main() -> int:
    rep = routing_report()
    print("== ROUTING ==", rep, flush=True)
    if not is_postgres():
        print("!! not Postgres — refusing"); return 1
    # Build only on a capable reasoning model — Sonnet (OpenRouter) or an OpenAI
    # reasoning model (gpt-5*/o-series). The bare gpt-4.1/gpt-4o is the degraded
    # fallback we refuse to build on.
    m = (rep.get("reasoning_heavy_model") or "").lower()
    intended = ("sonnet" in m or "opus" in m
                or m.split("/")[-1].startswith(("gpt-5", "o1", "o3", "o4")))
    if not intended:
        print(f"!! reasoning_heavy={rep.get('reasoning_heavy_model')} looks like the "
              "degraded fallback — aborting (configure the model first)."); return 1

    from engine.config import load_companies
    from engine.ingestion.news_fetcher import fetch_for_company
    from engine.analysis.deck_builder import build_company_deck

    companies = load_companies()
    only = {s.strip() for s in (os.environ.get("ONLY_COMPANIES") or "").split(",") if s.strip()}
    if only:
        companies = [c for c in companies if c.slug in only]
    print(f"== rebuilding {len(companies)} companies on {rep['reasoning_heavy_model']} ==\n", flush=True)

    summary = []
    for company in companies:
        slug = company.slug
        before = _crit_count(slug)
        try:
            fresh = fetch_for_company(company, max_per_query=18)
            deck = build_company_deck(company, fresh, n_critical=3, n_total=10)
            after = _crit_count(slug)
            d = deck.to_dict() if hasattr(deck, "to_dict") else {}
            print(f"  [{slug}] OK  fetched={len(fresh)}  crit {before}->{after}  deck={d}", flush=True)
            summary.append((slug, "OK", before, after))
        except Exception as exc:  # noqa: BLE001
            print(f"  [{slug}] FAILED {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()
            summary.append((slug, f"FAIL:{type(exc).__name__}", before, before))

    print("\n== SUMMARY ==")
    for slug, st, b, a in summary:
        print(f"  {slug:24} {st:18} crit {b}->{a}")
    fails = [s for s in summary if s[1].startswith("FAIL")]
    print(f"\n{'ALL OK' if not fails else str(len(fails))+' FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
