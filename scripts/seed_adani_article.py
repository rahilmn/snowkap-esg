"""Fetch a real Adani Power news article via NewsAPI.ai and run it
through the full intelligence stack so the demo board has a 10/10
showcase across ESG-Analyst, CEO and CFO lenses for Adani Power.

Usage:
    python scripts/seed_adani_article.py
    python scripts/seed_adani_article.py --pick best   # auto-pick (default)
    python scripts/seed_adani_article.py --pick first  # newest
    python scripts/seed_adani_article.py --query "Adani Power nuclear"

The script:
1. Calls NewsAPI.ai for a curated set of Adani Power queries.
2. Filters out wrap-ups, off-topic articles, and ones that don't
   mention Adani Power in the title or first 800 characters of the
   body (same guards the production fetcher uses).
3. Picks the article with the richest body (highest signal density),
   preferring those with a hero image.
4. Persists the IngestedArticle JSON under data/inputs/news/adani-power/
   (preserves metadata.image_url + source_type=newsapi_ai).
5. Wipes any prior cached output for the same article id.
6. Runs the full 12-stage pipeline via engine.main._run_article.
7. Prints the deep insight + all three perspectives + recommendations.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.config import get_company, get_data_path  # noqa: E402
from engine.ingestion.news_fetcher import (  # noqa: E402
    IngestedArticle,
    _is_calendar_preview,
    _is_wrapup_article,
    _url_hash,
    _write_article,
    _parse_published,
    _strip_html,
)

NEWSAPI_AI_URL = "https://eventregistry.org/api/v1/article/getArticles"

DEFAULT_QUERIES = [
    "Adani Power",
    "Adani nuclear",
    "Adani solar",
    "Adani Green Energy",
    "Adani Group",
]

# Topic boosters — articles whose body talks about these themes get
# scored higher because they exercise the full ESG / capex / regulatory
# stack across all three perspectives. Generic stock-tip / trendspotting
# pieces stay near 0.
_TOPIC_KEYWORDS = (
    "nuclear", "renewable", "solar", "wind", "battery", "bess",
    "green hydrogen", "decarbon", "emission", "scope 1", "scope 2",
    "scope 3", "capex", "brsr", "tcfd", "issb", "sdg",
    "transition", "esg rating",
)


def _api_key() -> str:
    for name in ("NEWSAPI_AI_API_KEY", "NEWSAPI_AI_KEY", "EVENT_REGISTRY_API_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    raise SystemExit(
        "NewsAPI.ai key missing — set NEWSAPI_AI_API_KEY in Replit Secrets."
    )


def _fetch(query: str, key: str, count: int = 30) -> list[dict]:
    resp = requests.post(
        NEWSAPI_AI_URL,
        json={
            "action": "getArticles",
            "keyword": query,
            "articlesPage": 1,
            "articlesCount": count,
            "articlesSortBy": "date",
            "includeArticleBody": True,
            "articleBodyLen": -1,
            "resultType": "articles",
            "lang": "eng",
            "apiKey": key,
        },
        timeout=25,
    )
    resp.raise_for_status()
    return ((resp.json().get("articles") or {}).get("results") or [])


def _candidate_score(item: dict) -> float:
    body = item.get("body") or ""
    title = item.get("title") or ""
    title_lc = title.lower()
    body_lc = body[:2000].lower()

    # Body length contributes a smaller share now — a 50K Q4 results
    # roundup is NOT richer than a 4K focused capex story.
    score = min(float(len(body)), 8000.0)

    if item.get("image"):
        score += 1500
    # Strongly prefer headlines that name the configured company.
    if "adani power" in title_lc:
        score += 12000
    elif "adani power" in body_lc:
        score += 4000
    # Penalise multi-company roundups / market wrap-ups
    if any(m in title_lc for m in ("q4 results", "results highlights", "trendspotting",
                                   "stocks to buy", "stock to buy", "outlook for",
                                   "stocks in news", "stocks in focus", "stocks to watch",
                                   "stocks rally", "top picks", "buzzing stocks")):
        score -= 15000
    # Penalise headlines that name 3+ companies (sign of a roundup)
    other_tickers = ("hul", "bajaj", "vedanta", "tata motors", "reliance",
                     "icici", "sbi", "infosys", "tcs", "wipro", "maruti",
                     "ntpc", "ongc", "coal india")
    other_count = sum(1 for t in other_tickers if t in title_lc)
    if other_count >= 1:
        score -= other_count * 4000
    # Boost if body covers ESG / transition themes
    hit_topics = sum(1 for k in _TOPIC_KEYWORDS if k in body_lc)
    score += hit_topics * 800
    return score


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed a real Adani Power article")
    parser.add_argument("--query", default=None, help="single query (else curated set)")
    parser.add_argument("--pick", default="best", choices=["best", "first"])
    parser.add_argument("--min-body", type=int, default=1500)
    parser.add_argument("--url-contains", default=None,
                        help="pin to candidate whose URL contains this substring")
    args = parser.parse_args(argv)

    company = get_company("adani-power")
    print(f"Company: {company.name} (slug={company.slug}, industry={company.industry})")

    key = _api_key()
    queries = [args.query] if args.query else DEFAULT_QUERIES

    pool: dict[str, dict] = {}
    for q in queries:
        try:
            results = _fetch(q, key)
        except requests.RequestException as exc:
            print(f"  [{q}] fetch failed: {exc}")
            continue
        kept = 0
        for r in results:
            url = r.get("url")
            if not url or url in pool:
                continue
            body = r.get("body") or ""
            title = r.get("title") or ""
            if len(body) < args.min_body:
                continue
            if _is_wrapup_article(title, body, company):
                continue
            if _is_calendar_preview(title, body):
                continue
            haystack = (title + " " + body[:800]).lower()
            if "adani power" not in haystack and "adanipower" not in haystack:
                continue
            r["_query"] = q
            pool[url] = r
            kept += 1
        print(f"  [{q}] {len(results)} fetched, {kept} kept")

    if not pool:
        print("\n[ERROR] No qualifying Adani Power articles in the live feed.")
        return 1

    candidates = sorted(pool.values(), key=_candidate_score, reverse=True)
    print(f"\nTotal qualifying candidates: {len(candidates)}")
    for i, c in enumerate(candidates[:5]):
        print(f"  [{i}] {c.get('title', '')[:80]}  body={len(c.get('body', ''))} "
              f"img={'Y' if c.get('image') else 'N'} src={(c.get('source') or {}).get('title', '')}")

    chosen = None
    if args.url_contains:
        for c in candidates:
            if args.url_contains in (c.get("url") or ""):
                chosen = c
                break
        if not chosen:
            print(f"\n[WARN] --url-contains '{args.url_contains}' matched 0 candidates; "
                  f"falling back to top-ranked")
    if not chosen:
        chosen = candidates[0]
    title = chosen.get("title") or ""
    body = chosen.get("body") or ""
    url = chosen.get("url") or ""
    image_url = chosen.get("image") or ""
    source_name = (chosen.get("source") or {}).get("title") or "NewsAPI.ai"
    published = _parse_published(chosen.get("dateTime") or chosen.get("date") or "")
    article_id = _url_hash(url)

    print(f"\n=== CHOSEN ===")
    print(f"Title       : {title}")
    print(f"Source      : {source_name}")
    print(f"URL         : {url}")
    print(f"Published   : {published}")
    print(f"Image       : {image_url or '(none)'}")
    print(f"Body length : {len(body)} chars")
    print(f"Article id  : {article_id}")

    article = IngestedArticle(
        id=article_id,
        title=_strip_html(title),
        content=_strip_html(body),
        summary=_strip_html(body[:500]),
        source=source_name,
        url=url,
        published_at=published,
        company_slug=company.slug,
        source_type="newsapi_ai",
        metadata={
            "query": chosen.get("_query", ""),
            "image_url": image_url,
            "concepts": [
                (c.get("label") or {}).get("eng", "")
                for c in (chosen.get("concepts") or [])[:5]
            ],
            "sentiment": chosen.get("sentiment"),
            "source_type": "newsapi_ai",
        },
    )
    input_path = _write_article(article)
    print(f"Input JSON  : {input_path}")

    out_root = get_data_path("outputs", company.slug)
    removed = 0
    if out_root.exists():
        for path in out_root.rglob(f"*{article_id}*"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    print(f"Cleared {removed} stale output file(s)")

    from engine.main import _run_article  # noqa: WPS433

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
    print("\n--- Running pipeline (this calls OpenAI; ~30-90s) ---")
    try:
        summary = _run_article(article_dict, company)
    except Exception as exc:  # noqa: BLE001 — surface clean error to operator
        print(f"\n[ERROR] Pipeline run failed: {type(exc).__name__}: {exc}")
        return 3
    print()
    print(f"Tier            : {summary.tier}")
    print(f"Rejected        : {summary.rejected}")
    print(f"Impact score    : {summary.impact_score}")
    print(f"Recommendations : {summary.recommendations}")
    print(f"Ontology queries: {summary.ontology_queries}")
    print(f"Files written   : {summary.files_written}")
    print(f"Elapsed         : {summary.elapsed_seconds}s")

    insight_json_path = out_root / "insights" / f"{published[:10]}_{article_id}.json"
    if not insight_json_path.exists():
        print(f"[ERROR] insight json missing at {insight_json_path}")
        return 2
    try:
        payload = json.loads(insight_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Could not read insight JSON: {exc}")
        return 4
    art_block = payload.get("article") or {}
    print(f"\nimage_url in article block: {art_block.get('image_url') or '(missing)'}")

    insight = payload.get("insight") or {}
    print()
    print("Headline    :", insight.get("headline"))
    print("Mechanism   :", (insight.get("core_mechanism") or "")[:240], "…")
    print("Net impact  :", (insight.get("net_impact_summary") or "")[:240], "…")

    perspectives = payload.get("perspectives") or {}

    esg = perspectives.get("esg-analyst") or {}
    if esg:
        print(f"\n[ESG-ANALYST] {esg.get('headline')}")
        for kpi in (esg.get("kpi_table") or [])[:3]:
            print(f"   KPI: {kpi.get('kpi_name')} = {kpi.get('company_value')} "
                  f"(quartile {kpi.get('peer_quartile')})")
        for sdg in (esg.get("sdg_targets") or [])[:3]:
            print(f"   SDG {sdg.get('code')}: {sdg.get('title')} ({sdg.get('applicability')})")
        for fc in (esg.get("framework_citations") or [])[:3]:
            print(f"   Framework: {fc.get('code')} — deadline {fc.get('deadline')} "
                  f"({fc.get('region')})")

    ceo = perspectives.get("ceo") or {}
    if ceo:
        print(f"\n[CEO] {ceo.get('headline')}")
        bp = (ceo.get("board_paragraph") or "").strip()
        if bp:
            print(f"   Board paragraph: {bp[:280]}…")
        for sh in (ceo.get("stakeholder_map") or [])[:3]:
            print(f"   Stakeholder: {sh.get('stakeholder')} — "
                  f"{(sh.get('stance') or '')[:120]}…")

    cfo = perspectives.get("cfo") or {}
    if cfo:
        print(f"\n[CFO] {cfo.get('headline')}")
        for k, v in (cfo.get("impact_grid") or {}).items():
            print(f"   grid {k:10s}: {v}")
        print(f"   Materiality: {cfo.get('materiality')}")

    print("\n--- Recommendations ---")
    for i, r in enumerate((payload.get("recommendations") or {}).get("recommendations") or [], 1):
        print(f"  {i}. [{r.get('urgency'):11s}] {r.get('title')}")
        print(f"      type={r.get('type')} impact={r.get('estimated_impact')} "
              f"roi={r.get('roi_percentage')}% payback={r.get('payback_months')}mo")

    print(f"\n[OK] Adani article ready: id={article_id}")
    print(f"     Open in app: /article/{article_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
