"""Phase 9 — HTML newsletter renderer for drip marketing + prospect outreach.

Visual target: ET Sustainability "The Green Shift" — branded header, dated
tagline, 2-column highlights grid, per-article blocks with image + headline +
"Bottom line" + "Why this matters" + "Read more" link, and a prominent CTA
block near the footer.

Design constraints (email-client-safe):
  - Tables for layout (Outlook doesn't support flex/grid)
  - Inline styles ONLY (no <style> block; Gmail strips head CSS in clipped mode)
  - No modern CSS features (max-width + vh, grid, clamp)
  - Images use absolute URLs
  - Body width 600px (works across all mobile + desktop clients)
  - UTF-8 encoded; hex color codes (not oklch/named colors)

The newsletter aggregates 5-7 recent HOME-tier articles across one or more
companies, pulling:
  - Headline, article image URL (from NewsAPI.ai metadata)
  - "Bottom line" from `insight.decision_summary.key_risk` or `net_impact_summary`
  - "Why this matters" from CEO `board_paragraph` first sentences or
    `decision_summary.top_opportunity`
  - "Read more" link (original article or Snowkap hosted analysis)

CTA is a single prominent button block. Default destination:
https://snowkap.com/contact-us/

No LLM calls. Renders deterministically from existing pipeline JSON output.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Colour + style tokens (Snowkap brand palette, email-safe hex)
# ---------------------------------------------------------------------------

BRAND_PRIMARY = "#F97316"  # orange-500 — Snowkap brand (matches logo + app UI)
BRAND_DARK = "#0F172A"  # slate-900 — deep black for contrast blocks
BRAND_DEEP = "#C2410C"  # orange-700 — for gradients / darker accents
BRAND_ACCENT = "#DC2626"  # red-600 — for urgent / call-out markers
TEXT_PRIMARY = "#0F172A"  # slate-900 — near-black body text
TEXT_SECONDARY = "#334155"  # slate-700
TEXT_MUTED = "#64748B"  # slate-500
BORDER_LIGHT = "#E5E7EB"  # gray-200
BG_LIGHT = "#F9FAFB"  # gray-50
BG_CALLOUT = "#FFF7ED"  # orange-50 — warm background for CTA
LINK_BLUE = "#C2410C"  # orange-700 — on-brand link colour

DEFAULT_NEWSLETTER_TITLE = "The Snowkap Signal"
DEFAULT_TAGLINE = "Your weekly intelligence on ESG signals that move ₹"
DEFAULT_CTA_URL = "https://snowkap.com/contact-us/"
DEFAULT_CTA_LABEL = "Book a demo with Snowkap"
DEFAULT_FOOTER_ADDRESS = "Snowkap Inc · ci@snowkap.com · snowkap.com"

# Phase 11C — Snowkap logo hosted on the brand CDN.
#
# Using the SVG URL directly (user-supplied): Gmail web/mobile and Apple Mail
# render it fine; Outlook Desktop blocks it by default (which is why we keep
# a pure-CSS wordmark underneath — see `_brand_header_dark()`).
#
# Override via `SNOWKAP_LOGO_URL` env when testing against a staging CDN.
import os as _os
SNOWKAP_LOGO_URL = _os.environ.get(
    "SNOWKAP_LOGO_URL",
    "https://snowkaplive.b-cdn.net/wp-content/uploads/2025/07/Snowkap_Logo.svg",
)


def _load_inline_logo_svg() -> str:
    """Return the bundled Snowkap SVG as an inline string (renders in Gmail,
    Apple Mail, iOS Mail, Outlook 365 — the CSS wordmark fallback catches the
    rest). Read lazily so the asset path stays discoverable."""
    from pathlib import Path
    try:
        asset = Path(__file__).resolve().parent.parent.parent / "Snowkap_Logo (2).svg"
        if not asset.exists():
            asset = Path(__file__).resolve().parent.parent.parent / "client" / "public" / "assets" / "snowkap-logo.svg"
        if asset.exists():
            svg = asset.read_text(encoding="utf-8")
            # Strip XML preamble (keeps email clients happier)
            import re as _re
            svg = _re.sub(r"<\?xml[^?]*\?>\s*", "", svg).strip()
            return svg
    except Exception:
        pass
    return ""


SNOWKAP_LOGO_SVG_INLINE = _load_inline_logo_svg()

# Emoji markers for highlights, one per industry (add more as needed)
_INDUSTRY_EMOJI = {
    "Power/Energy": "⚡",
    "Financials/Banking": "🏦",
    "Renewable Energy": "🌱",
    "Asset Management": "📊",
    "Pharmaceuticals": "💊",
    "Consumer/Beverage": "🥤",
    "Information Technology": "💻",
    "Automotive": "🚗",
    "Steel": "🔩",
    "Oil & Gas": "🛢️",
    "Chemicals": "🧪",
    "Other": "🌿",
}


# ---------------------------------------------------------------------------
# Dataclass for a single article block in the newsletter
# ---------------------------------------------------------------------------


@dataclass
class NewsletterArticle:
    title: str
    company_name: str
    industry: str
    bottom_line: str  # "If you care about X, this is material because..."
    why_matters: str  # "A CFO should watch this because..."
    read_more_url: str
    read_more_label: str = "Read the full Snowkap brief"
    image_url: str = ""  # absolute URL; if empty, emoji banner used
    published_at: str = ""  # ISO string
    source_name: str = ""  # e.g. "Mint", "Economic Times"
    article_id: str = ""  # pipeline article id — for filter-by-id in share service

    def emoji(self) -> str:
        return _INDUSTRY_EMOJI.get(self.industry, "🌿")

    def short_headline(self, max_len: int = 120) -> str:
        return self.title[: max_len - 1] + "…" if len(self.title) > max_len else self.title


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def _esc(s: str | None) -> str:
    """HTML-escape + convert None to empty string."""
    if not s:
        return ""
    return html.escape(s, quote=True)


def _format_date(iso_str: str | None = None) -> str:
    """'April 22, 2026' style."""
    if not iso_str:
        iso_str = datetime.now(timezone.utc).isoformat()
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        dt = datetime.now(timezone.utc)
    return dt.strftime("%B %d, %Y").replace(" 0", " ")


# ---------------------------------------------------------------------------
# Block renderers
# ---------------------------------------------------------------------------


def _render_header(
    newsletter_title: str,
    tagline: str,
    send_date: str,
    unsubscribe_url: str | None,
) -> str:
    """Top brand strip + title block — black background with Snowkap logo +
    a thick orange accent rule under the hero block."""
    unsub_link = ""
    if unsubscribe_url:
        unsub_link = (
            f'<td align="right" style="font-family:Arial,sans-serif;font-size:11px;color:#9CA3AF;padding:10px 20px;">'
            f'<a href="{_esc(unsubscribe_url)}" style="color:#9CA3AF;text-decoration:underline;">Unsubscribe</a>'
            f"</td>"
        )
    return f"""\
