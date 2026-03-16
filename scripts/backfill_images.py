"""Backfill image_url for existing articles by scraping OG images.

Usage:
    cd D:/ClaudePowerofnow/snowkap-esg/snowkap-esg
    PYTHONPATH=. python scripts/backfill_images.py
"""

import asyncio
import re

import httpx
from sqlalchemy import select

from backend.core.database import create_worker_session_factory
from backend.models.news import Article

TENANT_ID = "6908c18b-6c5d-4a1a-b5a6-7e2783d90d1a"
OG_IMAGE_RE = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE)
OG_IMAGE_RE2 = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.IGNORECASE)


async def fetch_og_image(client: httpx.AsyncClient, url: str) -> str | None:
    """Try to extract og:image from an article URL."""
    try:
        # Google News URLs redirect to the actual article
        resp = await client.get(url, follow_redirects=True, timeout=10.0)
        if resp.status_code != 200:
            return None
        # Only scan first 20KB for meta tags
        html = resp.text[:20000]
        match = OG_IMAGE_RE.search(html) or OG_IMAGE_RE2.search(html)
        return match.group(1) if match else None
    except Exception:
        return None


async def main():
    sf = create_worker_session_factory()
    async with sf() as db:
        result = await db.execute(
            select(Article).where(
                Article.tenant_id == TENANT_ID,
                Article.image_url.is_(None),
            )
        )
        articles = result.scalars().all()
        print(f"Found {len(articles)} articles without images")

        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 (compatible; SnowkapBot/1.0)"},
            limits=httpx.Limits(max_connections=5),
        ) as client:
            updated = 0
            for i, article in enumerate(articles):
                if not article.url:
                    continue
                img = await fetch_og_image(client, article.url)
                if img:
                    article.image_url = img
                    updated += 1
                    print(f"  [{i+1}/{len(articles)}] Found image for: {article.title[:50]}...")
                else:
                    print(f"  [{i+1}/{len(articles)}] No image: {article.title[:50]}...")

            await db.commit()
            print(f"\nDone! Updated {updated}/{len(articles)} articles with images.")


if __name__ == "__main__":
    asyncio.run(main())
