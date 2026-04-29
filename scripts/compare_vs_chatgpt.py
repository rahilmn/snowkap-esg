"""Phase 5 — Head-to-head comparison harness.

Takes an article (file/prompt/already-processed) + company slug and runs:

  1. Our full Snowkap pipeline (or reuses existing outputs if available)
  2. GPT-4o baseline with a plain "give me an ESG analysis" prompt
  3. Gemini Pro baseline (optional — requires GOOGLE_API_KEY)

Then scores each brief on 30 persona dimensions (CFO + CEO + ESG Analyst)
via `engine.analysis.persona_scorer` and renders a 3-column markdown diff.

Usage:
    python scripts/compare_vs_chatgpt.py \
        --prompt data/inputs/prompts/test_phase3_adani_sebi.txt \
        --company adani-power \
        --output docs/comparisons/adani_sebi_2026-04-22.md

    # Use a pre-processed article (skip our pipeline re-run)
    python scripts/compare_vs_chatgpt.py \
        --article-id 826a3ce6508bfe9f \
        --company adani-power \
        --article-text-file data/inputs/prompts/test_phase3_adani_sebi.txt

Cost: ~$0.30-0.50 per comparison (our pipeline Stage 10+11a+11b+12 + GPT-4o + Gemini).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.analysis.persona_scorer import Scorecard, compute_win_matrix, score_brief  # noqa: E402
from engine.config import CONFIG_DIR, get_company, get_data_path, get_openai_api_key  # noqa: E402

logger = logging.getLogger("compare")

# ---------------------------------------------------------------------------
# Baseline LLM calls
# ---------------------------------------------------------------------------

_BASELINE_PROMPT = """You are an ESG advisor. Read the article below about {company_name} and produce a complete ESG analysis covering:

1. **CFO view** — specific ₹ financial impact (penalty amount, margin compression in bps, cost of capital effect), tagged with source (from article vs your estimate), named peer precedent with date + cost, regulatory deadline, do-nothing cost vs action cost, ROI if action taken, recovery path.

2. **CEO view** — a board-ready paragraph (60-120 words), named stakeholders with their likely stance and precedent (proxy advisors, regulators, top institutional investors, rating agencies), a single analogous precedent case with company + year + ₹ cost + outcome, 3-year trajectory contrasting do-nothing vs act-now, and Q&A drafts for earnings call / press / board / regulator.

3. **ESG Analyst view** — quantitative KPI table (Scope 1/2/3 emissions, LTIFR, board diversity %, cyber incidents etc.) with peer quartile positioning (25th/50th/75th), confidence bounds on every engine-estimated figure (β range, lag window, functional form), double materiality split (financial impact AND impact on world), TCFD scenario framing (1.5°C / 2°C / 4°C), SDG targets at sub-goal level (e.g. "SDG 8.7"), audit trail linking each claim to a source, framework citations at section level with rationale (BRSR:P6:Q14 with explanation, not just "BRSR").

Be specific. Name real companies, dates, ₹ amounts, and sources. Don't say "typical" or "in general" — cite actual precedents.

Article:
---
{article_text}
---

Company: {company_name} ({industry}, {market_cap})