<!-- HEADER: black bar with Snowkap wordmark (image + CSS text fallback) -->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{BRAND_DARK};">
  <tr>
    <td align="center" style="padding:0;">
      <table role="presentation" width="600" align="center" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;background:{BRAND_DARK};">
        <tr>
          <td align="left" style="padding:22px 28px 22px 28px;">
            <!--
              Pure-CSS wordmark that always renders — even when Gmail blocks
              images (its default). The <img> tag below is hidden via display
              toggle when images fail to load; the text is the reliable brand.
            -->
            <span style="font-family:Georgia,'Times New Roman',serif;font-size:26px;font-weight:700;color:#ffffff;letter-spacing:1.5px;line-height:1;">
              <span style="color:{BRAND_PRIMARY};">S</span>NOWKAP
            </span>
            <span style="font-family:Arial,sans-serif;font-size:10px;font-weight:700;color:{BRAND_PRIMARY};letter-spacing:2px;margin-left:10px;text-transform:uppercase;vertical-align:2px;">
              ESG Intelligence
            </span>
          </td>
          {unsub_link}
        </tr>
      </table>
    </td>
  </tr>
  <!-- Orange accent rule -->
  <tr>
    <td style="height:4px;line-height:4px;font-size:4px;background:{BRAND_PRIMARY};">&nbsp;</td>
  </tr>
</table>

<!-- TITLE block on white -->
<table role="presentation" width="600" align="center" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;background:#ffffff;">
  <tr>
    <td align="center" style="padding:36px 24px 4px 24px;">
      <div style="display:inline-block;padding:5px 14px;background:{BRAND_PRIMARY};color:#ffffff;font-family:Arial,sans-serif;font-size:11px;font-weight:700;letter-spacing:1.8px;text-transform:uppercase;border-radius:2px;">
        ESG Signal
      </div>
    </td>
  </tr>
  <tr>
    <td align="center" style="padding:14px 24px 4px 24px;font-family:Georgia,'Times New Roman',serif;font-size:30px;font-weight:700;color:{BRAND_DARK};line-height:1.2;">
      {_esc(newsletter_title)}
    </td>
  </tr>
  <tr>
    <td align="center" style="padding:8px 24px 2px 24px;font-family:Arial,sans-serif;font-size:13px;color:{TEXT_MUTED};letter-spacing:0.5px;text-transform:uppercase;">
      {_esc(send_date)}
    </td>
  </tr>
  <tr>
    <td align="center" style="padding:4px 24px 28px 24px;font-family:Arial,sans-serif;font-size:14px;color:{TEXT_SECONDARY};font-style:italic;">
      {_esc(tagline)}
    </td>
  </tr>
</table>
"""


def _render_greeting(recipient_name: str | None, intro_paragraph: str) -> str:
    greeting_line = ""
    if recipient_name:
        greeting_line = (
            f'<p style="font-family:Arial,sans-serif;font-size:15px;color:{TEXT_PRIMARY};margin:0 0 12px 0;font-weight:700;">'
            f"Dear {_esc(recipient_name)},</p>"
        )
    return f"""\
<!-- GREETING + INTRO -->
<table role="presentation" width="600" align="center" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;background:#ffffff;">
  <tr>
    <td style="padding:24px;">
      {greeting_line}
      <p style="font-family:Arial,sans-serif;font-size:15px;color:{TEXT_PRIMARY};line-height:1.55;margin:0;">
        {_esc(intro_paragraph)}
      </p>
    </td>
  </tr>
</table>
"""


def _render_highlights(articles: list[NewsletterArticle]) -> str:
    """2-column bullet grid with industry emoji + short headline."""
    # Split into two columns
    mid = (len(articles) + 1) // 2
    left = articles[:mid]
    right = articles[mid:]

    def _col(items: list[NewsletterArticle]) -> str:
        rows = []
        for a in items:
            rows.append(f"""\
        <tr>
          <td valign="top" style="padding:8px 0;width:32px;font-size:18px;">{a.emoji()}</td>
          <td valign="top" style="padding:8px 12px 8px 0;font-family:Arial,sans-serif;font-size:14px;color:{TEXT_PRIMARY};line-height:1.45;">
            {_esc(a.short_headline(110))}
          </td>
        </tr>""")
        inner = "\n".join(rows) or '<tr><td style="padding:8px;font-family:Arial,sans-serif;font-size:13px;color:%s;">—</td></tr>' % TEXT_MUTED
        return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{inner}</table>'

    return f"""\
<!-- HIGHLIGHTS -->
<table role="presentation" width="600" align="center" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;background:#ffffff;">
  <tr>
    <td style="padding:16px 24px 8px 24px;">
      <div style="background:{BG_CALLOUT};padding:8px 16px;font-family:Arial,sans-serif;font-size:18px;font-weight:700;color:{TEXT_PRIMARY};text-align:center;">
        Highlights
      </div>
    </td>
  </tr>
  <tr>
    <td style="padding:0 16px 12px 16px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td valign="top" width="50%" style="padding:0 8px;">{_col(left)}</td>
          <td valign="top" width="50%" style="padding:0 8px;">{_col(right)}</td>
        </tr>
      </table>
    </td>
  </tr>
  <tr>
    <td style="border-top:1px solid {BORDER_LIGHT};"></td>
  </tr>
