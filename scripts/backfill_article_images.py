"""Backfill NewsAPI.ai image URLs onto previously-ingested articles.

The early Phase 9 ingestion path had a metadata-overwrite bug that discarded
every `image_url` the fetcher captured (news_fetcher.py:375). After the fix
this script re-queries NewsAPI.ai by keyword, matches results back to stored
articles by URL, and patches `metadata.image_url` in place.

Usage:
    python scripts/backfill_article_images.py                # all 7 companies
    python scripts/backfill_article_images.py --company icici-bank
    python scripts/backfill_article_images.py --dry-run      # report only

Safe to re-run: articles that already have `image_url` populated are skipped.
Rate-limited sleeping between queries so we don't burn NewsAPI.ai tokens.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.config import get_data_path, load_companies

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_images")

NEWSAPI_AI_URL = "https://eventregistry.org/api/v1/article/getArticles"


def _query_newsapi(keyword: str, api_key: str, count: int = 20) -> list[dict]:
    """Return recent NewsAPI.ai results for a keyword."""
    try:
        resp = requests.post(
            NEWSAPI_AI_URL,
            json={
                "action": "getArticles",
                "keyword": keyword,
                "articlesPage": 1,
                "articlesCount": min(count, 100),
                "articlesSortBy": "date",
                "includeArticleBody": False,  # we only need image + url here
                "resultType": "articles",
                "lang": "eng",
                "apiKey": api_key,
            },
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("NewsAPI.ai fetch failed for %r: %s", keyword, exc)
        return []
    return resp.json().get("articles", {}).get("results", []) or []


def _build_url_to_image_map(
    queries: list[str], api_key: str, delay_s: float = 0.4
) -> dict[str, str]:
    """Return {url: image_url} across a batch of queries. Empty images skipped."""
    url_to_image: dict[str, str] = {}
    for q in queries:
        results = _query_newsapi(q, api_key)
        new_hits = 0
        for r in results:
            url = r.get("url") or ""
            image = r.get("image") or ""
            if url and image and url not in url_to_image:
                url_to_image[url] = image
                new_hits += 1
        logger.info("query %r → %d articles, %d new image URLs", q, len(results), new_hits)
        time.sleep(delay_s)
    return url_to_image


def _patch_company(company_slug: str, url_to_image: dict[str, str], dry_run: bool) -> dict:
    """Patch every input-news JSON file for a company. Return counters."""
    counts = {"scanned": 0, "already": 0, "matched": 0, "missed": 0, "patched": 0}
    inputs_dir = get_data_path("inputs", "news", company_slug)
    if not inputs_dir.exists():
        logger.info("  [%s] no inputs dir — skipping", company_slug)
        return counts

    for path in inputs_dir.glob("*.json"):
        counts["scanned"] += 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("  [%s] unreadable %s: %s", company_slug, path.name, exc)
            continue

        meta = data.setdefault("metadata", {}) or {}
        if meta.get("image_url"):
            counts["already"] += 1
            continue

        article_url = data.get("url") or ""
        new_image = url_to_image.get(article_url)
        if not new_image:
            counts["missed"] += 1
            continue

        counts["matched"] += 1
        if dry_run:
            logger.info("  would patch %s  ← %s", path.name, new_image[:80])
            continue

        meta["image_url"] = new_image
        data["metadata"] = meta
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        counts["patched"] += 1

    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", default=None, help="single slug (else all)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-queries", type=int, default=6,
                        help="cap queries per company (NewsAPI.ai quota).")
    args = parser.parse_args(argv)

    api_key = (
        os.environ.get("NEWSAPI_AI_API_KEY")
        or os.environ.get("NEWSAPI_AI_KEY")
        or os.environ.get("EVENT_REGISTRY_API_KEY")
        or ""
    )
    if not api_key:
        # Fallback: read from .env (accept any of the supported aliases)
        env = _ROOT / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                for alias in ("NEWSAPI_AI_API_KEY=", "NEWSAPI_AI_KEY=", "EVENT_REGISTRY_API_KEY="):
                    if line.startswith(alias):
                        api_key = line.split("=", 1)[1].strip()
                        break
                if api_key:
                    break
    if not api_key:
        logger.error("NewsAPI.ai key not set — cannot backfill. "
                     "Set NEWSAPI_AI_API_KEY (or NEWSAPI_AI_KEY / EVENT_REGISTRY_API_KEY).")
        return 2

    companies = load_companies()
    if args.company:
        companies = [c for c in companies if c.slug == args.company]
        if not companies:
            logger.error("company slug %r not found", args.company)
            return 2

    totals = {"scanned": 0, "already": 0, "matched": 0, "missed": 0, "patched": 0}
    for c in companies:
        logger.info("=== %s ===", c.slug)
        queries = (c.news_queries or [])[: args.max_queries]
        if not queries:
            logger.info("  no queries configured")
            continue
        url_to_image = _build_url_to_image_map(queries, api_key)
        logger.info("  collected %d url→image pairs from %d queries",
                    len(url_to_image), len(queries))
        counts = _patch_company(c.slug, url_to_image, args.dry_run)
        logger.info(
            "  [%s] scanned=%d already=%d matched=%d missed=%d patched=%d",
            c.slug, counts["scanned"], counts["already"],
            counts["matched"], counts["missed"], counts["patched"],
        )
        for k in totals:
            totals[k] += counts[k]

    action = "would-patch" if args.dry_run else "patched"
    logger.info("=== TOTAL: scanned=%d  already_had=%d  %s=%d  missed=%d ===",
                totals["scanned"], totals["already"], action,
                totals["patched"] if not args.dry_run else totals["matched"],
                totals["missed"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
