"""Phase 9 — Share service: one-click "share this analyzed article" workflow.

Flow:
  1. User has an analyzed article open (article_id + company_slug)
  2. Clicks "Share", enters recipient's email
  3. We extract first name from email, render the newsletter HTML with that
     single article and a personalised greeting, send via Resend.

Used by both:
  - `POST /api/news/{article_id}/share` (frontend "Share" button)
  - `python scripts/generate_brief.py --format share --to email@...`
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from engine.output.email_sender import (
    SendResult,
    is_valid_email,
    name_from_email,
    send_email,
)
from engine.output.email_assets import logo_attachment
from engine.output.newsletter_renderer import (
    DEFAULT_CTA_URL,
    DEFAULT_CTA_LABEL,
    build_articles_from_outputs,
    render_article_brief_dark,
    render_newsletter,
)
from engine.output.intro_copywriter import build_intro as _build_stakes_intro
from engine.output.subject_line import build_subject as _build_editorial_subject

logger = logging.getLogger(__name__)


@dataclass
class ShareResult:
    status: str  # "sent" | "preview" | "failed"
    recipient: str
    recipient_name: str | None
    subject: str
    html_length: int  # useful in the UI to show "6 KB brief sent"
    article_id: str
    company_slug: str
    company_name: str
    error: str = ""
    provider_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_insight_payload(
    article_id: str, company_slug: str, outputs_root: Path | None
) -> dict | None:
    """Return the full pipeline payload for an article, or None."""
    if outputs_root is None:
        from engine.config import get_data_path
        outputs_root = get_data_path("outputs")
    import json
    insights_dir = Path(outputs_root) / company_slug / "insights"
    if not insights_dir.exists():
        return None
    for path in sorted(insights_dir.glob(f"*{article_id}*.json"), reverse=True):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _company_industry(company_slug: str) -> str:
    """Look up the company's industry for the Industry pill. Safe on error —
    returns empty string so the pill renders with a dash. The debug log
    (not warn) is intentional: unknown slugs are expected for onboarded
    prospects that haven't hit the ontology yet."""
    try:
        from engine.config import get_company
        c = get_company(company_slug)
        return c.industry or ""
    except Exception as exc:
        logger.debug("_company_industry(%s): not in registry (%s)", company_slug, exc)
        return ""


def _build_subject(company_name: str, article_title: str) -> str:
    """Legacy subject line formula — kept for callers that don't have the
    full insight payload. Phase 11C's editorial path via
    `_build_editorial_subject()` is preferred when the payload is available."""
    head = article_title.strip().split(" — ", 1)[0]
    if len(head) > 60:
        head = head[:58].rstrip() + "…"
    subject = f"Snowkap ESG · {company_name} · {head}"
    if len(subject) > 90:
        subject = subject[:87] + "…"
    return subject


def _build_subject_for_payload(
    company_name: str, payload: dict, target_title: str
) -> str:
    """Phase 11C: prefer the editorial subject line when we have the full
    insight payload (so we can open with ₹ exposure + regulator + timeline).
    Fall back to the legacy `Snowkap ESG · {Company} · {Headline}` pattern
    only when the payload is empty."""
    try:
        insight = (payload or {}).get("insight") or {}
        article = (payload or {}).get("article") or {}
        if insight:
            return _build_editorial_subject(company_name, insight, article)
    except Exception as exc:
        logger.warning("editorial subject line failed, falling back: %s", exc)
    return _build_subject(company_name, target_title)


def _build_intro_paragraph(
    recipient_name: str | None,
    sender_note: str | None,
    company_name: str,
    *,
    payload: dict | None = None,
) -> str:
    """Email body intro. Short, direct, opens with the stakes.

    Priority:
      1. Admin-provided `sender_note` — always wins, no edits.
      2. Stakes-first auto-generated intro (Phase 11C) — when we have the
         insight payload.
      3. Fallback generic copy — when no payload is available.
    """
    if sender_note:
        return sender_note.strip()
    if payload:
        insight = payload.get("insight") or {}
        article = payload.get("article") or {}
        if insight:
            try:
                return _build_stakes_intro(company_name, insight, article)
            except Exception as exc:
                logger.warning("stakes intro failed, falling back: %s", exc)
    return (
        f"An ESG signal on {company_name} worth your two minutes. Below: "
        f"the bottom line, why it's material, and a link to the full brief."
    )