Produce the full analysis now."""


def call_gpt4o_baseline(article_text: str, company_name: str, industry: str, market_cap: str) -> str:
    """Call GPT-4o with the plain 'give me ESG analysis' prompt.

    This is the 'what a competent user would get by pasting the article
    into ChatGPT' baseline.
    """
    from openai import APIError, APITimeoutError, OpenAI

    client = OpenAI(api_key=get_openai_api_key())
    prompt = _BASELINE_PROMPT.format(
        article_text=article_text,
        company_name=company_name,
        industry=industry,
        market_cap=market_cap,
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a senior ESG analyst."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=3500,
        )
        return resp.choices[0].message.content or ""
    except (APIError, APITimeoutError) as exc:
        logger.warning("GPT-4o baseline failed: %s", exc)
        return f"(GPT-4o call failed: {type(exc).__name__})"


def call_gemini_baseline(article_text: str, company_name: str, industry: str, market_cap: str) -> str | None:
    """Call Gemini Pro if GOOGLE_API_KEY is set. Returns None if unavailable."""
    key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        logger.info("Gemini skipped — GOOGLE_API_KEY not set")
        return None

    try:
        import google.generativeai as genai
    except ImportError:
        logger.info("Gemini skipped — google-generativeai not installed")
        return None

    genai.configure(api_key=key)
    prompt = _BASELINE_PROMPT.format(
        article_text=article_text,
        company_name=company_name,
        industry=industry,
        market_cap=market_cap,
    )
    try:
        model = genai.GenerativeModel("gemini-1.5-pro")
        resp = model.generate_content(prompt)
        return getattr(resp, "text", "") or ""
    except Exception as exc:  # noqa: BLE001 — Gemini raises many types
        logger.warning("Gemini baseline failed: %s", exc)
        return f"(Gemini call failed: {type(exc).__name__})"


# ---------------------------------------------------------------------------
# Snowkap pipeline runner
# ---------------------------------------------------------------------------


def run_snowkap_pipeline(article_text: str, company_slug: str, article_title: str = "") -> dict:
    """Run our full Stage 1-12 pipeline and return consolidated outputs.

    Returns a dict with keys:
      - consolidated_text: all perspectives + recommendations as one string (for scoring)
      - insight, perspectives (3), recommendations: the raw dicts
      - elapsed_seconds
    """
    from engine.analysis.ceo_narrative_generator import generate_ceo_narrative_perspective
    from engine.analysis.esg_analyst_generator import generate_esg_analyst_perspective
    from engine.analysis.insight_generator import generate_deep_insight
    from engine.analysis.perspective_engine import transform_for_perspective
    from engine.analysis.pipeline import process_article
    from engine.analysis.recommendation_engine import generate_recommendations
    from engine.ingestion.news_fetcher import _url_hash

    company = get_company(company_slug)
    started = time.perf_counter()

    article_dict = {
        "id": _url_hash(article_text[:200]),
        "title": article_title or article_text.split("\n", 1)[0][:150],
        "content": article_text,
        "summary": article_text[:400],
        "source": "compare_vs_chatgpt_harness",
        "url": "prompt://harness",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {},
    }

    result = process_article(article_dict, company)
    out: dict = {"elapsed_seconds": 0.0, "tier": result.tier, "rejected": result.rejected}

    if result.rejected or result.tier != "HOME":
        out["elapsed_seconds"] = round(time.perf_counter() - started, 2)
        out["consolidated_text"] = f"(Article tier={result.tier} — our pipeline did not produce full HOME output)"
        return out

    insight = generate_deep_insight(result, company)
    esg = generate_esg_analyst_perspective(insight, result, company) if insight else None
    ceo = generate_ceo_narrative_perspective(insight, result, company) if insight else None
    cfo = transform_for_perspective(insight, result, "cfo") if insight else None
    recs = generate_recommendations(insight, result, company) if insight else None

    out["insight"] = insight.to_dict() if insight else None
    out["esg_analyst"] = esg.to_dict() if esg else None
    out["ceo"] = ceo.to_dict() if ceo else None
    out["cfo"] = cfo.to_dict() if cfo else None
    out["recommendations"] = recs.to_dict() if recs else None
    out["elapsed_seconds"] = round(time.perf_counter() - started, 2)

    # Consolidated text = all perspective bodies + recommendations → for scoring
    parts: list[str] = []
    if cfo:
        parts.append("=== CFO ===")
        parts.append(cfo.headline)
        parts.extend(cfo.what_matters)
        parts.extend(cfo.action)
    if ceo:
        parts.append("=== CEO ===")
        parts.append(ceo.headline)
        parts.append(ceo.board_paragraph)
        for s in ceo.stakeholder_map:
            parts.append(f"Stakeholder: {s.get('stakeholder','')} — stance: {s.get('stance','')} — precedent: {s.get('precedent','')}")
        ap = ceo.analogous_precedent
        if ap:
            parts.append(f"Precedent: {ap.get('case_name','')} — {ap.get('company','')} ({ap.get('year','')}) — cost: {ap.get('cost','')} — outcome: {ap.get('outcome','')}")
        traj = ceo.three_year_trajectory
        for k, v in traj.items():
            parts.append(f"Trajectory {k}: {v}")
        for k, v in ceo.qna_drafts.items():
            parts.append(f"QnA {k}: {v}")
    if esg:
        parts.append("=== ESG ANALYST ===")
        parts.append(esg.headline)
        for kpi in esg.kpi_table:
            parts.append(f"KPI: {kpi.get('kpi_name','')} = {kpi.get('company_value','')} ({kpi.get('unit','')}), peer_quartile={kpi.get('peer_quartile','')}")
        for cb in esg.confidence_bounds:
            parts.append(
                f"Confidence: {cb.get('figure','')} {cb.get('source_type','')}, "
                f"β={cb.get('beta_range','')}, lag={cb.get('lag','')}, form={cb.get('functional_form','')}"
            )
        dm = esg.double_materiality
        parts.append(f"Double materiality — financial: {dm.get('financial_impact','')}; impact on world: {dm.get('impact_on_world','')}")
        for k, v in esg.tcfd_scenarios.items():
            parts.append(f"TCFD {k}: {v}")
        for s in esg.sdg_targets:
            parts.append(f"SDG {s.get('code','')}: {s.get('title','')} ({s.get('applicability','')})")
        for at in esg.audit_trail:
            parts.append(f"Audit: {at.get('claim','')} → {at.get('derivation','')} [sources: {at.get('sources','')}]")
        for fc in esg.framework_citations:
            parts.append(f"Framework: {fc.get('code','')} — {fc.get('rationale','')} (deadline: {fc.get('deadline','')}, region: {fc.get('region','')})")
    if recs and recs.to_dict().get("recommendations"):
        parts.append("=== RECOMMENDATIONS ===")
        for r in recs.to_dict().get("recommendations", []):
            parts.append(
                f"- {r.get('title','')}: {r.get('description','')} "
                f"[type={r.get('type','')}, ROI={r.get('roi_percentage','')}% {'(capped)' if r.get('roi_capped') else ''}, "
                f"peer={r.get('peer_benchmark','')}]"
            )

    out["consolidated_text"] = "\n".join(parts)
    return out


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def render_markdown_report(
    article_title: str,
    company_name: str,
    snowkap_text: str,
    gpt4o_text: str,
    gemini_text: str | None,
    snowkap_score: Scorecard,
    gpt4o_score: Scorecard,
    gemini_score: Scorecard | None,
    win_matrix: dict,
    snowkap_elapsed_s: float,
) -> str:
    """3-column markdown (or 2-column if Gemini skipped)."""
    lines: list[str] = []
    lines.append(f"# Comparison Report — {article_title}")
    lines.append("")
    lines.append(f"**Company:** {company_name}  ")
    lines.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}  ")
    lines.append(f"**Snowkap pipeline runtime:** {snowkap_elapsed_s}s  ")
    if gemini_score is None:
        lines.append("**Gemini:** not configured (GOOGLE_API_KEY missing)  ")
    lines.append("")

    # Score summary
    lines.append("## Persona score summary")
    lines.append("")
    headers = ["Persona", "Snowkap", "GPT-4o"]
    if gemini_score:
        headers.append("Gemini")
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for persona in ("cfo", "ceo", "esg_analyst"):
        row = [
            persona.upper().replace("_", " "),
            f"{getattr(snowkap_score, persona).total}/{getattr(snowkap_score, persona).max_total} ({getattr(snowkap_score, persona).pct:.0f}%)",
            f"{getattr(gpt4o_score, persona).total}/{getattr(gpt4o_score, persona).max_total} ({getattr(gpt4o_score, persona).pct:.0f}%)",
        ]
        if gemini_score:
            row.append(f"{getattr(gemini_score, persona).total}/{getattr(gemini_score, persona).max_total} ({getattr(gemini_score, persona).pct:.0f}%)")
        lines.append("| " + " | ".join(row) + " |")
    # Total
    totals = [
        "**TOTAL**",
        f"**{snowkap_score.total}/{snowkap_score.max_total} ({snowkap_score.pct:.0f}%)**",
        f"**{gpt4o_score.total}/{gpt4o_score.max_total} ({gpt4o_score.pct:.0f}%)**",
    ]
    if gemini_score:
        totals.append(f"**{gemini_score.total}/{gemini_score.max_total} ({gemini_score.pct:.0f}%)**")
    lines.append("| " + " | ".join(totals) + " |")
    lines.append("")

    # Per-dimension win table
    lines.append("## Win rate by dimension")
    lines.append("")
    for persona in ("cfo", "ceo", "esg_analyst"):
        lines.append(f"### {persona.upper().replace('_', ' ')}")
        lines.append("")
        lines.append("| Dimension | Snowkap | GPT-4o | " + ("Gemini | " if gemini_score else "") + "Winner |")
        lines.append("| --- | --- | --- | " + ("--- | " if gemini_score else "") + "--- |")
        p_s = getattr(snowkap_score, persona)
        p_g = getattr(gpt4o_score, persona)
        p_m = getattr(gemini_score, persona) if gemini_score else None
        for i, d_s in enumerate(p_s.dimensions):
            d_g = p_g.dimensions[i] if i < len(p_g.dimensions) else None
            d_m = p_m.dimensions[i] if p_m and i < len(p_m.dimensions) else None
            winner = win_matrix.get(persona, {}).get(d_s.name, "?")
            row = [
                d_s.name,
                str(d_s.score),
                str(d_g.score if d_g else "-"),
            ]
            if gemini_score:
                row.append(str(d_m.score if d_m else "-"))
            row.append(winner)
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    # Full-text side-by-side
    lines.append("---")
    lines.append("")
    lines.append("## Snowkap output (full)")
    lines.append("")
    lines.append("```text")
    lines.append(snowkap_text[:10000])
    if len(snowkap_text) > 10000:
        lines.append("... (truncated)")
    lines.append("```")
    lines.append("")
    lines.append("## GPT-4o baseline output (full)")
    lines.append("")
    lines.append("```text")
    lines.append(gpt4o_text[:10000])
    if len(gpt4o_text) > 10000:
        lines.append("... (truncated)")
    lines.append("```")
    if gemini_text:
        lines.append("")
        lines.append("## Gemini baseline output (full)")
        lines.append("")
        lines.append("```text")
        lines.append(gemini_text[:10000])
        if len(gemini_text) > 10000:
            lines.append("... (truncated)")
        lines.append("```")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare Snowkap output vs GPT-4o + Gemini")
    parser.add_argument("--prompt", required=True, help="Path to article text (prompt file).")
    parser.add_argument("--company", required=True, help="Company slug.")
    parser.add_argument("--output", help="Output markdown path. Default: docs/comparisons/<slug>_<timestamp>.md")
    parser.add_argument("--skip-gemini", action="store_true", help="Skip Gemini even if key is set.")
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Unicode-safe stdout on Windows
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass

    prompt_path = Path(args.prompt)
    if not prompt_path.exists():
        print(f"Prompt file not found: {prompt_path}", file=sys.stderr)
        return 2

    article_text = prompt_path.read_text(encoding="utf-8")
    article_title = article_text.split("\n", 1)[0][:150]

    company = get_company(args.company)

    print(f"\n--- Comparison: {article_title[:60]} ---")
    print(f"Company: {company.name} ({company.industry}, {company.market_cap})")
    print("Running Snowkap pipeline...")
    snowkap = run_snowkap_pipeline(article_text, args.company, article_title)
    print(f"  tier={snowkap.get('tier')} elapsed={snowkap['elapsed_seconds']}s")

    print("Running GPT-4o baseline...")
    gpt4o_text = call_gpt4o_baseline(article_text, company.name, company.industry, company.market_cap)
    print(f"  chars={len(gpt4o_text)}")

    gemini_text: str | None = None
    if not args.skip_gemini:
        print("Running Gemini baseline...")
        gemini_text = call_gemini_baseline(article_text, company.name, company.industry, company.market_cap)
        if gemini_text:
            print(f"  chars={len(gemini_text)}")

    # Score
    print("Scoring...")
    s_snowkap = score_brief(snowkap["consolidated_text"], "snowkap")
    s_gpt4o = score_brief(gpt4o_text, "gpt4o")
    s_gemini = score_brief(gemini_text, "gemini") if gemini_text else None
    cards = [s_snowkap, s_gpt4o] + ([s_gemini] if s_gemini else [])
    win_matrix = compute_win_matrix(cards)

    # Render
    report = render_markdown_report(
        article_title=article_title,
        company_name=company.name,
        snowkap_text=snowkap["consolidated_text"],
        gpt4o_text=gpt4o_text,
        gemini_text=gemini_text,
        snowkap_score=s_snowkap,
        gpt4o_score=s_gpt4o,
        gemini_score=s_gemini,
        win_matrix=win_matrix,
        snowkap_elapsed_s=snowkap["elapsed_seconds"],
    )

    # Write
    if args.output:
        out_path = Path(args.output)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
        out_path = _ROOT / "docs" / "comparisons" / f"{args.company}_{ts}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"\nReport written: {out_path}")

    # Print summary
    print("\n=== SUMMARY ===")
    print(f"Snowkap:  {s_snowkap.total}/{s_snowkap.max_total} ({s_snowkap.pct:.1f}%)")
    print(f"GPT-4o:   {s_gpt4o.total}/{s_gpt4o.max_total} ({s_gpt4o.pct:.1f}%)")
    if s_gemini:
        print(f"Gemini:   {s_gemini.total}/{s_gemini.max_total} ({s_gemini.pct:.1f}%)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
