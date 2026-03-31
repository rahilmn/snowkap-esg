"""Full article content extraction via trafilatura.

Phase 1B: Replaces the ~50-word RSS summary with 500-2000 words of clean article text.
All downstream NLP (sentiment, entities, frameworks) runs on this richer input.
"""

import asyncio
import json
import re
from base64 import b64decode
from dataclasses import dataclass
from urllib.parse import urlparse

import structlog
import trafilatura
from tenacity import retry, stop_after_attempt, wait_fixed

logger = structlog.get_logger()

# Domains known to block scraping — skip and use RSS summary
BLOCKED_DOMAINS: set[str] = {
    "wsj.com",
    "ft.com",
    "bloomberg.com",
    "economist.com",
    "nytimes.com",
}


def _resolve_google_news_url(url: str) -> str:
    """Resolve Google News proxy URLs to the actual article URL.

    Google News RSS returns proxy URLs (https://news.google.com/rss/articles/...)
    that serve a JavaScript redirect page, not the actual article HTML.
    Uses googlenewsdecoder to extract the real URL.
    Falls back to the original URL if resolution fails.
    """
    if "news.google.com" not in url:
        return url

    try:
        from googlenewsdecoder import new_decoderv1

        result = new_decoderv1(url)
        if result.get("status") and result.get("decoded_url"):
            resolved = result["decoded_url"]
            logger.debug("google_news_url_resolved", resolved=resolved[:80])
            return resolved
    except Exception as e:
        logger.debug("google_news_url_resolve_failed", url=url[:60], error=str(e))

    return url


@dataclass
class ExtractedContent:
    """Result of trafilatura article extraction."""

    content: str | None = None
    title: str | None = None
    author: str | None = None
    sitename: str | None = None
    date: str | None = None
    image_url: str | None = None
    error: str | None = None


def _is_blocked_domain(url: str) -> bool:
    """Check if URL is from a known paywalled domain."""
    try:
        domain = urlparse(url).netloc.lower()
        return any(blocked in domain for blocked in BLOCKED_DOMAINS)
    except Exception:
        return False


def _extract_og_image_from_html(html: str) -> str | None:
    """Extract og:image meta tag from raw HTML."""
    if not html:
        return None
    # Try both attribute orderings
    match = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html[:20000])
    if not match:
        match = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html[:20000])
    if not match:
        # Try twitter:image as fallback
        match = re.search(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']', html[:20000])
    return match.group(1) if match else None


def extract_og_image(url: str) -> str | None:
    """Standalone og:image extractor — works even when article text extraction fails."""
    try:
        resolved = _resolve_google_news_url(url)
        downloaded = trafilatura.fetch_url(resolved)
        return _extract_og_image_from_html(downloaded) if downloaded else None
    except Exception:
        return None


@retry(stop=stop_after_attempt(2), wait=wait_fixed(1))
def _fetch_and_extract(url: str) -> ExtractedContent:
    """Synchronous trafilatura extraction (runs in thread pool for async callers)."""
    # Resolve Google News proxy URLs to real article URLs
    resolved_url = _resolve_google_news_url(url)

    if _is_blocked_domain(resolved_url):
        return ExtractedContent(error="blocked_domain")

    downloaded = trafilatura.fetch_url(resolved_url)
    if not downloaded:
        return ExtractedContent(error="fetch_failed")

    result = trafilatura.extract(
        downloaded,
        include_comments=False,
        include_tables=True,
        include_links=False,
        include_images=True,
        output_format="json",
        with_metadata=True,
        favor_precision=True,
    )

    if not result:
        return ExtractedContent(error="extraction_failed")

    try:
        data = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return ExtractedContent(error="json_parse_failed")

    content = data.get("text") or data.get("raw_text")
    if not content or len(content.strip()) < 50:
        return ExtractedContent(error="content_too_short")

    # Extract image from trafilatura output or og:image from HTML
    image_url = data.get("image") or _extract_og_image_from_html(downloaded)

    return ExtractedContent(
        content=content.strip(),
        title=data.get("title"),
        author=data.get("author"),
        sitename=data.get("sitename"),
        date=data.get("date"),
        image_url=image_url,
    )


async def extract_article_content(url: str) -> ExtractedContent:
    """Async wrapper — runs trafilatura in thread pool to avoid blocking event loop."""
    try:
        return await asyncio.to_thread(_fetch_and_extract, url)
    except Exception as e:
        logger.warning("content_extraction_failed", url=url[:120], error=str(e))
        return ExtractedContent(error=str(e))