</table>
"""


def _render_article_block(a: NewsletterArticle) -> str:
    """Full article section — image + headline + body + callouts + read-more."""
    if a.image_url:
        image_html = f"""\
      <tr>
        <td style="padding:0 0 16px 0;">
          <img src="{_esc(a.image_url)}" width="552" alt="" style="display:block;width:100%;max-width:552px;height:auto;border:0;line-height:0;" />
        </td>
      </tr>"""
    else:
        # Emoji banner fallback — black with orange accent rule.
        # (For articles ingested via NewsAPI.ai after Phase 9, `image_url` is
        #  populated from the publisher's metadata. Old articles + manual
        #  prompts fall through here.)
        image_html = f"""\
      <tr>
        <td style="padding:0 0 20px 0;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{BRAND_DARK};">
            <tr>
              <td align="center" style="padding:56px 16px 52px 16px;font-size:64px;color:{BRAND_PRIMARY};line-height:1;">
                {a.emoji()}
              </td>
            </tr>
            <tr>
              <td style="height:3px;line-height:3px;font-size:3px;background:{BRAND_PRIMARY};">&nbsp;</td>
            </tr>
          </table>
        </td>
      </tr>"""

    src_line = ""
    # Filter out internal / dev-only source names so "user_prompt", "test",
    # etc. never render as an uppercase byline in a prospect's email.
    _HIDDEN_SOURCES = {"user_prompt", "test", "dev", "prompt", "manual", ""}
    visible_source = (
        a.source_name if a.source_name and a.source_name.lower().strip() not in _HIDDEN_SOURCES else ""
    )
    if visible_source or a.published_at:
        pieces = []
        if visible_source:
            pieces.append(_esc(_prettify_source(visible_source)))
        if a.published_at:
            pieces.append(_format_date(a.published_at))
        src_line = (
            f'<p style="font-family:Arial,sans-serif;font-size:12px;color:{TEXT_MUTED};margin:0 0 8px 0;text-transform:uppercase;letter-spacing:0.5px;">'
            f"{' · '.join(pieces)}</p>"
        )

    # Guard against dead links — local file paths (prompt://, file://) or
    # empty URLs never show up as clickable. Fall back to snowkap.com so the
    # email always has a working CTA.
    _safe_url = a.read_more_url if a.read_more_url.startswith(("http://", "https://")) else "https://snowkap.com/"
    read_more_html = f"""\
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:4px 0 0 0;">
              <tr>
                <td style="background:{BRAND_DARK};border-radius:4px;">
                  <a href="{_esc(_safe_url)}" style="display:inline-block;padding:10px 22px;font-family:Arial,sans-serif;font-size:13px;font-weight:700;color:{BRAND_PRIMARY};text-decoration:none;letter-spacing:0.3px;border-radius:4px;">
                    {_esc(a.read_more_label)} &rarr;
                  </a>
                </td>
              </tr>
            </table>"""

    return f"""\
<!-- ARTICLE -->
<table role="presentation" width="600" align="center" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;background:#ffffff;">
  <tr>
    <td style="padding:24px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        {image_html}
        <tr>
          <td>
            {src_line}
            <h3 style="font-family:Georgia,'Times New Roman',serif;font-size:24px;color:{BRAND_DARK};margin:0 0 18px 0;line-height:1.3;font-weight:700;">
              {_esc(a.title)}
            </h3>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 14px 0;">
              <tr>
                <td style="border-left:4px solid {BRAND_PRIMARY};padding:4px 0 4px 14px;">
                  <p style="font-family:Arial,sans-serif;font-size:11px;font-weight:700;color:{BRAND_PRIMARY};margin:0 0 4px 0;text-transform:uppercase;letter-spacing:1px;">The bottom line</p>
                  <p style="font-family:Arial,sans-serif;font-size:15px;color:{TEXT_PRIMARY};line-height:1.55;margin:0;">
                    {_esc(a.bottom_line)}
                  </p>
                </td>
              </tr>
            </table>
            <p style="font-family:Arial,sans-serif;font-size:11px;font-weight:700;color:{TEXT_MUTED};margin:18px 0 6px 0;text-transform:uppercase;letter-spacing:1px;">Why this matters</p>
            <p style="font-family:Arial,sans-serif;font-size:15px;color:{TEXT_PRIMARY};line-height:1.6;margin:0 0 18px 0;">
              {_esc(a.why_matters)}
            </p>
            {read_more_html}
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
"""


def _render_cta(cta_url: str, cta_label: str, pre_cta_copy: str) -> str:
    """Prominent call-to-action block — the thing the editor / CFO clicks.

    Black background, orange CTA button — the Snowkap signature."""
    return f"""\
<!-- CTA (black + orange signature block) -->
<table role="presentation" width="600" align="center" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;background:#ffffff;">
  <tr>
    <td style="padding:24px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{BRAND_DARK};border-radius:8px;">
        <tr>
          <td align="center" style="padding:40px 28px 12px 28px;">
            <div style="display:inline-block;padding:4px 12px;background:{BRAND_PRIMARY};color:#ffffff;font-family:Arial,sans-serif;font-size:10px;font-weight:700;letter-spacing:1.8px;text-transform:uppercase;border-radius:2px;">
              Snowkap ESG
            </div>
          </td>
        </tr>
        <tr>
          <td align="center" style="padding:16px 28px 12px 28px;font-family:Georgia,'Times New Roman',serif;font-size:24px;color:#ffffff;font-weight:700;line-height:1.3;">
            Want this kind of analysis for <em style="color:{BRAND_PRIMARY};font-style:italic;">your</em> company?
          </td>
        </tr>
        <tr>
          <td align="center" style="padding:0 32px 22px 32px;font-family:Arial,sans-serif;font-size:14px;color:#D1D5DB;line-height:1.6;">
            {_esc(pre_cta_copy)}
          </td>
        </tr>
        <tr>
          <td align="center" style="padding:4px 28px 36px 28px;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td align="center" style="border-radius:6px;background:{BRAND_PRIMARY};">
                  <a href="{_esc(cta_url)}" style="display:inline-block;padding:14px 34px;font-family:Arial,sans-serif;font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:6px;letter-spacing:0.3px;">
                    {_esc(cta_label)} &rarr;
                  </a>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
"""


def _render_footer(address: str, website: str = "https://snowkap.com/") -> str:
    return f"""\
