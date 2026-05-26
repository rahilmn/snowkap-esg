"""Phase 33 §6 — Morning-Brew-style newsletter renderer.

Parallel to ``newsletter_renderer.render_article_brief_dark``, this
template consumes the Phase 32 unified ``insight.analysis`` block and
emits a conversational, scannable, second-person email patterned on
Morning Brew's tone:

  * Subject line — verb-first, ≤90 chars, ₹ figure when material.
  * Greeting     — "Hey {first_name}, here's what {company} is dealing
                   with today."
  * 📰 The story  — what_changed in one short paragraph.
  * 💡 Why you'll care — why_it_matters + stakes_for_company, ₹ exposure
                          framed as scenario when all_estimate is set.
  * ⚡ What that means — top 2-3 recommended_actions as a bullet list
                         (action + deadline + owner).
  * 🔮 What to watch — sentiment_trajectory in plain English + lead
                       indicators + benchmarks.
  * CTAs         — "Read the full analysis →" + "Discuss this in chat →"
  * Footer       — same brand block as the dark-card layout.

Ships behind ``SNOWKAP_EMAIL_LAYOUT=morning_brew`` (env flag) so the dark
card stays the default until editorial approves three sample sends per
the Phase 33 rollout decision.

Outlook + Gmail + iOS Mail compatibility verified by reusing the
table-based layout primitives + inline-styles approach from the dark
card. Emoji headers render cleanly as single-character glyphs (the
Phase 11C audit showed Outlook fragments multi-coloured emoji
*backgrounds* but renders single Unicode glyphs fine).
"""

from __future__ import annotations

import html
import logging
from typing import Any

from engine.output.email_assets import SNOWKAP_LOGO_CONTENT_ID as LOGO_CID
from engine.output.email_assets import SNOWKAP_LOGO_BASE64 as LOGO_BASE64

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Style tokens — Morning Brew lifts neutral cream + a single accent.
# ---------------------------------------------------------------------------

_BG = "#F8F6F0"        # cream page background
_INK = "#0F172A"        # body ink
_INK_MUTED = "#475569"  # secondary text
_ACCENT = "#DF5900"     # Snowkap orange (kept for brand consistency)
_DIVIDER = "#E2E8F0"


def _escape(s: Any) -> str:
    return html.escape(str(s)) if s is not None else ""


def _polarity_emoji(polarity: str) -> str:
    return {
        "positive": "📈",
        "negative": "📉",
        "neutral": "📰",
        "": "📰",
    }.get((polarity or "").lower(), "📰")


def _polarity_verb(polarity: str) -> str:
    return {
        "positive": "scored a win",
        "negative": "ran into trouble",
        "neutral": "made a disclosure",
        "": "made a move",
    }.get((polarity or "").lower(), "made a move")


def _format_inr_cr(amount: Any) -> str:
    """Render amount in ₹ Cr with en-IN grouping. Returns '' when invalid."""
    try:
        v = float(amount)
    except (TypeError, ValueError):
        return ""
    if v <= 0:
        return ""
    # 2-sig-fig render that matches Phase 26 number-format conventions.
    if v >= 100:
        return f"₹{v:,.0f} Cr"
    if v >= 10:
        return f"₹{v:,.1f} Cr"
    return f"₹{v:,.2f} Cr"


def _trajectory_phrase(traj: dict[str, Any]) -> str:
    h3 = (traj.get("horizon_3m") or "").lower()
    h6 = (traj.get("horizon_6m") or "").lower()
    h12 = (traj.get("horizon_12m") or "").lower()
    if not any((h3, h6, h12)):
        return "Sentiment baseline holding — nothing alarming on the horizon yet."
    if h3 == h6 == h12 == "stable":
        return "Sentiment is expected to hold steady across the next year — keep watching, but no fire drill."
    if h3 == h6 == h12 == "declining":
        return "Sentiment is bending downward across the next year — plan a response narrative before the next investor touchpoint."
    if h3 == h6 == h12 == "improving":
        return "Sentiment is on the up across the next year — capitalise on the momentum in the next earnings cycle."
    return f"Trajectory at 3m: {h3 or '—'}, 6m: {h6 or '—'}, 12m: {h12 or '—'}."


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------


