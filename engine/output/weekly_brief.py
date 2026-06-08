"""Phase 48.K — weekly Morning-Brew newsletter.

Composes + sends each company's weekly brief from its ALREADY-APPROVED
deck (so the newsletter inherits the approval gate, 30-day freshness, and
non-fabrication guarantees for free). Reuses the existing single-article
Morning-Brew renderer + Resend send path in `share_service` — nothing new
to render or attach (the SNOWKAP logo CID is handled there).

The weekly brief leads with the company's TOP CRITICAL article of the
week (the #1 by deck order). The email's CTA links to /now where the
reader sees the full 10-card deck. ("Top-3 in one email" is a future
multi-article-digest enhancement; leading with the #1 critical story +
deck link is the launch behaviour.)

Public surface:
  * top_article_for_company(slug) → (article_id, title) | None
  * build_weekly_brief(slug) → (subject, html)   (preview / send-me)
  * send_weekly_brief_to_subscribers(slug) → dict (counts)
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def top_article_for_company(company_slug: str) -> tuple[str, str] | None:
    """Return (article_id, title) of the company's #1 deck article, or None."""
    try:
        from engine.config import get_company
        from engine.models import company_article_view
        company = get_company(company_slug)
        industry = getattr(company, "industry", None) if company else None
        rows, _meta = company_article_view.deck_for_company(
            company_slug, industry, max_age_days=30, limit=10,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("weekly_brief: deck read failed for %s: %s", company_slug, exc)
        return None
    if not rows:
        return None
    top = rows[0]
    return (top.get("article_id") or "", top.get("title") or "")


def build_weekly_brief(company_slug: str) -> tuple[str, str]:
    """Return (subject, html) for the company's weekly brief preview.

    Renders the top critical article via the Morning-Brew layout (dry-run,
    no send). Empty strings when the company has no deck.
    """
    top = top_article_for_company(company_slug)
    if not top or not top[0]:
        return "", ""
    article_id, title = top
    try:
        from engine.config import get_company, get_data_path
        from engine.output.share_service import _load_insight_payload
        from engine.output.newsletter_morning_brew import render_article_morning_brew
        company = get_company(company_slug)
        company_name = getattr(company, "name", None) or company_slug
        payload = _load_insight_payload(article_id, company_slug, get_data_path("outputs"))
        if not payload:
            return "", ""
        html = render_article_morning_brew(
            payload=payload,
            company_name=company_name,
            company_slug=company_slug,
            article_id=article_id,
        )
        subject = f"Your weekly ESG brief — {company_name}: {(title or '')[:60]}"
        return subject, html
    except Exception as exc:  # noqa: BLE001
        logger.warning("weekly_brief: render failed for %s: %s", company_slug, exc)
        return "", ""


def send_weekly_brief_to_subscribers(company_slug: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Send the weekly brief to every active subscriber for the company.

    Reuses share_article_by_email (Morning-Brew layout + logo + Resend).
    Returns counts {subscribers, sent, failed, skipped}.
    """
    result = {"company_slug": company_slug, "subscribers": 0, "sent": 0, "failed": 0, "skipped": 0}

    top = top_article_for_company(company_slug)
    if not top or not top[0]:
        result["skipped"] = 1
        logger.info("weekly_brief: no deck article for %s — skipping send", company_slug)
        return result
    article_id = top[0]

    try:
        from engine.models import newsletter_subscribers
        emails = newsletter_subscribers.list_active(company_slug)
    except Exception as exc:  # noqa: BLE001
        logger.warning("weekly_brief: subscriber read failed for %s: %s", company_slug, exc)
        emails = []
    result["subscribers"] = len(emails)
    if not emails:
        return result

    from engine.output.share_service import share_article_by_email
    for email in emails:
        try:
            r = share_article_by_email(
                article_id=article_id,
                company_slug=company_slug,
                recipient_email=email,
                layout="morning_brew",
                cta_label="Open your weekly deck →",
                dry_run=dry_run,
            )
            status = getattr(r, "status", "")
            if status in ("sent", "preview"):
                result["sent"] += 1
            else:
                result["failed"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("weekly_brief: send to %s failed: %s", email, exc)
            result["failed"] += 1

    logger.info(
        "weekly_brief: %s → %d subscribers, %d sent, %d failed",
        company_slug, result["subscribers"], result["sent"], result["failed"],
    )
    return result