<!-- FOOTER: dark, minimal, matches the black+orange brand -->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{BRAND_DARK};">
  <tr>
    <td style="padding:28px 0 32px 0;">
      <table role="presentation" width="600" align="center" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;">
        <tr>
          <td align="center" style="padding:0 16px 14px 16px;">
            <span style="font-family:Georgia,'Times New Roman',serif;font-size:20px;font-weight:700;color:#ffffff;letter-spacing:1.2px;line-height:1;">
              <span style="color:{BRAND_PRIMARY};">S</span>NOWKAP
            </span>
          </td>
        </tr>
        <tr>
          <td align="center" style="font-family:Arial,sans-serif;font-size:12px;color:#9CA3AF;line-height:1.7;padding:0 16px;">
            {_esc(address)}<br/>
            <a href="{_esc(website)}" style="color:{BRAND_PRIMARY};text-decoration:none;font-weight:600;">{_esc(website)}</a>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
"""


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


def render_newsletter(
    articles: list[NewsletterArticle],
    recipient_name: str | None = None,
    send_date: str | None = None,
    newsletter_title: str = DEFAULT_NEWSLETTER_TITLE,
    tagline: str = DEFAULT_TAGLINE,
    intro_paragraph: str | None = None,
    cta_url: str = DEFAULT_CTA_URL,
    cta_label: str = DEFAULT_CTA_LABEL,
    pre_cta_copy: str | None = None,
    unsubscribe_url: str | None = None,
    footer_address: str = DEFAULT_FOOTER_ADDRESS,
) -> str:
    """Assemble the full HTML newsletter.

    `articles` should have 3-8 entries. Highlights grid assumes 2-column split.
    `intro_paragraph` auto-generated from article industries if not supplied.
    """
    if not articles:
        raise ValueError("newsletter needs at least 1 article")

    date_str = _format_date(send_date) if send_date else _format_date()

    if intro_paragraph is None:
        industries = sorted({a.industry for a in articles})
        ind_phrase = ", ".join(industries[:3]) + (" and more" if len(industries) > 3 else "")
        intro_paragraph = (
            f"This week's ESG signals across {ind_phrase}. Each story below includes our reading of "
            f"what it means for your P&L, what the board should know, and what we'd recommend if "
            f"you were acting on it."
        )

    if pre_cta_copy is None:
        pre_cta_copy = (
            "See the ESG risks moving your numbers — before they hit the "
            "boardroom. Decision-ready briefs for every signal that matters."
        )

    blocks: list[str] = []
    blocks.append(_render_header(newsletter_title, tagline, date_str, unsubscribe_url))
    blocks.append(_render_greeting(recipient_name, intro_paragraph))
    # Highlights only make sense for multi-article digests — a single-article
    # share would just duplicate what's shown below. Skip to keep the layout
    # tight + professional.
    if len(articles) >= 2:
        blocks.append(_render_highlights(articles))
    for a in articles:
        blocks.append(_render_article_block(a))
    blocks.append(_render_cta(cta_url, cta_label, pre_cta_copy))
    blocks.append(_render_footer(footer_address))

    body = "\n".join(blocks)

    # Minimal HTML shell — no external CSS, email-client-safe
    return f"""\
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{_esc(newsletter_title)} — {_esc(date_str)}</title>
</head>
<body style="margin:0;padding:0;background:{BG_LIGHT};font-family:Arial,sans-serif;">
{body}
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Adapter: convert pipeline JSON outputs → NewsletterArticle dataclasses
# ---------------------------------------------------------------------------


def build_articles_from_outputs(
    slugs: list[str],
    outputs_root: Path,
    max_count: int = 6,
    read_more_base: str | None = None,
    article_source_resolver: callable = None,  # type: ignore[valid-type]
) -> list[NewsletterArticle]:
    """Scan data/outputs/<slug>/insights/ for the most recent HOME articles
    across the given company slugs and convert each to a NewsletterArticle.

    Pulls:
      - article.title (from pipeline payload)
      - article.url (→ read_more_url, unless `read_more_base` provided)
      - insight.decision_summary.key_risk → bottom_line
      - CEO perspective board_paragraph first 200 chars → why_matters
      - article image from input metadata (if NewsAPI.ai captured one)
    """
    import json

    # Load companies.json to map slug → industry + name
    try:
        from engine.config import load_companies
        companies = {c.slug: c for c in load_companies()}
    except Exception:  # noqa: BLE001
        companies = {}

    candidates: list[tuple[str, Path]] = []  # (slug, path)
    for slug in slugs:
        insights_dir = outputs_root / slug / "insights"
        if not insights_dir.exists():
            continue
        for p in sorted(insights_dir.glob("*.json"), reverse=True):
            candidates.append((slug, p))

    # Order by filename date (descending — most recent first)
    candidates.sort(key=lambda t: t[1].name, reverse=True)

    result: list[NewsletterArticle] = []
    for slug, path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        insight = payload.get("insight") or {}
        if not insight:
            continue  # SECONDARY / REJECTED — skip

        article_meta = payload.get("article") or {}
        title = insight.get("headline") or article_meta.get("title") or ""
        article_url = article_meta.get("url") or ""

        # Bottom line = decision_summary.key_risk or net_impact_summary
        ds = insight.get("decision_summary") or {}
        bottom_line = ds.get("key_risk") or insight.get("net_impact_summary") or ""
        bottom_line = bottom_line[:400]

        # Why matters = CEO board paragraph (first 250 chars) or top_opportunity
        article_id = path.stem.split("_", 1)[1] if "_" in path.stem else path.stem
        why_matters = _load_ceo_board_excerpt(outputs_root / slug, article_id)
        if not why_matters:
            why_matters = ds.get("top_opportunity") or ""
        why_matters = why_matters[:400]

        # Image — try the input article JSON metadata
        image_url = _load_article_image(outputs_root.parent / "inputs" / "news" / slug, article_id)

        company = companies.get(slug)
        industry = (company.industry if company else "Other") or "Other"
        company_name = (company.name if company else slug.replace("-", " ").title())

        read_more_url = article_url
        if read_more_base:
            read_more_url = f"{read_more_base.rstrip('/')}/{slug}/{article_id}"

        result.append(NewsletterArticle(
            title=title,
            company_name=company_name,
            industry=industry,
            bottom_line=bottom_line,
            why_matters=why_matters,
            read_more_url=read_more_url,
            read_more_label="Read the full Snowkap brief",
            image_url=image_url,
            published_at=article_meta.get("published_at", ""),
            source_name=_prettify_source(article_meta.get("source") or article_source_resolver and article_source_resolver(article_meta) or ""),
            article_id=article_id,
        ))
        if len(result) >= max_count:
            break

    return result