def share_article_by_email(
    article_id: str,
    company_slug: str,
    recipient_email: str,
    outputs_root: Path | None = None,
    sender_note: str | None = None,
    read_more_base: str | None = None,
    cta_url: str = DEFAULT_CTA_URL,
    cta_label: str = DEFAULT_CTA_LABEL,
    dry_run: bool = False,
) -> ShareResult:
    """Render + send (or preview) a single-article share email.

    `dry_run=True` → returns the rendered HTML without sending (use for UI
    preview before the recipient confirms).
    """
    if not is_valid_email(recipient_email):
        return ShareResult(
            status="failed", recipient=recipient_email, recipient_name=None,
            subject="", html_length=0, article_id=article_id,
            company_slug=company_slug, company_name="",
            error="invalid recipient email",
        )

    # Resolve outputs root
    if outputs_root is None:
        from engine.config import get_data_path
        outputs_root = get_data_path("outputs")

    # Build the NewsletterArticle list — filtering to the one article of interest
    all_articles = build_articles_from_outputs(
        slugs=[company_slug],
        outputs_root=outputs_root,
        max_count=200,  # scan all, then filter
        read_more_base=read_more_base,
    )
    match = [a for a in all_articles if a.article_id == article_id or article_id in a.read_more_url]
    if not match:
        # Fallback: look through all output JSON filenames for any containing article_id
        # (build_articles_from_outputs filenames include article_id)
        return ShareResult(
            status="failed", recipient=recipient_email, recipient_name=None,
            subject="", html_length=0, article_id=article_id,
            company_slug=company_slug, company_name="",
            error=f"no HOME-tier analysis found for article_id {article_id}",
        )

    target_article = match[0]
    company_name = target_article.company_name

    recipient_name = name_from_email(recipient_email)

    # Phase 10 — Rich dark card template. Load the full insight payload so we
    # can surface Executive Summary / Key Insights / Framework chips / Impacted
    # metrics — not just a headline + bottom line.
    full_payload = _load_insight_payload(article_id, company_slug, outputs_root)

    # Phase 11C: editorial subject line when we have the payload.
    subject = _build_subject_for_payload(company_name, full_payload or {}, target_article.title)
    if full_payload:
        industry = _company_industry(company_slug) or target_article.industry or ""
        html = render_article_brief_dark(
            payload=full_payload,
            company_name=company_name,
            industry=industry,
            recipient_name=recipient_name,
            cta_url=cta_url,
            cta_label=cta_label,
        )
    else:
        # Fallback to the legacy newsletter layout if the insight JSON is
        # missing — shouldn't happen in production since the runner's accuracy
        # gate catches it, but better than a 500.
        html = render_newsletter(
            articles=[target_article],
            recipient_name=recipient_name,
            newsletter_title=f"A Snowkap signal on {company_name}",
            tagline="Shared with you · not a subscription",
            intro_paragraph=_build_intro_paragraph(
                recipient_name=recipient_name,
                sender_note=sender_note,
                company_name=company_name,
            ),
            cta_url=cta_url,
            cta_label=cta_label,
        )

    # Phase 11+ — inline the Snowkap logo as a CID attachment so it renders
    # even in Outlook Desktop (which blocks external <img src="https://...">
    # by default). The renderer references it as <img src="cid:snowkap-logo">.
    send_result: SendResult = send_email(
        to=recipient_email,
        subject=subject,
        html_body=html,
        dry_run=dry_run,
        attachments=[logo_attachment()],
    )

    return ShareResult(
        status=send_result.status,
        recipient=recipient_email,
        recipient_name=recipient_name,
        subject=subject,
        html_length=len(html),
        article_id=article_id,
        company_slug=company_slug,
        company_name=company_name,
        error=send_result.error,
        provider_id=send_result.provider_id,
    )


def preview_share_html(
    article_id: str,
    company_slug: str,
    recipient_email: str,
    outputs_root: Path | None = None,
    sender_note: str | None = None,
    read_more_base: str | None = None,
) -> tuple[str, ShareResult]:
    """Render without sending — returns (html, stub ShareResult).

    Used by the UI preview pane before the user confirms send.
    """
    result = share_article_by_email(
        article_id=article_id,
        company_slug=company_slug,
        recipient_email=recipient_email,
        outputs_root=outputs_root,
        sender_note=sender_note,
        read_more_base=read_more_base,
        dry_run=True,
    )
    # For preview-with-HTML, we regenerate the HTML string in-process.
    # (share_article_by_email returns the SendResult but discards the HTML —
    # we could refactor to surface it, but for now a simple second render
    # is fine and costs nothing.)
    if result.status == "failed":
        return "", result

    # Re-render for caller using the SAME rich dark-card renderer
    # that share_article_by_email uses — so the UI preview matches what
    # actually ships.
    if outputs_root is None:
        from engine.config import get_data_path
        outputs_root = get_data_path("outputs")

    payload = _load_insight_payload(article_id, company_slug, outputs_root)
    recipient_name = name_from_email(recipient_email)
    if payload:
        html = render_article_brief_dark(
            payload=payload,
            company_name=result.company_name,
            industry=_company_industry(company_slug),
            recipient_name=recipient_name,
        )
        return html, result

    # Fallback to legacy newsletter layout if no insight is present
    all_articles = build_articles_from_outputs(
        slugs=[company_slug], outputs_root=outputs_root,
        max_count=200, read_more_base=read_more_base,
    )
    match = [a for a in all_articles if a.article_id == article_id or article_id in a.read_more_url]
    if not match:
        return "", result
    target_article = match[0]
    html = render_newsletter(
        articles=[target_article],
        recipient_name=recipient_name,
        newsletter_title=f"A Snowkap signal on {target_article.company_name}",
        tagline="Shared with you · not a subscription",
        intro_paragraph=_build_intro_paragraph(
            recipient_name, sender_note, target_article.company_name,
        ),
    )
    return html, result
