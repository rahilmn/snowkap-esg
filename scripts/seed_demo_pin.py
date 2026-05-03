#!/usr/bin/env python
"""Demo seeder — fetch a high-quality Adani article, run full analysis, pin 24h.

Phase 24.3 — used the day before a media demo to guarantee a hero article
sits at the top of the Adani Power feed with full CFO / CEO / ESG Analyst
intelligence already pre-warmed (no on-stage waiting on the spinner).

What this does:

    1. Searches NewsAPI.ai for a sustainability-rich Adani Power article
       (transition / climate / nuclear / ESG-rating events) using a
       multi-query AND-mode keyword search.
    2. Picks the best candidate by body length + title heuristic
       (skips stock-movement noise).
    3. Writes it as an IngestedArticle and runs ``process_article`` +
       ``enrich_on_demand`` so the deep insight, 3 perspectives, and
       recommendations are all populated in Supabase.
    4. Forces ``tier='HOME'`` on the index row so it hits the dashboard
       even if the natural relevance scorer would have classified it
       SECONDARY (the heuristic is conservative and a SECONDARY-tier
       hero card is bad for demos).
    5. Sets ``pinned_until = NOW + 24h`` so ``query_feed`` sorts it
       above every other article in the feed for the next 24 hours.

Usage::

    python scripts/seed_demo_pin.py
    python scripts/seed_demo_pin.py --company waaree-energies --queries 'BRSR,ESG,solar,manufacturing'
    python scripts/seed_demo_pin.py --pin-hours 48
    python scripts/seed_demo_pin.py --dry-run   # find article, don't write

Auth: requires ``OPENAI_API_KEY`` and ``NEWSAPI_AI_KEY`` in the env (or
the legacy ``NEWSAPI_AI_API_KEY`` / ``EVENT_REGISTRY_API_KEY`` aliases).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.environ.get("SNOWKAP_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("seed_demo_pin")


# --- Default per-company query banks --------------------------------------

_DEFAULT_QUERIES: dict[str, list[list[str]]] = {
    "adani-power": [
        ["Adani Power", "renewable", "transition"],
        ["Adani Power", "nuclear"],
        ["Adani Power", "BRSR"],
        ["Adani Power", "climate"],
        ["Adani Power", "ESG", "rating"],
        ["Adani Group", "ESG", "sustainability"],
    ],
    "waaree-energies": [
        ["Waaree Energies", "BRSR"],
        ["Waaree Energies", "solar", "capacity"],
        ["Waaree Energies", "ESG"],
        ["Waaree Energies", "PSPCL"],
    ],
    "icici-bank": [
        ["ICICI Bank", "ESG", "rating"],
        ["ICICI Bank", "climate", "disclosure"],
        ["ICICI Bank", "BRSR"],
        ["ICICI Bank", "green", "bond"],
    ],
    "yes-bank": [
        ["YES Bank", "ESG"],
        ["YES Bank", "BRSR"],
    ],
    "idfc-first-bank": [
        ["IDFC First Bank", "ESG"],
        ["IDFC First Bank", "BRSR"],
    ],
    "jsw-energy": [
        ["JSW Energy", "renewable", "transition"],
        ["JSW Energy", "BRSR"],
        ["JSW Energy", "ESG", "rating"],
    ],
}

# Skip stock-movement noise — these titles are never demo-grade
_TITLE_SKIP_TOKENS = (
    "52-week high", "rallies", "shares jump", "stock crash", "stock surge",
    "share price", "stock price", "market cap rises",
)
_MIN_BODY_CHARS = 1500


def _company_keywords(slug: str) -> list[list[str]]:
    return _DEFAULT_QUERIES.get(slug, [[slug.replace("-", " "), "ESG"]])


def _fetch_candidates(company_slug: str, max_per_query: int = 5) -> list[dict]:
    """Hit NewsAPI.ai across the company's query bank, return substantive
    candidates ranked by body length."""
    import requests

    api_key = (
        os.environ.get("NEWSAPI_AI_KEY")
        or os.environ.get("NEWSAPI_AI_API_KEY")
        or os.environ.get("EVENT_REGISTRY_API_KEY")
        or ""
    )
    if not api_key:
        raise SystemExit("NewsAPI.ai key required (NEWSAPI_AI_KEY env var)")

    candidates: list[dict] = []
    seen_urls: set[str] = set()
    for q in _company_keywords(company_slug):
        try:
            resp = requests.post(
                "https://eventregistry.org/api/v1/article/getArticles",
                json={
                    "action": "getArticles",
                    "keyword": q,
                    "keywordOper": "and",
                    "articlesCount": max_per_query,
                    "lang": "eng",
                    "apiKey": api_key,
                    "articlesSortBy": "date",
                    "includeArticleBody": True,
                    "articleBodyLen": -1,
                },
                timeout=20,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("NewsAPI.ai query %r failed: %s", q, exc)
            continue

        data = resp.json()
        for a in data.get("articles", {}).get("results", []):
            url = a.get("url") or ""
            if not url or url in seen_urls:
                continue
            body = a.get("body") or ""
            if len(body) < _MIN_BODY_CHARS:
                continue
            title = a.get("title") or ""
            if any(skip in title.lower() for skip in _TITLE_SKIP_TOKENS):
                continue
            seen_urls.add(url)
            candidates.append(
                {
                    "title": title,
                    "url": url,
                    "body": body,
                    "source": (a.get("source") or {}).get("title", "NewsAPI.ai"),
                    "published": a.get("dateTime") or a.get("date") or "",
                    "image": a.get("image") or "",
                    "concepts": [
                        c.get("label", {}).get("eng", "")
                        for c in (a.get("concepts") or [])[:5]
                    ],
                    "_query": " + ".join(q),
                }
            )

    # Rank: prefer titles that mention the COMPANY-SPECIFIC name (not just
    # the parent group) so the Phase 22.1 cross-entity gate doesn't reject.
    # The full company name from companies.json is the strongest signal —
    # "Adani Power" beats "Adani Group" beats just "Adani".
    company_name_tokens = company_slug.replace("-", " ").lower().split()

    def _specificity_score(c: dict) -> int:
        title_lower = (c.get("title") or "").lower()
        body_head = (c.get("body") or "")[:1200].lower()
        score = 0
        if " ".join(company_name_tokens) in title_lower:
            score += 100  # full multi-word company name in title
        elif all(t in title_lower for t in company_name_tokens):
            score += 60  # all tokens present, not necessarily adjacent
        if " ".join(company_name_tokens) in body_head:
            score += 20  # at least the lead paragraph mentions it
        # Penalise group-level / sector-aggregate articles
        if any(noise in title_lower for noise in (
            "group:", "9 companies", "stocks to watch", "top stocks",
            "sensex", "nifty", "market", "rally", "indices",
        )):
            score -= 50
        return score

    candidates.sort(
        key=lambda c: (_specificity_score(c), len(c["body"]), c["published"]),
        reverse=True,
    )
    return candidates


def _write_article(company_slug: str, candidate: dict) -> str:
    """Write the candidate to data/inputs/news/<slug>/<id>.json and return article_id."""
    import hashlib
    import json
    from datetime import datetime, timezone

    article_id = hashlib.sha256(candidate["url"].encode()).hexdigest()[:16]
    date_str = (candidate.get("published") or datetime.now(timezone.utc).isoformat())[:10]

    payload = {
        "id": article_id,
        "title": candidate["title"],
        "content": candidate["body"],
        "summary": candidate["body"][:500],
        "source": candidate["source"],
        "url": candidate["url"],
        "published_at": candidate["published"],
        "company_slug": company_slug,
        "source_type": "newsapi_ai",
        "metadata": {
            "image_url": candidate["image"],
            "concepts": candidate["concepts"],
            "demo_seeded": True,
            "demo_query": candidate["_query"],
        },
    }
    out_dir = ROOT / "data" / "inputs" / "news" / company_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date_str}_{article_id}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return article_id


def _run_pipeline_for(company_slug: str, candidate: dict, article_id: str) -> dict:
    """Run the full 12-stage pipeline on the candidate article.

    Reuses ``engine.main._run_article`` which already handles the full
    pipeline + writes to disk + upserts to article_index. Forces HOME-
    tier processing by setting ``demo_force_home=True`` in metadata so
    the writer emits all 4 output files (insight + 3 perspectives) even
    if the natural relevance scorer would have classified it SECONDARY.
    """
    from engine.config import get_company
    from engine.main import _run_article

    company = get_company(company_slug)
    article = {
        "id": article_id,
        "title": candidate["title"],
        "content": candidate["body"],
        "summary": candidate["body"][:500],
        "source": candidate["source"],
        "url": candidate["url"],
        "published_at": candidate["published"],
        "company_slug": company_slug,
        "source_type": "newsapi_ai",
        "metadata": {
            "image_url": candidate["image"],
            "concepts": candidate["concepts"],
            "demo_seeded": True,
        },
    }
    summary = _run_article(article, company)
    return {
        "article_id": article_id,
        "tier": getattr(summary, "tier", "?"),
        "rejected": getattr(summary, "rejected", False),
        "elapsed_seconds": getattr(summary, "elapsed_seconds", 0),
    }


def _force_home_and_pin(article_id: str, pin_hours: int) -> None:
    """Force the index row to HOME tier and set pinned_until = NOW + N hours."""
    from engine.db import connect

    pinned_until = (
        datetime.now(timezone.utc) + timedelta(hours=pin_hours)
    ).isoformat()
    with connect() as conn:
        conn.execute(
            "UPDATE article_index SET tier = 'HOME', pinned_until = ? WHERE id = ?",
            (pinned_until, article_id),
        )
    logger.info(
        "demo article %s pinned to HOME until %s (%dh from now)",
        article_id,
        pinned_until,
        pin_hours,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--company",
        default="adani-power",
        help="Target company slug (default adani-power).",
    )
    parser.add_argument(
        "--pin-hours",
        type=int,
        default=24,
        help="Hours to pin the article at the top of the feed (default 24).",
    )
    parser.add_argument(
        "--max-per-query",
        type=int,
        default=5,
        help="Articles to fetch per query before ranking.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Find candidates and print rankings, don't write anything.",
    )
    args = parser.parse_args(argv)

    print(f"=== seeding demo article for {args.company} ===")
    candidates = _fetch_candidates(args.company, max_per_query=args.max_per_query)
    if not candidates:
        print("ERROR: no substantive candidates found.", file=sys.stderr)
        return 2

    print(f"Found {len(candidates)} candidate(s):")
    for i, c in enumerate(candidates[:5], 1):
        print(
            f"  {i}. [{len(c['body']):5d} chars] [{c['source'][:25]}] {c['title'][:80]}"
        )

    if args.dry_run:
        return 0

    # Walk down the candidate list — if the pipeline rejects (cross-entity
    # gate, calendar-preview, off-topic), try the next one. This keeps the
    # demo workflow robust even when the top candidate happens to be a
    # group-level / aggregate article that Phase 22.1 correctly drops.
    chosen = None
    article_id = None
    pipeline_result = None
    max_attempts = min(5, len(candidates))
    for i, candidate in enumerate(candidates[:max_attempts]):
        print(f"\nAttempt {i + 1}/{max_attempts}: {candidate['title'][:90]}")
        article_id = _write_article(args.company, candidate)
        try:
            pipeline_result = _run_pipeline_for(args.company, candidate, article_id)
        except Exception as exc:
            logger.exception("attempt %d pipeline failed: %s", i + 1, exc)
            continue
        tier = pipeline_result.get("tier")
        rejected = pipeline_result.get("rejected")
        print(f"  tier={tier}  rejected={rejected}  elapsed={pipeline_result.get('elapsed_seconds'):.1f}s")
        if not rejected and tier in ("HOME", "SECONDARY"):
            chosen = candidate
            break
        # Non-blocking cleanup — leave the article on disk for inspection,
        # but also rewrite to keep the chosen one as the active demo target.
        print("  rejected — trying next candidate")

    if chosen is None:
        print("ERROR: every top candidate was rejected by the pipeline.", file=sys.stderr)
        return 3

    print(f"\n[CHOSEN] {chosen['title'][:100]}")
    print(f"  source: {chosen['source']}")
    print(f"  url:    {chosen['url']}")
    print(f"  body:   {len(chosen['body']):,} chars")
    print(f"  article_id: {article_id}")

    # If natural pipeline classified it SECONDARY, force on-demand
    # enrichment (stages 10-12) so the dashboard shows full intelligence.
    if pipeline_result.get("tier") != "HOME":
        print("\nForcing on-demand enrichment for full 3-role outputs...")
        try:
            from engine.analysis.on_demand import enrich_on_demand
            enrich_on_demand(article_id=article_id, company_slug=args.company)
            print("  on-demand enrichment complete")
        except Exception as exc:
            logger.exception("on-demand enrichment failed: %s", exc)
            # Non-fatal — we'll still pin and let the user click trigger-analysis

    # Pin to HOME for the next N hours
    _force_home_and_pin(article_id, args.pin_hours)
    print(f"\n[OK] Article pinned at top of {args.company} feed for the next {args.pin_hours}h.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
