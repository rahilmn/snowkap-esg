"""Phase 8 — Auto-brief generator.

Takes an already-processed HOME-tier article and emits a 1-page markdown
brief suitable for:
  - Drip email to a CFO / ESG team
  - Co-branded journalist handoff (e.g. Mint ESG vertical)
  - Internal sales demo prep

Pulls from existing data/outputs/<slug>/... JSONs. No LLM calls, no cost.

Usage:
    python scripts/generate_brief.py --company adani-power --article-id 826a3ce6508bfe9f
    python scripts/generate_brief.py --company adani-power --latest
    python scripts/generate_brief.py --company adani-power --latest \\
        --format email --output briefs/adani_latest.md
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.config import get_company, get_output_dir  # noqa: E402

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Load helpers
# ---------------------------------------------------------------------------


def _latest_home_insight(company_slug: str) -> Path | None:
    folder = get_output_dir(company_slug) / "insights"
    if not folder.exists():
        return None
    files = sorted(folder.glob("*.json"), reverse=True)
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        if data.get("insight"):  # HOME articles have a non-null insight
            return f
    return None


def _load_perspective(company_slug: str, lens: str, article_id: str) -> dict | None:
    folder = get_output_dir(company_slug) / "perspectives" / lens
    # Match any YYYY-MM-DD_<article_id>.json
    matches = list(folder.glob(f"*_{article_id}.json"))
    if not matches:
        return None
    return json.loads(matches[0].read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _render_long_format(
    company_name: str,
    insight_payload: dict,
    esg: dict | None,
    ceo: dict | None,
    cfo: dict | None,
    recs: list[dict],
) -> str:
    """Full-page brief (for journalist handoff or detailed drip email)."""
    art = insight_payload.get("article", {}) or {}
    insight = insight_payload.get("insight", {}) or {}

    lines: list[str] = []
    lines.append(f"# {company_name} ESG Brief")
    lines.append(f"*Generated {datetime.now(timezone.utc).date().isoformat()}*")
    lines.append("")

    # Article reference
    lines.append(f"**Source:** {art.get('source', '?')}  ")
    lines.append(f"**Headline:** {art.get('title', '')}  ")
    if art.get("url"):
        lines.append(f"**URL:** {art['url']}")
    lines.append("")

    # Intelligence headline
    head = insight.get("headline", "")
    if head:
        lines.append(f"> **{head}**")
        lines.append("")

    # Decision summary — single concrete call-out
    ds = insight.get("decision_summary") or {}
    if ds:
        lines.append(f"**Materiality:** {ds.get('materiality', '')}  ")
        lines.append(f"**Action:** {ds.get('action', '')}  ")
        lines.append(f"**Financial exposure:** {ds.get('financial_exposure', '')}  ")
        lines.append(f"**Key risk:** {ds.get('key_risk', '')}  ")
        lines.append(f"**Opportunity:** {ds.get('top_opportunity', '')}  ")
        lines.append(f"**Timeline:** {ds.get('timeline', '')}  ")
        lines.append("")

    # CFO view
    if cfo:
        lines.append("## For the CFO — P&L impact")
        lines.append("")
        lines.append(f"**{cfo.get('headline','')}**")
        lines.append("")
        for bullet in (cfo.get("what_matters") or [])[:3]:
            lines.append(f"- {bullet}")
        lines.append("")

    # CEO view — board narrative
    if ceo:
        lines.append("## For the CEO — Board narrative")
        lines.append("")
        lines.append(f"**{ceo.get('headline','')}**")
        lines.append("")
        bp = ceo.get("board_paragraph", "")
        if bp:
            lines.append(bp)
            lines.append("")

        # Stakeholder map
        stakes = ceo.get("stakeholder_map") or []
        if stakes:
            lines.append("### Stakeholder map")
            lines.append("")
            lines.append("| Stakeholder | Stance | Precedent |")
            lines.append("| --- | --- | --- |")
            for s in stakes[:6]:
                label = (s.get("stakeholder") or "").replace("|", "/")
                stance = (s.get("stance") or "").replace("|", "/")[:150]
                prec = (s.get("precedent") or "").replace("|", "/")[:120]
                lines.append(f"| {label} | {stance} | {prec} |")
            lines.append("")

        # Analogous precedent
        ap = ceo.get("analogous_precedent") or {}
        if ap:
            lines.append("### Analogous precedent")
            lines.append("")
            lines.append(
                f"**{ap.get('case_name','')}** — {ap.get('company','')} ({ap.get('year','')}) — "
                f"cost {ap.get('cost','')}, duration {ap.get('duration','')}. "
                f"Outcome: {ap.get('outcome','')}. *Why this matches:* {ap.get('applicability','')}"
            )
            lines.append("")

        # Trajectory
        tj = ceo.get("three_year_trajectory") or {}
        if tj:
            lines.append("### 3-year trajectory")
            lines.append("")
            if tj.get("do_nothing"):
                lines.append(f"**Do nothing:** {tj['do_nothing']}")
                lines.append("")
            if tj.get("act_now"):
                lines.append(f"**Act now:** {tj['act_now']}")
                lines.append("")

        # Q&A
        qna = ceo.get("qna_drafts") or {}
        if qna:
            lines.append("### Q&A drafts")
            lines.append("")
            for ctx, text in qna.items():
                if text:
                    lines.append(f"**{ctx.replace('_', ' ').title()}:** {text}")
                    lines.append("")

    # ESG Analyst view
    if esg:
        lines.append("## For the ESG Analyst — Depth")
        lines.append("")
        lines.append(f"**{esg.get('headline','')}**")
        lines.append("")

        # KPI table
        kpis = esg.get("kpi_table") or []
        if kpis:
            lines.append("### KPIs")
            lines.append("")
            lines.append("| KPI | Value | Unit | Peer quartile | Source |")
            lines.append("| --- | --- | --- | --- | --- |")
            for k in kpis[:6]:
                lines.append(
                    f"| {k.get('kpi_name','')} | {k.get('company_value','')} | "
                    f"{k.get('unit','')} | {k.get('peer_quartile','')} | {k.get('data_source','')} |"
                )
            lines.append("")

        # Confidence bounds
        cbs = esg.get("confidence_bounds") or []
        if cbs:
            lines.append("### Confidence bounds on ₹ estimates")
            lines.append("")
            for c in cbs[:6]:
                bits = []
                if c.get("beta_range"):
                    bits.append(f"β {c['beta_range']}")
                if c.get("lag"):
                    bits.append(f"lag {c['lag']}")
                if c.get("functional_form"):
                    bits.append(f"{c['functional_form']}")
                bits_str = " · ".join(bits) if bits else ""
                lines.append(
                    f"- **{c.get('figure','')}** ({c.get('source_type','')}, confidence: {c.get('confidence','')})"
                    + (f"  \n  {bits_str}" if bits_str else "")
                )
            lines.append("")

        # Double materiality
        dm = esg.get("double_materiality") or {}
        if dm:
            lines.append("### Double materiality")
            lines.append("")
            lines.append(f"- **Financial impact:** {dm.get('financial_impact','')}")
            lines.append(f"- **Impact on world:** {dm.get('impact_on_world','')}")
            lines.append("")

        # TCFD scenarios
        tcfd = esg.get("tcfd_scenarios") or {}
        if tcfd:
            lines.append("### TCFD scenarios")
            lines.append("")
            for k, v in tcfd.items():
                if v:
                    lines.append(f"- **{k.replace('_', '.').upper()}:** {v}")
            lines.append("")

        # SDG targets
        sdgs = esg.get("sdg_targets") or []
        if sdgs:
            lines.append("### SDG targets triggered")
            lines.append("")
            for s in sdgs[:4]:
                lines.append(f"- **SDG {s.get('code','')}** — {s.get('title','')} *({s.get('applicability','')})*")
            lines.append("")

        # Framework citations
        fcs = esg.get("framework_citations") or []
        if fcs:
            lines.append("### Framework citations")
            lines.append("")
            for fc in fcs[:6]:
                line = f"- **{fc.get('code','')}** — {fc.get('rationale','')}"
                if fc.get("deadline"):
                    line += f" *(deadline: {fc['deadline']})*"
                lines.append(line)
            lines.append("")

    # Recommendations
    if recs:
        lines.append("## Recommended actions")
        lines.append("")
        lines.append("| # | Action | Type | Priority | ROI | Peer benchmark |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for i, r in enumerate(recs[:5], 1):
            roi = r.get("roi_percentage")
            roi_str = f"{roi:.0f}%" if roi is not None else "—"
            if r.get("roi_capped"):
                roi_str += " *(capped)*"
            lines.append(
                f"| {i} | {(r.get('title','') or '')[:60]} | {r.get('type','')} | "
                f"{r.get('priority','')} | {roi_str} | "
                f"{(r.get('peer_benchmark','') or '—')[:80]} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("*Produced by Snowkap ESG Intelligence Engine · all ₹ figures carry source tags*")
    return "\n".join(lines)


def _render_email_format(
    company_name: str,
    insight_payload: dict,
    ceo: dict | None,
) -> str:
    """Short drip-email format — aim for 150-200 words tops."""
    art = insight_payload.get("article", {}) or {}
    insight = insight_payload.get("insight", {}) or {}
    ds = insight.get("decision_summary") or {}

    lines: list[str] = []
    lines.append(f"Subject: {company_name} ESG pulse — {ds.get('materiality','') or 'new signal'}")
    lines.append("")
    lines.append(f"Hi team,")
    lines.append("")
    lines.append(f"One ESG item worth your attention at {company_name}:")
    lines.append("")
    lines.append(f"> {insight.get('headline', art.get('title', ''))}")
    lines.append("")
    lines.append(f"**Materiality:** {ds.get('materiality','')}. **Exposure:** {ds.get('financial_exposure','')}.")
    lines.append("")

    if ceo:
        bp = ceo.get("board_paragraph", "")
        if bp:
            # Trim to ~60 words for email
            words = bp.split()
            bp = " ".join(words[:60])
            if len(words) > 60:
                bp += "…"
            lines.append(bp)
            lines.append("")

        ap = ceo.get("analogous_precedent") or {}
        if ap:
            lines.append(
                f"**Closest precedent:** {ap.get('case_name','')} — {ap.get('company','')} ({ap.get('year','')}): "
                f"{ap.get('cost','')}, {ap.get('duration','')}. "
                f"{ap.get('outcome','')[:150]}"
            )
            lines.append("")

    lines.append("Want the full board-facing brief? Reply and I'll send the long-form analysis.")
    lines.append("")
    lines.append("— Snowkap ESG Intelligence")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Newsletter renderer runner (Phase 9 — HTML drip / ET-style)
# ---------------------------------------------------------------------------


def _run_newsletter(args: argparse.Namespace) -> int:
    from engine.output.newsletter_renderer import (
        DEFAULT_TAGLINE,
        build_articles_from_outputs,
        render_newsletter,
    )

    outputs_root = _ROOT / "data" / "outputs"

    # Resolve companies list
    if args.companies:
        slugs = [s.strip() for s in args.companies.split(",") if s.strip()]
    elif args.company:
        slugs = [args.company]
    else:
        # All 7 target companies
        from engine.config import load_companies
        slugs = [c.slug for c in load_companies()]

    articles = build_articles_from_outputs(
        slugs=slugs,
        outputs_root=outputs_root,
        max_count=args.count,
        read_more_base=args.read_more_base,
    )

    if not articles:
        print("ERROR: no HOME insights found in the selected companies", file=sys.stderr)
        return 1

    html_out = render_newsletter(
        articles=articles,
        recipient_name=args.recipient,
        newsletter_title=args.newsletter_title,
        tagline=args.tagline or DEFAULT_TAGLINE,
        cta_url=args.cta_url,
        cta_label=args.cta_label,
    )

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html_out, encoding="utf-8")
        print(f"Newsletter written: {out_path}")
        print(f"  articles: {len(articles)} across {len(set(a.company_name for a in articles))} companies")
        print(f"  CTA: {args.cta_url}")
    else:
        print(html_out)
    return 0


# ---------------------------------------------------------------------------
# Share runner (Phase 9 — single-article send to one recipient)
# ---------------------------------------------------------------------------


def _run_share(args: argparse.Namespace) -> int:
    from engine.output.share_service import (
        preview_share_html,
        share_article_by_email,
    )

    if not args.to:
        print("ERROR: --to <recipient email> required for share format", file=sys.stderr)
        return 2
    if not args.company:
        print("ERROR: --company <slug> required for share format", file=sys.stderr)
        return 2

    outputs_root = _ROOT / "data" / "outputs"

    # Resolve article_id (latest HOME if --latest)
    if args.latest:
        insight_path = _latest_home_insight(args.company)
        if not insight_path:
            print(f"ERROR: no HOME insights found for {args.company}", file=sys.stderr)
            return 1
        article_id = insight_path.stem.split("_", 1)[1]
    elif args.article_id:
        article_id = args.article_id
    else:
        print("ERROR: --latest or --article-id required for share format", file=sys.stderr)
        return 2

    if args.send:
        # Real send via Resend
        result = share_article_by_email(
            article_id=article_id,
            company_slug=args.company,
            recipient_email=args.to,
            outputs_root=outputs_root,
            sender_note=args.sender_note,
            read_more_base=args.read_more_base,
        )
        print(f"\nShare result: {result.status}")
        print(f"  recipient: {result.recipient}")
        print(f"  name:      {result.recipient_name or '(no name extracted — fallback greeting)'}")
        print(f"  subject:   {result.subject}")
        print(f"  provider:  {result.provider_id or '—'}")
        if result.error:
            print(f"  error:     {result.error}")
        return 0 if result.status == "sent" else 1

    # Preview — render HTML without sending
    html, result = preview_share_html(
        article_id=article_id,
        company_slug=args.company,
        recipient_email=args.to,
        outputs_root=outputs_root,
        sender_note=args.sender_note,
        read_more_base=args.read_more_base,
    )
    if result.status == "failed":
        print(f"ERROR: {result.error}", file=sys.stderr)
        return 1

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
        print(f"Preview written: {out_path}")
    else:
        print(html)

    print(f"\n(preview only — no email sent. Add --send to actually deliver.)")
    print(f"  to:        {result.recipient}")
    print(f"  name:      {result.recipient_name or '(no name extracted)'}")
    print(f"  subject:   {result.subject}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a brief or HTML newsletter from existing pipeline output.")
    parser.add_argument("--company", help="Company slug (for long/email formats). For newsletter use --companies.")
    parser.add_argument("--companies", help="Comma-separated company slugs for newsletter aggregation (e.g. adani-power,icici-bank).")
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--article-id", help="Specific article id (long/email formats).")
    grp.add_argument("--latest", action="store_true", help="Use latest HOME insight (long/email formats).")
    parser.add_argument("--format", choices=["long", "email", "newsletter", "share"], default="long",
                        help="long = 1-page markdown; email = 150-word drip; newsletter = full HTML ET-style weekly; share = single-article HTML for ad-hoc send.")
    parser.add_argument("--to", help="Recipient email (share format). Name is auto-extracted for greeting.")
    parser.add_argument("--send", action="store_true", help="Share format: actually send via Resend (requires RESEND_API_KEY). Default is preview-only.")
    parser.add_argument("--sender-note", help="Optional intro paragraph for the share email.")
    parser.add_argument("--output", help="Output path (default: print to stdout).")
    # Newsletter-only options
    parser.add_argument("--recipient", help="Recipient first name for newsletter greeting (optional).")
    parser.add_argument("--count", type=int, default=6, help="Number of articles in newsletter (default 6).")
    parser.add_argument("--cta-url", default="https://snowkap.com/contact-us/", help="CTA destination URL.")
    parser.add_argument("--cta-label", default="Book a demo with Snowkap", help="CTA button label.")
    parser.add_argument("--newsletter-title", default="The Snowkap Signal", help="Newsletter masthead title.")
    parser.add_argument("--tagline", default=None, help="Tagline below title (optional).")
    parser.add_argument("--read-more-base", default=None,
                        help="Base URL for 'Read full brief' links (e.g. https://snowkap.com/brief). If omitted, original article URL is used.")
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass

    # Newsletter format is multi-company + multi-article, handled separately
    if args.format == "newsletter":
        return _run_newsletter(args)

    # Share format: single article → single recipient → Resend send (or preview)
    if args.format == "share":
        return _run_share(args)

    if not args.company:
        print("ERROR: --company <slug> required for long/email formats", file=sys.stderr)
        return 2
    if not (args.latest or args.article_id):
        print("ERROR: --latest or --article-id required for long/email formats", file=sys.stderr)
        return 2

    company = get_company(args.company)

    # Resolve article_id
    if args.latest:
        insight_path = _latest_home_insight(args.company)
        if not insight_path:
            print(f"ERROR: no HOME insights found for {args.company}", file=sys.stderr)
            return 1
        article_id = insight_path.stem.split("_", 1)[1]  # YYYY-MM-DD_<id>.json
    else:
        article_id = args.article_id
        # Find the file
        insight_dir = get_output_dir(args.company) / "insights"
        matches = list(insight_dir.glob(f"*_{article_id}.json"))
        if not matches:
            print(f"ERROR: no insight for article_id {article_id}", file=sys.stderr)
            return 1
        insight_path = matches[0]

    # Load everything
    payload = json.loads(insight_path.read_text(encoding="utf-8"))
    esg = _load_perspective(args.company, "esg-analyst", article_id)
    ceo = _load_perspective(args.company, "ceo", article_id)
    cfo = _load_perspective(args.company, "cfo", article_id)

    recs_data = payload.get("recommendations") or {}
    recs = recs_data.get("recommendations", []) or []

    if args.format == "email":
        brief = _render_email_format(company.name, payload, ceo)
    else:
        brief = _render_long_format(company.name, payload, esg, ceo, cfo, recs)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(brief, encoding="utf-8")
        print(f"Brief written: {out_path}")
    else:
        print(brief)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