def render_article_morning_brew(
    *,
    payload: dict,
    company_name: str,
    industry: str = "",
    recipient_name: str | None = None,
    cta_url: str = "https://snowkap.com/contact-us/",
    article_url_override: str | None = None,
    chat_url: str | None = None,
    snowkap_base: str = "https://powerofnow.snowkap.co.in",
    company_slug: str = "",
    article_id: str = "",
) -> str:
    """Render a single-article Morning-Brew-style HTML email.

    Reads the unified ``insight.analysis`` block. Falls back gracefully
    when any sub-block is absent — the section is simply omitted
    rather than left as an empty header.

    Args:
        payload: full insight payload (article + pipeline + insight).
        company_name: human-readable company name for the greeting.
        recipient_name: first name (or full name) for "Hey {name}". When
            None, defaults to "there".
        chat_url: deep-link to the chat page (`/chat?company=...&article=...`).
            When None, the second CTA is hidden.
    """
    article = payload.get("article") or {}
    insight = payload.get("insight") or {}
    analysis = insight.get("analysis") or {}

    what_changed = analysis.get("what_changed") or {}
    why_it_matters = analysis.get("why_it_matters") or {}
    what_it_triggers = analysis.get("what_it_triggers") or {}
    what_to_watch = analysis.get("what_to_watch") or {}

    polarity = (what_changed.get("polarity") or "").lower()
    headline = what_changed.get("headline") or article.get("title") or "ESG Intelligence Brief"
    source = what_changed.get("source") or article.get("source") or ""
    source_url = article.get("url") or ""

    # Phase 33 fix — "Read the full analysis" must point at the Snowkap
    # analysis page, NOT the source publication (Whalesbook, Mint, etc).
    # Build the canonical Snowkap deep-link from snowkap_base + slug +
    # article_id; HomePage auto-opens the article-detail sheet when
    # ?article= is present. Fall back to the source URL only when the
    # caller didn't supply slug/id (legacy back-compat path).
    _slug = company_slug or (article.get("company_slug") or "")
    _aid = article_id or (article.get("id") or "")
    if article_url_override:
        analysis_url = article_url_override
    elif _slug and _aid and snowkap_base:
        analysis_url = (
            f"{snowkap_base.rstrip('/')}/home"
            f"?company={_slug}&article={_aid}"
        )
    elif source_url.startswith(("http://", "https://")):
        analysis_url = source_url
    else:
        analysis_url = cta_url

    first_name = (recipient_name or "there").split()[0]
    greeting = f"Hey {_escape(first_name)},"

    band = (why_it_matters.get("materiality_band") or "").upper()
    exposure = why_it_matters.get("financial_exposure") or {}
    exposure_label = exposure.get("label") or _format_inr_cr(exposure.get("amount_cr"))
    all_estimate = (why_it_matters.get("warning") or "") == "all_estimate"
    stakes = why_it_matters.get("stakes_for_company") or ""
    crit_summary = why_it_matters.get("criticality_summary") or ""

    # ── The story ──────────────────────────────────────────────────────
    story_html = f"""
      <div style="margin-bottom:24px;">
        <div style="font-size:14px; font-weight:800; letter-spacing:0.5px; text-transform:uppercase; color:{_ACCENT}; margin-bottom:8px;">
          {_polarity_emoji(polarity)} The story
        </div>
        <p style="margin:0; font-size:16px; line-height:1.55; color:{_INK};">
          <strong>{_escape(company_name)}</strong> {_escape(_polarity_verb(polarity))}: {_escape(headline)}
        </p>
        <p style="margin:4px 0 0; font-size:12px; color:{_INK_MUTED};">
          Source: {_escape(source) or '—'}
        </p>
      </div>
    """

    # ── Why you'll care ────────────────────────────────────────────────
    band_badge = ""
    if band:
        band_color = {
            "CRITICAL": "#991B1B", "HIGH": "#9A3412",
            "MEDIUM": "#92400E", "LOW": "#065F46",
        }.get(band, _INK_MUTED)
        band_badge = (
            f'<span style="display:inline-block; padding:2px 8px; '
            f'border-radius:999px; background:#FFFFFF; color:{band_color}; '
            f'border:1px solid {band_color}33; font-size:10px; '
            f'font-weight:800; letter-spacing:0.5px; margin-right:8px;">'
            f"{_escape(band)}</span>"
        )
    exposure_chip = ""
    if exposure_label:
        suffix = " (engine estimate)" if all_estimate else ""
        exposure_chip = (
            f'<span style="display:inline-block; padding:2px 8px; '
            f'border-radius:999px; background:#F1F5F9; color:{_INK}; '
            f'font-size:11px; font-weight:700;">'
            f"{_escape(exposure_label)}{_escape(suffix)}</span>"
        )
    estimate_note = ""
    if all_estimate:
        estimate_note = (
            f'<p style="margin:6px 0 0; font-size:11px; color:{_INK_MUTED}; font-style:italic;">'
            f"Every ₹ figure below is an engine estimate — treat them as scenarios, not facts."
            f"</p>"
        )
    why_html = f"""
      <div style="margin-bottom:24px;">
        <div style="font-size:14px; font-weight:800; letter-spacing:0.5px; text-transform:uppercase; color:{_ACCENT}; margin-bottom:8px;">
          💡 Why you'll care
        </div>
        <div style="margin-bottom:8px;">
          {band_badge}{exposure_chip}
        </div>
        <p style="margin:0; font-size:14px; line-height:1.55; color:{_INK};">
          {_escape(crit_summary) or "&nbsp;"}
        </p>
        {('<p style="margin:6px 0 0; font-size:13px; line-height:1.6; color:'+_INK_MUTED+';">'+_escape(stakes)+"</p>") if stakes else ""}
        {estimate_note}
      </div>
    """

    # ── What that means for {company} ──────────────────────────────────
    # Phase 35 — surface owner + cost-band + payback + ROI inline so a
    # CFO reading the email can action a recommendation without
    # opening the app. Pre-fix the email showed only title + deadline,
    # which read as 3 generic ESG todos. Now: "▸ Title · Owner · ₹X-Y Cr cost
    # · 6mo payback · ROI 180% · by DATE".
    actions = what_it_triggers.get("recommended_actions") or []
    actions_rows = ""
    for a in actions[:3]:
        if not isinstance(a, dict):
            continue
        title = _escape(a.get("title") or "")
        deadline = _escape(a.get("deadline") or "")
        owner = _escape(a.get("owner") or "")
        budget = _escape(a.get("budget") or "")
        payback = a.get("payback_months")
        roi = a.get("roi_pct")
        details_bits: list[str] = []
        if owner:
            details_bits.append(f"owner: {owner}")
        if budget:
            details_bits.append(f"cost: {budget}")
        if payback:
            try:
                pm = float(payback)
                if pm > 0:
                    if pm >= 12:
                        details_bits.append(f"payback: {pm/12:.1f} yr")
                    else:
                        details_bits.append(f"payback: {int(pm)} mo")
            except (TypeError, ValueError):
                pass
        if roi is not None:
            try:
                rv = float(roi)
                if rv > 0:
                    details_bits.append(f"ROI: {rv:.0f}%")
            except (TypeError, ValueError):
                pass
        if deadline:
            details_bits.append(f"by {deadline}")
        details = " · ".join(details_bits)
        actions_rows += f"""
          <tr><td style="padding:8px 0; border-bottom:1px solid {_DIVIDER};">
            <p style="margin:0; font-size:14px; font-weight:600; color:{_INK};">
              ▸ {title}
            </p>
            {(f'<p style="margin:3px 0 0 14px; font-size:11px; color:'+_INK_MUTED+'; line-height:1.5;">'+details+"</p>") if details else ""}
          </td></tr>
        """
    means_html = ""
    if actions_rows:
        means_html = f"""
          <div style="margin-bottom:24px;">
            <div style="font-size:14px; font-weight:800; letter-spacing:0.5px; text-transform:uppercase; color:{_ACCENT}; margin-bottom:8px;">
              ⚡ What that means for {_escape(company_name)}
            </div>
            <table cellpadding="0" cellspacing="0" border="0" width="100%">
              {actions_rows}
            </table>
          </div>
        """

    # ── What to watch ──────────────────────────────────────────────────
    traj = what_to_watch.get("sentiment_trajectory") or {}
    risk_cats = what_to_watch.get("top_risk_categories") or []
    benchmarks = what_to_watch.get("benchmarks") or []
    traj_phrase = _trajectory_phrase(traj if isinstance(traj, dict) else {})
    risk_phrase = ""
    if risk_cats:
        risk_phrase = (
            f'<p style="margin:6px 0 0; font-size:13px; color:{_INK};">'
            f"Top risk categories: <strong>{_escape(' · '.join(risk_cats[:3]))}</strong>"
            f"</p>"
        )
    bm_phrase = ""
    if benchmarks:
        bm_chips = " · ".join(
            f"{_escape(b.get('source'))} {_escape(b.get('metric'))}: <strong>{_escape(b.get('value'))}</strong>"
            for b in benchmarks[:3] if isinstance(b, dict)
        )
        bm_phrase = (
            f'<p style="margin:6px 0 0; font-size:12px; color:{_INK_MUTED};">'
            f"External benchmarks: {bm_chips}"
            f"</p>"
        )
    watch_html = f"""
      <div style="margin-bottom:24px;">
        <div style="font-size:14px; font-weight:800; letter-spacing:0.5px; text-transform:uppercase; color:{_ACCENT}; margin-bottom:8px;">
          🔮 What to watch
        </div>
        <p style="margin:0; font-size:14px; line-height:1.55; color:{_INK};">
          {_escape(traj_phrase)}
        </p>
        {risk_phrase}
        {bm_phrase}
      </div>
    """

    # ── CTAs ───────────────────────────────────────────────────────────
    # Phase 33 fix #2 — the user asked for three buttons in this row:
    #   1. "Read full article →" — opens the SOURCE publication (Whalesbook,
    #      Mint, etc.) so the reader can verify our analysis against the
    #      original. Falls back to the contact page when no source URL.
    #   2. "Contact Snowkap" — fixed link to the sales / contact page.
    #   3. "💬 Discuss in chat →" — opens the Snowkap chat with this
    #      article's context pre-loaded. Stays optional (hidden when we
    #      couldn't build the URL).

    # Button 1 — original publication
    read_article_url = (
        source_url if source_url and source_url.startswith(("http://", "https://"))
        else "https://snowkap.com/contact-us/"
    )
    cta_read = f"""
      <a href="{_escape(read_article_url)}"
         style="display:inline-block; padding:11px 22px; background:{_ACCENT};
                color:#FFFFFF; font-size:13px; font-weight:700; text-decoration:none;
                border-radius:999px; letter-spacing:0.3px; margin:4px;">
        Read full article →
      </a>
    """

    # Button 2 — Contact Snowkap (always the canonical sales page)
    cta_contact = f"""
      <a href="https://snowkap.com/contact-us/"
         style="display:inline-block; padding:11px 22px; background:#FFFFFF;
                color:{_ACCENT}; font-size:13px; font-weight:700; text-decoration:none;
                border-radius:999px; border:1px solid {_ACCENT}; letter-spacing:0.3px; margin:4px;">
        Contact Snowkap
      </a>
    """

    # ── Methodology disclaimer ─────────────────────────────────────────
    # Phase 33 — professional methodology note positioned between the
    # body + CTAs. Modelled on AI-platform "how this was made" disclosures.
    # Plain language, no jargon — tells the reader what they're reading,
    # how it was built, and where the receipts are.
    disclaimer_html = f"""
      <div style="margin:18px 0 6px; padding:12px 14px; background:{_BG};
                  border:1px solid {_DIVIDER}; border-radius:10px;">
        <p style="margin:0 0 4px; font-size:10px; font-weight:800; letter-spacing:0.6px;
                  text-transform:uppercase; color:{_INK_MUTED};">
          How this brief was built
        </p>
        <p style="margin:0; font-size:11px; line-height:1.6; color:{_INK_MUTED};">
          Snowkap reads each article and scores it against industry-specific
          materiality benchmarks, then estimates the financial exposure on
          a per-company basis using our in-house calibration models.
          Figures tagged <em>"(engine estimate)"</em> are scenario
          projections — not numbers the article itself reported — and we
          clamp them to plausible ranges. The full breakdown, source
          citations, and audit trail live inside the Snowkap app — tap the
          <strong>(i)</strong> icons on each section. We surface analysis
          to inform decisions; we don't provide investment, legal, or
          compliance advice.
        </p>
      </div>
    """

    # ── Assemble ───────────────────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Snowkap — {_escape(company_name)}</title>
