"""Phase 25 W10 — daily 8am morning digest to sales@snowkap.co.in.

The user's #4 + #5 ask: "sales@snowkap.co.in should have the ability to
accurately share the intelligence via email" + "It needs to work maybe
overnight so that once a user comes up they instantly get the insights."

Composes a single HTML email summarising every CRITICAL + HIGH article
ingested overnight, grouped by customer tenant. Each article gets a 1-click
"Send to client" button deep-linked to the existing share flow with
the client point-of-contact email pre-filled.

Run via the scheduler at 7:50am IST (1:20am UTC for India business
hours), reads the latest entry from ``data/audit/overnight_runs.jsonl``
+ queries ``article_index`` for HOME-tier articles published in the
last 24 hours.

Cost: $0 (uses Resend free tier; no LLM call). Composition is pure
template fill from already-analysed insights.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_RECIPIENT = "sales@snowkap.co.in"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def send_morning_digest(
    recipient: str | None = None,
    *,
    dry_run: bool = False,
    materiality_filter: tuple[str, ...] = ("CRITICAL", "HIGH"),
    hours_window: int = 24,
) -> dict[str, Any]:
    """Compose + send the morning digest.

    Returns a result dict ``{status, recipient, subject, articles_included,
    customers_with_alerts, error?}``. Used by the scheduler 7:50am job
    AND the ``/snowkap/skills/morning-digest/`` skill.

    Feature flag: ``SNOWKAP_MORNING_DIGEST_ENABLED`` env var (default 1).
    Set to 0 to disable the cron without removing the wiring.
    """
    flag = os.environ.get("SNOWKAP_MORNING_DIGEST_ENABLED", "1").strip().lower()
    if flag in {"0", "false", "no"}:
        logger.info("morning_digest: disabled via SNOWKAP_MORNING_DIGEST_ENABLED=0")
        return {"status": "disabled", "articles_included": 0, "customers_with_alerts": 0}

    to = recipient or os.environ.get("SNOWKAP_DIGEST_RECIPIENT", DEFAULT_RECIPIENT)

    # 1. Pull HOME-tier articles from the last N hours
    try:
        articles = _query_recent_articles(materiality_filter, hours_window)
    except Exception as exc:  # noqa: BLE001
        logger.exception("morning_digest: query failed: %s", exc)
        return {"status": "failed", "recipient": to, "error": str(exc)}

    # Group by customer slug (deterministic ordering: critical-first then
    # by company name)
    by_company = _group_by_company(articles)
    customers_with_alerts = len(by_company)
    total_articles = sum(len(v) for v in by_company.values())

    # 2. Pull last overnight run summary (for the header line)
    overnight_summary = _latest_overnight_summary()

    # 3. Compose subject + body
    subject = _build_subject(total_articles, customers_with_alerts)
    body_html = _render_html(by_company, overnight_summary, hours_window)

    # 4. Send
    try:
        from engine.output.email_sender import send_email
        result = send_email(to=to, subject=subject, html_body=body_html, dry_run=dry_run)
        return {
            "status": result.status if hasattr(result, "status") else str(result),
            "recipient": to,
            "subject": subject,
            "articles_included": total_articles,
            "customers_with_alerts": customers_with_alerts,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("morning_digest: send failed: %s", exc)
        return {"status": "failed", "recipient": to, "subject": subject, "error": str(exc)}


# ---------------------------------------------------------------------------
# Article query
# ---------------------------------------------------------------------------


def _query_recent_articles(
    materiality_filter: tuple[str, ...],
    hours_window: int,
) -> list[dict[str, Any]]:
    """Query article_index for recent CRITICAL/HIGH articles."""
    from engine.index.sqlite_index import _connect, ensure_schema

    ensure_schema()
    placeholders = ",".join("?" * len(materiality_filter))
    sql = f"""
        SELECT id, company_slug, title, source, url, published_at, tier,
               materiality, action, relevance_score, impact_score,
               primary_theme, do_nothing, json_path,
               cfo_preflight_status
        FROM article_index
        WHERE UPPER(materiality) IN ({placeholders})
          AND published_at >= datetime('now', '-{int(hours_window)} hours')
          AND tier = 'HOME'
        ORDER BY
            CASE UPPER(materiality)
                WHEN 'CRITICAL' THEN 4
                WHEN 'HIGH' THEN 3
                ELSE 0
            END DESC,
            relevance_score DESC,
            published_at DESC
    """
    params = tuple(m.upper() for m in materiality_filter)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _group_by_company(articles: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group articles by company_slug, preserving the materiality-first order."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for art in articles:
        slug = art.get("company_slug") or "unknown"
        grouped.setdefault(slug, []).append(art)
    # Sort companies by their highest-materiality article's score, then by name
    def _company_key(slug: str) -> tuple[int, str]:
        max_priority = 0
        for art in grouped[slug]:
            mat = (art.get("materiality") or "").upper()
            rank = {"CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "LOW": 1}.get(mat, 0)
            max_priority = max(max_priority, rank)
        return (-max_priority, slug)
    return dict(sorted(grouped.items(), key=lambda kv: _company_key(kv[0])))


# ---------------------------------------------------------------------------
# Overnight-run summary lookup
# ---------------------------------------------------------------------------


def _latest_overnight_summary() -> dict[str, Any] | None:
    """Read the most recent overnight run from data/audit/overnight_runs.jsonl.
    Returns None when no log exists (fresh install / digest run before
    first batch)."""
    try:
        from engine import audit
        entries = list(audit.read_overnight_runs())
        if not entries:
            return None
        return entries[-1]  # newest at end of append-only log
    except Exception as exc:  # noqa: BLE001
        logger.debug("morning_digest: overnight summary lookup failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Subject + HTML composition
# ---------------------------------------------------------------------------


def _build_subject(total_articles: int, customer_count: int) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if total_articles == 0:
        return f"Snowkap Morning Brief · {today} · No critical alerts overnight"
    return (
        f"Snowkap Morning Brief · {today} · "
        f"{total_articles} alert{'s' if total_articles != 1 else ''} "
        f"across {customer_count} customer{'s' if customer_count != 1 else ''}"
    )


def _render_html(
    by_company: dict[str, list[dict[str, Any]]],
    overnight_summary: dict[str, Any] | None,
    hours_window: int,
) -> str:
    """Render the digest HTML. Mirrors the Phase 11C dark-card editorial
    style — orange accents on light background, system-ui font, tables
    for layout (Outlook compatibility)."""
    today = datetime.now(timezone.utc).strftime("%A, %d %B %Y")

    # Header band
    sections: list[str] = []
    sections.append(
        f"""
        <div style="background:#0F172A;padding:20px 24px;color:#fff;">
            <div style="font-size:13px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#DF5900;">
                Snowkap Morning Brief
            </div>
            <div style="font-size:18px;font-weight:600;margin-top:4px;">{today}</div>
            <div style="font-size:12px;opacity:0.75;margin-top:6px;">
                Critical + High-priority articles ingested in the last {hours_window} hours.
                Click "Send to client" on any article to forward to the customer's point-of-contact.
            </div>
        </div>
        """
    )

    # Overnight stats banner (if we have one)
    if overnight_summary:
        cost = overnight_summary.get("total_cost_usd")
        cost_str = f" · ${cost:.2f}" if cost is not None else ""
        sections.append(
            f"""
            <div style="background:#F1F5F9;padding:10px 24px;font-size:11px;color:#475569;">
                Overnight batch:
                {overnight_summary.get("tenants_succeeded", 0)} / {overnight_summary.get("tenants_attempted", 0)} tenants ·
                {overnight_summary.get("articles_fetched", 0)} fetched →
                {overnight_summary.get("articles_selected", 0)} selected →
                {overnight_summary.get("articles_passed_preflight", 0)} passed preflight{cost_str}
            </div>
            """
        )

    # Empty-state
    if not by_company:
        sections.append(
            """
            <div style="padding:32px 24px;text-align:center;color:#64748B;font-style:italic;">
                No CRITICAL or HIGH articles in the last 24 hours.<br>
                <span style="font-size:11px;">All customer feeds clear; no overnight escalations.</span>
            </div>
            """
        )
        return _wrap_html(sections)

    # Per-customer sections
    for slug, articles in by_company.items():
        company_block = _render_company_block(slug, articles)
        sections.append(company_block)

    # Footer
    sections.append(
        """
        <div style="padding:16px 24px;font-size:11px;color:#64748B;border-top:1px solid #E2E8F0;text-align:center;">
            Generated automatically by Snowkap ESG.
            Reply to this email to flag an article that should NOT have been surfaced.
        </div>
        """
    )

    return _wrap_html(sections)


def _render_company_block(slug: str, articles: list[dict[str, Any]]) -> str:
    """Render one company's section with all their alerts."""
    company_name = _company_display_name(slug)
    cards: list[str] = []
    for art in articles:
        cards.append(_render_article_card(art))
    return f"""
        <div style="padding:16px 24px;border-bottom:1px solid #E2E8F0;">
            <div style="font-size:14px;font-weight:700;color:#0F172A;text-transform:uppercase;letter-spacing:0.04em;">
                {company_name}
                <span style="float:right;color:#64748B;font-weight:400;font-size:11px;">{len(articles)} alert{'s' if len(articles) != 1 else ''}</span>
            </div>
            <div style="margin-top:10px;">
                {''.join(cards)}
            </div>
        </div>
        """