def _load_ceo_board_excerpt(company_dir: Path, article_id: str) -> str:
    """Pull the first 250 chars of the CEO board paragraph, if available."""
    import json
    folder = company_dir / "perspectives" / "ceo"
    if not folder.exists():
        return ""
    matches = list(folder.glob(f"*_{article_id}.json"))
    if not matches:
        return ""
    try:
        data = json.loads(matches[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    bp = data.get("board_paragraph") or ""
    return bp[:250].strip()


def _load_article_image(company_input_dir: Path, article_id: str) -> str:
    """Return article image URL from NewsAPI.ai metadata, if captured."""
    import json
    if not company_input_dir.exists():
        return ""
    matches = list(company_input_dir.glob(f"*_{article_id}.json"))
    if not matches:
        return ""
    try:
        data = json.loads(matches[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return (data.get("metadata") or {}).get("image_url", "")


def _prettify_source(raw: str) -> str:
    """'www.economictimes.com' → 'Economic Times'. Keeps originals if not a domain."""
    if not raw:
        return ""
    if "." not in raw or " " in raw:
        return raw
    # crude domain → name
    host = raw.replace("www.", "").split(".")[0]
    return host.replace("-", " ").title()


# =============================================================================
# Phase 10 — Rich dark-card single-article email
# =============================================================================
#
# Used by `share_service.share_article_by_email` and the campaign runner for
# single-article sends. Takes the full pipeline payload (not just a
# NewsletterArticle) so it can surface Executive Summary, Key Insights,
# sentiment/materiality/industry pills, framework chips, and impacted metrics.
#
# Design: ET Sustainability-inspired dark card layout on a near-black body,
# orange accents, Snowkap brand. Renders correctly without images (inline SVG
# logo + pure-CSS wordmark fallback).


_SENTIMENT_LABEL = {
    -2: ("Very Negative", "#DC2626"),
    -1: ("Negative", "#F97316"),
    0: ("Neutral", "#F59E0B"),
    1: ("Positive", "#10B981"),
    2: ("Very Positive", "#059669"),
}

_MATERIALITY_COLOUR = {
    "CRITICAL": "#DC2626",
    "HIGH": "#F97316",
    "MODERATE": "#F59E0B",
    "LOW": "#10B981",
    "NON-MATERIAL": "#64748B",
}


def _sentiment_display(nlp_sentiment: int | float | None) -> tuple[str, str]:
    try:
        n = int(nlp_sentiment) if nlp_sentiment is not None else 0
    except (TypeError, ValueError):
        n = 0
    n = max(-2, min(2, n))
    return _SENTIMENT_LABEL.get(n, ("Neutral", "#F59E0B"))


def _materiality_display(m: str) -> tuple[str, str, str]:
    """Return (label, X/5 score text, colour)."""
    m = (m or "").upper()
    score_map = {"CRITICAL": "5/5", "HIGH": "4/5", "MODERATE": "3/5", "LOW": "2/5", "NON-MATERIAL": "1/5"}
    label = m if m in _MATERIALITY_COLOUR else "LOW"
    return label, score_map.get(label, "2/5"), _MATERIALITY_COLOUR.get(label, "#10B981")


def _extract_framework_summary(pipeline: dict) -> list[tuple[str, list[str], dict]]:
    """Return [(framework_label, [section_codes], extras_dict)] from the payload.

    The pipeline emits frameworks as dicts with `framework_id` +
    `framework_label` + `triggered_sections` + `triggered_by_themes` +
    `applicable_deadlines` + `is_mandatory`. We surface only frameworks
    whose relevance ≥ 0.5 so the email shows signal, not noise.

    `extras_dict` carries themes, deadlines, mandatory flag so the renderer
    can show something meaningful even when `triggered_sections` is empty.
    """
    raw = pipeline.get("frameworks") or []
    buckets: dict[str, list[str]] = {}
    extras: dict[str, dict] = {}
    for fw in raw:
        if not isinstance(fw, dict):
            continue
        fid = (
            fw.get("framework_id")
            or fw.get("framework_label")
            or fw.get("id")
            or fw.get("framework")
            or ""
        )
        label = fw.get("framework_label") or fid
        secs = fw.get("triggered_sections") or fw.get("section") or []
        relevance = float(fw.get("relevance") or 0)
        if not fid:
            if isinstance(secs, list) and secs:
                first = secs[0]
                fid = str(first).split(":", 1)[0] if ":" in str(first) else "Framework"
                label = fid
            else:
                continue
        if relevance > 0 and relevance < 0.5:
            continue
        key = str(label).strip() or str(fid).strip()
        if not key:
            continue
        if isinstance(secs, str):
            secs = [secs]
        for s in secs:
            s = str(s).strip()
            if s and s not in buckets.setdefault(key, []):
                buckets[key].append(s)
        buckets.setdefault(key, [])
        # First-write wins on the extras bag
        if key not in extras:
            extras[key] = {
                "themes": list(fw.get("triggered_by_themes") or [])[:3],
                "deadlines": list(fw.get("applicable_deadlines") or [])[:2],
                "mandatory": bool(fw.get("is_mandatory")),
            }
    return [(k, buckets[k], extras.get(k, {})) for k in list(buckets.keys())[:6]]


def _brand_header_dark() -> str:
    """Black bar with Snowkap logo — CID-attached PNG, no text duplication.

    The asset (480px source @ 2x retina) is attached inline via Resend's
    `attachments` param with `content_id=snowkap-logo`. The <img> tag
    references it via `cid:snowkap-logo`, which every major email client
    (Outlook Desktop + 365, Gmail, Apple Mail, iOS Mail) renders
    immediately — no "right-click to download" placeholder.

    Display size is 240x40 (up from 180x29 in v11) so the brand reads
    clearly in the header of desktop clients without pixelation — the
    source is 480px wide so retina displays stay sharp.

    The styled `alt="SNOWKAP"` covers the ~0% edge case where a client
    strips attachments entirely.
    """
    logo_block = (
        '<img src="cid:snowkap-logo" width="240" height="40" '
        'alt="SNOWKAP" '
        'style="display:block;margin:0 auto;border:0;outline:none;'
        'text-decoration:none;max-width:240px;height:auto;'
        'color:#ffffff;font-family:Georgia,serif;font-size:28px;'
        'font-weight:700;letter-spacing:2.4px;line-height:40px;" />'
    )
    return f"""\
<!-- HEADER: black bar with Snowkap logo -->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{BRAND_DARK};">
  <tr>
    <td align="center" style="padding:30px 0 12px 0;">
      {logo_block}
    </td>
  </tr>
  <tr>
    <td align="center" style="padding:0 0 26px 0;font-family:Arial,sans-serif;font-size:10px;color:{BRAND_PRIMARY};letter-spacing:3px;text-transform:uppercase;font-weight:700;">
      ESG Intelligence
    </td>
  </tr>
  <tr>
    <td style="height:3px;line-height:3px;font-size:3px;background:{BRAND_PRIMARY};">&nbsp;</td>
  </tr>
</table>
"""


def _pill_cell(label: str, value: str, colour: str) -> str:
    return f"""\
            <td valign="middle" align="center" width="33%" style="padding:18px 10px;">
              <div style="font-family:Arial,sans-serif;font-size:10px;color:#94A3B8;letter-spacing:1.6px;text-transform:uppercase;font-weight:700;margin-bottom:8px;line-height:1.2;">{_esc(label)}</div>
              <div style="font-family:Arial,sans-serif;font-size:16px;color:{colour};font-weight:700;line-height:1.2;letter-spacing:0.2px;">{_esc(value)}</div>
            </td>"""


def _render_section_card(title: str, body_html: str) -> str:
    """Generic dark-card section with title + body.

    Editorial typography: tight section header (11px orange caps),
    generous body (14px, line-height 1.6). 22px inner padding for
    consistent rhythm across the email.

    No emoji icon: Outlook renders many emoji ("📊", "💡", "🏛", "📋") as
    fragmented colour-glyph boxes via its Segoe fallback font. The orange
    3px left border + bold orange title are the visual markers — we don't
    need a third redundant cue. If a per-section glyph is ever wanted,
    embed a sized <table> coloured block (not an emoji)."""
    # `text-align:left` is explicit on every <td> because the dark-brief
    # wrapper uses `align="center"` at the outermost row, and Outlook's
    # Word engine inherits that as text-align:center on all descendants
    # (Gmail/Apple Mail do NOT — they treat align= as scope-limited).
    # Without these overrides, section titles + bullet lists render
    # centered in Outlook only, which is confusing and amateurish.
    return f"""\
<tr>
  <td align="left" style="padding:0 20px 14px 20px;text-align:left;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#1E293B;border-radius:8px;border-left:3px solid {BRAND_PRIMARY};">
      <tr>
        <td align="left" style="padding:20px 24px 8px 24px;text-align:left;">
          <div style="font-family:Arial,sans-serif;font-size:11px;font-weight:800;color:{BRAND_PRIMARY};letter-spacing:1.8px;text-transform:uppercase;line-height:1.4;text-align:left;">
            {_esc(title)}
          </div>
        </td>
      </tr>
      <tr>
        <td align="left" style="padding:4px 24px 22px 24px;text-align:left;">
          {body_html}
        </td>
      </tr>
    </table>
  </td>
</tr>
"""


def _render_key_insights(items: list[str]) -> str:
    """Numbered list with rounded-square orange badges.

    Outlook Word-engine ignores `border-radius` on `<div>`, so we use a
    nested fixed-size `<table>` with `border-radius:13px` (half of 26x26).
    Gmail/Apple Mail render it as a perfect circle; Outlook Desktop renders
    a rounded square — acceptable degradation.

    Badge ↔ first-line alignment (v17): Outlook adds extra intrinsic
    vertical space above a 26x26 nested-table cell compared with a 14px
    text baseline, pushing the badge visually lower than its neighbour's
    first line. Counteract with a negative top-padding gap: badge cell
    gets 2px top-padding, text cell gets 6px, so the badge sits ~4px
    higher than the text cell's padding box — which, after Outlook's
    intrinsic offset, lands badge-center on text-first-line-visual-center.
    Bottom padding is symmetric (10px) so multi-item spacing stays even."""
    rows = []
    for i, text in enumerate(items, start=1):
        if not text:
            continue
        rows.append(f"""\
            <tr>
              <td valign="top" align="left" width="34" style="padding:2px 14px 14px 0;text-align:left;line-height:0;font-size:0;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="background:{BRAND_PRIMARY};border-radius:13px;width:26px;height:26px;">
                  <tr>
                    <td align="center" valign="middle" width="26" height="26" style="font-family:Arial,sans-serif;font-size:13px;font-weight:700;color:#ffffff;line-height:26px;text-align:center;mso-line-height-rule:exactly;">{i}</td>
                  </tr>
                </table>
              </td>
              <td valign="top" align="left" style="padding:6px 0 14px 0;font-family:Arial,sans-serif;font-size:14px;color:#E2E8F0;line-height:22px;text-align:left;mso-line-height-rule:exactly;">
                {_esc(text)}
              </td>
            </tr>""")
    inner = "\n".join(rows) or ""
    # `align="left"` on the outer table stops Outlook from centering the
    # whole numbered-list block inside the section card.
    return f'<table role="presentation" width="100%" align="left" cellpadding="0" cellspacing="0" border="0" style="text-align:left;">{inner}</table>'


def _render_framework_chips(buckets: list[tuple[str, list[str], dict]]) -> str:
    """Two-column list: [framework label]  [section chips | themes | deadline].

    Drops the unprofessional "(sections TBD)" placeholder from the previous
    design. When `triggered_sections` is empty we surface the richer
    payload fields instead:
      - `triggered_by_themes` as theme chips (e.g. "Energy · Climate Change")
      - First `applicable_deadlines` entry as a small badge
      - `is_mandatory=True` → "Mandatory" pill in orange
    """
    if not buckets:
        return '<div style="font-family:Arial,sans-serif;font-size:13px;color:#94A3B8;">No framework mappings available.</div>'

    def _chip(text: str, *, border: str = "#334155", bg: str = "#0F172A", fg: str = "#CBD5E1",
              weight: int = 500) -> str:
        return (
            f'<span style="display:inline-block;padding:4px 10px;margin:0 6px 0 0;'
            f'border:1px solid {border};border-radius:4px;font-family:Arial,sans-serif;'
            f'font-size:11px;font-weight:{weight};color:{fg};background:{bg};'
            f'white-space:nowrap;letter-spacing:0.3px;">{_esc(text)}</span>'
        )

    rows = []
    for fid, sections, extras in buckets:
        # Each signal type goes on its OWN line for readability:
        #   line 1 → real section codes (if any)
        #   line 2 → Mandatory pill + themes
        #   line 3 → next deadline
        # Previously all chips were crammed on one line and ran together.
        line_parts: list[str] = []

        # Line 1: real section codes (e.g. GRI:302, BRSR:P6)
        if sections:
            section_chips = "".join(
                _chip(s, border=BRAND_PRIMARY, fg=BRAND_PRIMARY, weight=700)
                for s in sections[:4]
            )
            line_parts.append(
                f'<div style="margin:0 0 6px 0;line-height:24px;">{section_chips}</div>'
            )

        # Line 2: Mandatory flag + themes (always informative)
        line2_chips: list[str] = []
        if extras.get("mandatory"):
            line2_chips.append(
                _chip("Mandatory", border=BRAND_PRIMARY, bg="#2A1810",
                      fg=BRAND_PRIMARY, weight=700)
            )
        for theme in (extras.get("themes") or [])[:3]:
            line2_chips.append(_chip(theme))
        if line2_chips:
            line_parts.append(
                f'<div style="margin:0 0 6px 0;line-height:24px;">{"".join(line2_chips)}</div>'
            )

        # Line 3: next deadline (when no section codes AND not mandatory — rare)
        deadlines = extras.get("deadlines") or []
        if deadlines and not sections and not extras.get("mandatory"):
            line_parts.append(
                f'<div style="line-height:24px;">{_chip(deadlines[0])}</div>'
            )

        cell_inner = (
            "".join(line_parts)
            if line_parts
            else '<span style="font-family:Arial,sans-serif;font-size:12px;color:#64748B;">—</span>'
        )
        rows.append(f"""\
            <tr>
              <td valign="top" align="left" width="84" style="padding:10px 14px 10px 0;font-family:Arial,sans-serif;font-size:12px;font-weight:700;color:{BRAND_PRIMARY};letter-spacing:1px;text-transform:uppercase;text-align:left;">{_esc(fid)}</td>
              <td valign="top" align="left" style="padding:10px 0 10px 0;text-align:left;">{cell_inner}</td>
            </tr>""")
    return f'<table role="presentation" width="100%" align="left" cellpadding="0" cellspacing="0" border="0" style="text-align:left;">{"".join(rows)}</table>'


def _render_metrics_list(metrics: list[str]) -> str:
    """Bullet list of impacted metrics / sub-indicators.

    v17: dropped the legacy `<ul>/<li>` implementation. Outlook Word-engine
    ignores `list-style-type` in half of contexts, adds inconsistent top
    margins, and occasionally clips the last `<li>`'s descender when it
    sits at the bottom of a cell. A 2-column `<table>` with an explicit
    orange `■` marker renders identically everywhere and guarantees every
    row gets predictable bottom spacing."""
    if not metrics:
        return '<div style="font-family:Arial,sans-serif;font-size:13px;color:#94A3B8;text-align:left;">No impacted metrics identified.</div>'
    rows = []
    for m in metrics[:8]:
        pretty = m.replace("_", " ").title()
        rows.append(f"""\
            <tr>
              <td valign="top" align="left" width="18" style="padding:4px 10px 8px 0;text-align:left;font-family:Arial,sans-serif;font-size:14px;color:{BRAND_PRIMARY};line-height:1.5;">&#9632;</td>
              <td valign="top" align="left" style="padding:4px 0 8px 0;text-align:left;font-family:Arial,sans-serif;font-size:14px;color:#E2E8F0;line-height:1.5;">{_esc(pretty)}</td>
            </tr>""")
    return f'<table role="presentation" width="100%" align="left" cellpadding="0" cellspacing="0" border="0" style="text-align:left;">{"".join(rows)}</table>'


def render_article_brief_dark(
    *,
    payload: dict,
    company_name: str,
    industry: str,
    recipient_name: str | None,
    cta_url: str = DEFAULT_CTA_URL,
    cta_label: str = DEFAULT_CTA_LABEL,
    article_url_override: str | None = None,
) -> str:
    """Render a single-article brief as an ET-Sustainability-style dark card email.

    Expects the full pipeline payload (article + pipeline + insight + perspectives).
    Falls back gracefully when any field is missing.
    """
    article = payload.get("article") or {}
    pipeline = payload.get("pipeline") or {}
    insight = payload.get("insight") or {}
    decision = insight.get("decision_summary") or {}
    nlp = pipeline.get("nlp") or {}
    themes = pipeline.get("themes") or {}

    title = insight.get("headline") or article.get("title") or "ESG Intelligence Brief"
    source = article.get("source") or ""
    published = article.get("published_at") or ""
    article_url = article_url_override or article.get("url") or ""
    if not article_url.startswith(("http://", "https://")):
        article_url = cta_url

    sentiment_label, sentiment_colour = _sentiment_display(nlp.get("sentiment"))
    mat_label, mat_score, mat_colour = _materiality_display(decision.get("materiality") or "")

    executive_summary = (
        insight.get("net_impact_summary")
        or decision.get("key_risk")
        or "Analysis pending — no executive summary available."
    )

    key_insights: list[str] = []
    if insight.get("core_mechanism"):
        key_insights.append(str(insight["core_mechanism"]))
    if decision.get("key_risk"):
        key_insights.append(f"Key risk: {decision['key_risk']}")
    if decision.get("top_opportunity"):
        key_insights.append(f"Opportunity: {decision['top_opportunity']}")

    frameworks = _extract_framework_summary(pipeline)
    metrics = list(themes.get("primary_sub_metrics") or [])

    greeting = (
        f'<div style="font-family:Arial,sans-serif;font-size:14px;color:#CBD5E1;margin-bottom:6px;">Dear {_esc(recipient_name)},</div>'
        if recipient_name else ""
    )

    source_line_pieces = []
    if source and source.lower().strip() not in {"user_prompt", "test", "dev", "prompt", "manual", ""}:
        source_line_pieces.append(_prettify_source(source))
    if published:
        source_line_pieces.append(f"Generated {_format_date(published)}")
    source_line = " · ".join(source_line_pieces) if source_line_pieces else ""

    # Framework card body
    fw_card = ""
    if frameworks:
        fw_card = _render_section_card(
            title="ESG Frameworks Mapped",
            body_html=(
                '<p style="font-family:Arial,sans-serif;font-size:12px;color:#94A3B8;margin:0 0 12px 0;line-height:1.6;text-align:left;">'
                "Sections of major ESG disclosure standards triggered by this signal."
                "</p>"
                + _render_framework_chips(frameworks)
            ),
        )

    metrics_card = ""
    if metrics:
        metrics_card = _render_section_card(
            title="Impacted Metrics & Sub-indicators",
            body_html=_render_metrics_list(metrics),
        )

    key_insights_card = ""
    if key_insights:
        key_insights_card = _render_section_card(
            title="Key Insights",
            body_html=_render_key_insights(key_insights),
        )

    return f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
  <title>{_esc(title)}</title>
</head>
<body style="margin:0;padding:0;background:{BRAND_DARK};color:#E2E8F0;-webkit-font-smoothing:antialiased;">

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{BRAND_DARK};">
  <tr>
    <td align="center" style="padding:0;">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="background:{BRAND_DARK};">

        {_brand_header_dark()}

        <!-- HERO card — matches the left-inset of sections below for
             consistent visual rhythm. Orange accent rule on left like the
             insight cards so the hero reads as "the article in focus". -->
        <tr>
          <td style="padding:28px 20px 16px 20px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#1E293B;border-radius:8px;border-left:3px solid {BRAND_PRIMARY};">
              <tr>
                <td style="padding:18px 22px 6px 22px;font-family:Arial,sans-serif;font-size:11px;font-weight:700;color:{BRAND_PRIMARY};letter-spacing:2px;text-transform:uppercase;">
                  ESG Signal &nbsp;·&nbsp; {_esc(company_name)}
                  {(' &nbsp;·&nbsp; ' + _format_date(article.get('published_at') or '')) if article.get('published_at') else ''}
                </td>
              </tr>
              <tr>
                <td style="padding:4px 22px 4px 22px;">
                  {greeting}
                </td>
              </tr>
              <tr>
                <td style="padding:2px 22px 12px 22px;font-family:Georgia,'Times New Roman',serif;font-size:22px;font-weight:700;color:#ffffff;line-height:1.3;">
                  {_esc(title)}
                </td>
              </tr>
              <tr>
                <td style="padding:0 22px 20px 22px;">
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr>
                      <td valign="middle" style="font-family:Arial,sans-serif;font-size:12px;color:#94A3B8;letter-spacing:0.3px;">
                        {_esc(source_line) if source_line else ""}
                      </td>
                      <td valign="middle" align="right">
                        <a href="{_esc(article_url)}" style="display:inline-block;padding:9px 18px;font-family:Arial,sans-serif;font-size:12px;font-weight:700;color:{BRAND_PRIMARY};text-decoration:none;border:1px solid {BRAND_PRIMARY};border-radius:4px;letter-spacing:0.3px;white-space:nowrap;">
                          Read article &rarr;
                        </a>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Metadata pills row (Sentiment | Criticality | Industry) -->
        <tr>
          <td style="padding:0 20px 20px 20px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#1E293B;border-radius:8px;">
              <tr>
                {_pill_cell("Market Sentiment", sentiment_label, sentiment_colour)}
                <td width="1" style="background:#334155;padding:14px 0;">&nbsp;</td>
                {_pill_cell("Criticality", f"{mat_label} · {mat_score}", mat_colour)}
                <td width="1" style="background:#334155;padding:14px 0;">&nbsp;</td>
                {_pill_cell("Industry", industry or "—", "#CBD5E1")}
              </tr>
            </table>
          </td>
        </tr>

        <!-- Executive Summary card -->
        {_render_section_card(
            title="Executive Summary",
            body_html=f'<p style="font-family:Georgia,\'Times New Roman\',serif;font-size:15px;color:#E2E8F0;line-height:1.7;margin:0;letter-spacing:0.1px;text-align:left;">{_esc(executive_summary)}</p>',
        )}

        <!-- Key Insights -->
        {key_insights_card}

        <!-- ESG Frameworks -->
        {fw_card}

        <!-- Impacted Metrics -->
        {metrics_card}

        <!-- CTA block — orange-bordered card with strong call-to-action. -->
        <tr>
          <td style="padding:12px 20px 28px 20px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#1E293B;border-radius:8px;border:1px solid {BRAND_PRIMARY};">
              <tr>
                <td align="center" style="padding:30px 24px 6px 24px;font-family:Georgia,'Times New Roman',serif;font-size:20px;color:#ffffff;font-weight:700;line-height:1.3;letter-spacing:0.2px;">
                  Get this for <em style="color:{BRAND_PRIMARY};font-style:italic;">your</em> company
                </td>
              </tr>
              <tr>
                <td align="center" style="padding:8px 40px 18px 40px;font-family:Arial,sans-serif;font-size:13px;color:#94A3B8;line-height:1.6;">
                  See how ESG signals move your frameworks, margins, and ratings — before they hit the boardroom.
                </td>
              </tr>
              <tr>
                <td align="center" style="padding:6px 24px 30px 24px;">
                  <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                    <tr>
                      <td style="background:{BRAND_PRIMARY};border-radius:4px;">
                        <a href="{_esc(cta_url)}" style="display:inline-block;padding:14px 34px;font-family:Arial,sans-serif;font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;letter-spacing:0.2px;">
                          {_esc(cta_label)} &rarr;
                        </a>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Footer — same CID logo, smaller size. No CSS wordmark. -->
        <tr>
          <td align="center" style="padding:28px 24px 8px 24px;border-top:1px solid #1E293B;">
            <img src="cid:snowkap-logo" width="168" height="28" alt="SNOWKAP"
              style="display:inline-block;border:0;outline:none;max-width:168px;height:auto;
              color:#ffffff;font-family:Georgia,serif;font-size:20px;font-weight:700;letter-spacing:1.6px;line-height:28px;" />
          </td>
        </tr>
        <tr>
          <td align="center" style="padding:6px 24px 28px 24px;font-family:Arial,sans-serif;font-size:11px;color:#64748B;">
            ESG Intelligence Platform
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>

</body>
</html>
"""