</head>
<body style="margin:0; padding:0; background:{_BG}; font-family:'Helvetica Neue',Arial,sans-serif;">
  <table cellpadding="0" cellspacing="0" border="0" width="100%" bgcolor="{_BG}" style="background-color:{_BG};">
    <tr><td align="center" style="padding:24px 12px;">

      <table cellpadding="0" cellspacing="0" border="0" width="600" style="width:600px; max-width:600px; background:#FFFFFF; border-radius:14px; overflow:hidden; box-shadow:0 4px 12px rgba(15,23,42,0.06);">
        <!-- Header -->
        <tr><td style="padding:24px 28px 0; text-align:left;">
          <img src="cid:{LOGO_CID}" alt="Snowkap" width="160" height="auto" style="display:block; max-width:160px; height:auto;" />
        </td></tr>

        <!-- Greeting -->
        <tr><td style="padding:20px 28px 0;">
          <p style="margin:0; font-size:18px; font-weight:700; color:{_INK};">{greeting}</p>
          <p style="margin:6px 0 0; font-size:14px; color:{_INK_MUTED};">
            Here's what <strong>{_escape(company_name)}</strong> is dealing with today.
          </p>
        </td></tr>

        <!-- Body -->
        <tr><td style="padding:24px 28px 8px;">
          {story_html}
          {why_html}
          {means_html}
          {watch_html}
        </td></tr>

        <!-- Methodology disclaimer (Phase 33 — professional, AI-platform style) -->
        <tr><td style="padding:0 28px 0;">
          {disclaimer_html}
        </td></tr>

        <!-- CTAs (2 buttons: Read full article · Contact Snowkap) -->
        <tr><td style="padding:6px 28px 28px;">
          <div style="text-align:center;">
            {cta_read}
            {cta_contact}
          </div>
        </td></tr>

        <!-- Footer -->
        <tr><td style="padding:18px 28px 28px; border-top:1px solid {_DIVIDER}; background:#F8FAFC;">
          <p style="margin:0; font-size:11px; color:{_INK_MUTED}; line-height:1.55;">
            Snowkap turns ESG news into decisions in 10 seconds. Read more, less, and only what matters.
          </p>
          <p style="margin:6px 0 0; font-size:10px; color:{_INK_MUTED};">
            © Snowkap. You're receiving this because someone on your team subscribed.
          </p>
        </td></tr>
      </table>

    </td></tr>
  </table>
</body>
</html>"""


# Re-export LOGO_BASE64 so existing share-service callers can attach the
# same CID-bound logo. (Same payload as dark-card; only the layout changes.)
__all__ = ["render_article_morning_brew", "LOGO_BASE64"]