def _render_article_card(article: dict[str, Any]) -> str:
    """One alert card with headline + materiality badge + Send-to-client button."""
    materiality = (article.get("materiality") or "").upper()
    title = article.get("title") or "(untitled article)"
    source = article.get("source") or ""
    article_id = article.get("id") or ""
    badge_color = {
        "CRITICAL": "#DC2626",
        "HIGH": "#EA580C",
        "MODERATE": "#CA8A04",
    }.get(materiality, "#475569")

    # Resolve POC email if available — see customer_poc table (W10 sub-feature
    # to be added on first real digest run; falls back to empty so the link
    # opens the share modal with no recipient pre-filled).
    poc_email = _lookup_customer_poc(article.get("company_slug") or "")
    share_url_base = os.environ.get("SNOWKAP_SHARE_BASE_URL", "/news") + f"/{article_id}/share"
    share_url = share_url_base + (f"?recipient={poc_email}" if poc_email else "")

    return f"""
        <div style="background:#fff;border:1px solid #E2E8F0;border-left:3px solid {badge_color};border-radius:6px;padding:10px 12px;margin-bottom:8px;">
            <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;">
                <div style="flex:1;min-width:0;">
                    <div style="font-size:14px;font-weight:600;color:#0F172A;line-height:1.3;">{title}</div>
                    <div style="font-size:11px;color:#64748B;margin-top:4px;">
                        {source} · <span style="color:{badge_color};font-weight:600;">{materiality}</span>
                    </div>
                </div>
                <a href="{share_url}" style="background:#DF5900;color:#fff;text-decoration:none;font-size:11px;font-weight:600;padding:6px 10px;border-radius:4px;white-space:nowrap;">Send to client →</a>
            </div>
        </div>
        """


