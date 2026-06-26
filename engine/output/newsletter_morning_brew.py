"""Phase 33 §6 / Phase 38 — Morning-Brew-style newsletter renderer.

Parallel to ``newsletter_renderer.render_article_brief_dark``, this
template consumes the Phase 32 unified ``insight.analysis`` block and
emits a clean, editorial, decision-grade brief modelled on Mint / FT
Sustainability sections:

  * Subject line  — verb-first, ≤90 chars, ₹ figure when material.
  * Greeting      — "Hey {first_name}, here's what {company} is dealing
                    with today."
  * WHAT CHANGED      — what_changed in one short paragraph.
  * WHY IT MATTERS    — materiality band + ₹ exposure + stakes for the
                        reader's company.
  * RECOMMENDED ACTIONS — top 2-3 recommended_actions (owner · cost ·
                          payback · ROI · deadline).
  * FORWARD INDICATORS — sentiment trajectory in plain English + top
                         risk categories. (External rating scores —
                         MSCI ESG / CRISIL / DJSI / Sustainalytics —
                         deliberately omitted; opaque + stale.)
  * CTAs          — "Read full article →" + "Contact Snowkap".
  * Footer        — same brand block as the dark-card layout.

Phase 38 — emojis removed across every section (📰 💡 ⚡ 🔮 📈 📉).
The v17 dark-card audit (newsletter_renderer.py) verified Outlook's
Word engine fragments emoji icons into coloured boxes. Section identity
is now carried by orange left-border accent + uppercase typographic
hierarchy.
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


def _section_label(text: str) -> str:
    """Phase 38 — orange-bordered uppercase label for a section header.

    Replaces the emoji-prefixed labels (📰 The story, 💡 Why you'll care,
    etc.) that read as AI-generated marketing copy. Visual identity now
    carried by:
      * orange left-border accent (3px, brand colour)
      * uppercase + letter-spacing for editorial typographic rhythm
      * bold weight at small size (11px) so it never competes with body

    Mirrors the v17 dark-card audit's conclusion that Outlook's Word
    engine fragments emoji into coloured boxes, so we lean on typography
    instead.
    """
    return (
        f'<div style="border-left:3px solid {_ACCENT}; padding-left:12px; '
        f'margin-bottom:10px; font-size:11px; font-weight:800; '
        f'letter-spacing:0.6px; text-transform:uppercase; color:{_INK};">'
        f'{_escape(text)}'
        f'</div>'
    )


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
    """Phase 38 — plain editorial English. No em-dashes, no breezy idioms
    ("fire drill", "on the up"), no AI tells. Max one comma per sentence."""
    h3 = (traj.get("horizon_3m") or "").lower()
    h6 = (traj.get("horizon_6m") or "").lower()
    h12 = (traj.get("horizon_12m") or "").lower()
    if not any((h3, h6, h12)):
        return "Sentiment is holding at baseline. No alert signals on the 12-month horizon."
    if h3 == h6 == h12 == "stable":
        return "Sentiment is set to hold steady through the next year. Track but no action needed."
    if h3 == h6 == h12 == "declining":
        return "Sentiment is set to weaken through the next year. Plan a response narrative before the next investor touchpoint."
    if h3 == h6 == h12 == "improving":
        return "Sentiment is set to strengthen through the next year. Lead with the recovery narrative at the next earnings call."
    return f"Trajectory: 3m {h3 or 'unknown'}. 6m {h6 or 'unknown'}. 12m {h12 or 'unknown'}."


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

    # Phase 39 — editorial lede. 2-3 sentence story-style opener that
    # sits between the greeting and the structured WHAT CHANGED section.
    # Composed once at write time via engine.analysis.lede_writer; the
    # renderer just reads insight.analysis.lede.text and wraps it in
    # serif italic typography. Falls back gracefully when absent
    # (pre-Phase-39 articles still on schema 3.2 or earlier).
    lede = analysis.get("lede") or {}
    lede_text = (lede.get("text") or "").strip() if isinstance(lede, dict) else ""

    band = (why_it_matters.get("materiality_band") or "").upper()
    exposure = why_it_matters.get("financial_exposure") or {}
    exposure_label = exposure.get("label") or _format_inr_cr(exposure.get("amount_cr"))
    all_estimate = (why_it_matters.get("warning") or "") == "all_estimate"
    stakes = why_it_matters.get("stakes_for_company") or ""
    crit_summary = why_it_matters.get("criticality_summary") or ""

    # ── Editorial lede (Phase 39) ─────────────────────────────────────
    # Serif italic, larger font, generous padding, no decorative label.
    # Signals "editor's voice" without shouting it — matches FT Alphaville
    # lede styling. Renders nothing when the analysis block carries no
    # lede (e.g. pre-Phase-39 articles still at schema 3.2 or earlier).
    lede_html = ""
    if lede_text:
        lede_html = f"""
          <tr><td style="padding:6px 28px 14px;">
            <p style="margin:0; font-family:Georgia,'Times New Roman',serif;
                      font-size:17px; line-height:1.55; color:{_INK};
                      font-style:italic; letter-spacing:0.1px;">
              {_escape(lede_text)}
            </p>
          </td></tr>
        """

    # ── WHAT CHANGED ───────────────────────────────────────────────────
    # Phase 38 — lead with the fact (Hemingway rule: never frame before
    # facts). No polarity verb prefix ("scored a win" / "ran into trouble").
    story_html = f"""
      <div style="margin-bottom:24px;">
        {_section_label("What changed")}
        <p style="margin:0; font-size:16px; line-height:1.55; color:{_INK};">
          <strong>{_escape(company_name)}</strong>. {_escape(headline)}
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
        {_section_label("Why it matters")}
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
            {_section_label("Recommended actions")}
            <table cellpadding="0" cellspacing="0" border="0" width="100%">
              {actions_rows}
            </table>
          </div>
        """

    # ── What to watch ──────────────────────────────────────────────────
    # Phase 39 polish (2026-05-27) — external rating scores (MSCI ESG,
    # CRISIL, DJSI, Sustainalytics, ISS QualityScore, etc.) are pulled
    # entirely from this section. The reader trusts the article facts
    # the engine surfaces; rating-shaped third-party scores add stale,
    # opaque noise without informing the decision. Data still lives in
    # the company_benchmarks table for future analyst-mode use; emails
    # + /now + chat just don't show it.
    traj = what_to_watch.get("sentiment_trajectory") or {}
    risk_cats = what_to_watch.get("top_risk_categories") or []
    traj_phrase = _trajectory_phrase(traj if isinstance(traj, dict) else {})
    risk_phrase = ""
    if risk_cats:
        risk_phrase = (
            f'<p style="margin:6px 0 0; font-size:13px; color:{_INK};">'
            f"Top risk categories: <strong>{_escape(' · '.join(risk_cats[:3]))}</strong>"
            f"</p>"
        )
    watch_html = f"""
      <div style="margin-bottom:24px;">
        {_section_label("Forward indicators")}
        <p style="margin:0; font-size:14px; line-height:1.55; color:{_INK};">
          {_escape(traj_phrase)}
        </p>
        {risk_phrase}
      </div>
    """

    # ── How this hits your framework (Phase 56.D/F/K) ─────────────────────
    # The framework / principle / mandatory facts are ontology-derived
    # (deterministic); only the interpretation prose is LLM-written. Prefer the
    # article-level hit, else the top recommendation's hit. Mirrors the mobile
    # ArticleSheet's "How this hits your framework" block.
    fhit = what_it_triggers.get("framework_hit")
    if not (isinstance(fhit, dict) and fhit.get("framework")):
        for _a in actions:
            _h = _a.get("framework_hit") if isinstance(_a, dict) else None
            if isinstance(_h, dict) and _h.get("framework"):
                fhit = _h
                break
    framework_html = ""
    if isinstance(fhit, dict) and fhit.get("framework"):
        _fw = _escape(fhit.get("framework") or "")
        _pc = _escape(fhit.get("principle_code") or "")
        _pt = _escape(fhit.get("principle_title") or "")
        _mand = bool(fhit.get("mandatory"))
        _interp = _escape(fhit.get("interpretation") or "")
        _cbg = "#FEE2E2" if _mand else "#F1F5F9"
        _cfg = "#991B1B" if _mand else _INK
        _cbd = "#FCA5A5" if _mand else _DIVIDER
        _mbadge = (' <span style="font-size:9px; font-weight:800; letter-spacing:0.5px;">'
                   "MANDATORY</span>") if _mand else ""
        _chip = (
            f'<span style="display:inline-block; padding:3px 10px; border-radius:6px; '
            f'background:{_cbg}; color:{_cfg}; border:1px solid {_cbd}; '
            f'font-size:11px; font-weight:700;">{_fw}{(" · " + _pc) if _pc else ""}{_mbadge}</span>'
        )
        _pt_html = (f'<span style="font-size:12px; color:{_INK_MUTED}; margin-left:8px;">'
                    f"{_pt}</span>") if _pt else ""
        _interp_html = (f'<p style="margin:0; font-size:13px; line-height:1.6; color:{_INK};">'
                        f"{_interp}</p>") if _interp else ""
        framework_html = f"""
          <div style="margin-bottom:24px;">
            {_section_label("How this hits your framework")}
            <div style="margin-bottom:8px;">
              {_chip}{_pt_html}
            </div>
            {_interp_html}
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
    # Phase 38 — plain editorial English. No em-dashes. Max 2 commas per
    # sentence. Lead with the fact (what we read, what we computed).
    disclaimer_html = f"""
      <div style="margin:18px 0 6px; padding:12px 14px; background:{_BG};
                  border:1px solid {_DIVIDER}; border-radius:10px;">
        <p style="margin:0 0 4px; font-size:10px; font-weight:800; letter-spacing:0.6px;
                  text-transform:uppercase; color:{_INK_MUTED};">
          How this brief was built
        </p>
        <p style="margin:0; font-size:11px; line-height:1.6; color:{_INK_MUTED};">
          Snowkap reads each article and scores it against industry-specific
          materiality benchmarks. Framework mappings come from our regulatory
          ontology: the BRSR principle and whether disclosure is mandatory are
          looked up, not guessed per article. Any &#8377; figure tagged
          "(engine estimate)" is a scenario projection clamped to a plausible
          range, not a number the article reported. Figures the article itself
          states are shown as reported. Full source citations and the audit
          trail live inside the Snowkap app. This brief informs decisions. It
          is not investment, legal, or compliance advice.
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
        <!-- Phase 38.4 — logo upsized 160 → 200px so the brand mark registers
             on first glance. Source PNG is 240×40 so we have headroom. -->
        <tr><td style="padding:24px 28px 0; text-align:left;">
          <img src="cid:{LOGO_CID}" alt="Snowkap" width="200" height="auto" style="display:block; max-width:200px; height:auto;" />
        </td></tr>

        <!-- Greeting -->
        <tr><td style="padding:20px 28px 0;">
          <p style="margin:0; font-size:18px; font-weight:700; color:{_INK};">{greeting}</p>
          <p style="margin:6px 0 0; font-size:14px; color:{_INK_MUTED};">
            Here's what <strong>{_escape(company_name)}</strong> is dealing with today.
          </p>
        </td></tr>

        <!-- Phase 39 — editorial lede sits between greeting and the
             structured WHAT CHANGED section. Renders nothing when the
             insight has no analysis.lede (back-compat for old articles). -->
        {lede_html}

        <!-- Body -->
        <tr><td style="padding:18px 28px 8px;">
          {story_html}
          {why_html}
          {means_html}
          {framework_html}
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