def _wrap_html(sections: list[str]) -> str:
    """Wrap sections in the outer table-based shell for Outlook compat."""
    body = "".join(sections)
    return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Snowkap Morning Brief</title>
</head>
<body style="margin:0;padding:0;background:#F1F5F9;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#F1F5F9;">
        <tr><td align="center" style="padding:20px 0;">
            <table role="presentation" width="640" cellspacing="0" cellpadding="0" style="max-width:640px;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
                <tr><td style="text-align:left;">{body}</td></tr>
            </table>
        </td></tr>
    </table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _company_display_name(slug: str) -> str:
    """Best-effort human name for a slug. Falls back to title-cased slug."""
    try:
        from engine.config import load_companies
        for company in load_companies():
            if company.slug == slug:
                return company.name
    except Exception:  # noqa: BLE001
        pass
    return slug.replace("-", " ").title()


def _lookup_customer_poc(company_slug: str) -> str:
    """Look up the customer's primary point-of-contact email.

    For the first Phase 25 deploy there's no customer_poc table populated
    yet; this returns empty so the digest's "Send to client" link opens
    the share modal with no recipient pre-filled (operator types it).
    The table can be added later as a small `customer_poc(slug PK,
    email)` SQLite table populated via the W6 batch onboarding admin UI.
    """
    if not company_slug:
        return ""
    try:
        from engine.db import connect as _db_connect
        with _db_connect() as conn:
            cur = conn.execute(
                "SELECT email FROM customer_poc WHERE slug = ? LIMIT 1",
                (company_slug,),
            )
            row = cur.fetchone()
            if row:
                return str(row[0] if not isinstance(row, dict) else row.get("email", ""))
    except Exception:  # noqa: BLE001 — table doesn't exist yet, that's OK
        pass
    return ""
