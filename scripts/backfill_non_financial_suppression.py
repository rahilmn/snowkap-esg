"""Phase 47.R — backfill the non-financial exposure suppression to
every on-disk insight + corresponding company_article_view row.

Walks data/outputs/{slug}/insights/*.json. For each insight:
  1. Read the original article body from data/inputs/news/{slug}/...
  2. If body has zero financial signal AND insight.analysis carries
     an engine_estimate / primitive_engine ₹ figure, suppress it:
       - financial_exposure → {kind: non_financial_event, source: suppressed, label: "..."}
       - strip ₹ X.X Cr clauses from criticality_summary + stakes_for_company
  3. Re-emit the analysis block to Postgres company_article_view

Idempotent — a second run on a clean insight is a no-op.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(_REPO_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)5s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
for n in ("urllib3", "httpx", "rdflib", "openai"):
    logging.getLogger(n).setLevel(logging.WARNING)

_FINANCIAL_SIGNAL_RE = re.compile(
    r"(?P<hard>[₹€$£¥]\s*[\d,]+(?:\.\d+)?|"
    r"\b\d[\d,.]*\s*(?:Cr|crore|lakh|billion|bn)\b)|"
    r"(?P<soft>\b(?:revenue|profit|EBIT|EBITDA|margin|capex|"
    r"capital expenditure|budget|funding|investment|valuation|"
    r"turnover|order\s+book|orderbook|topline|bottom\s*line|"
    r"cost\s+of\s+capital|cash\s+flow|million|million\s+dollars|"
    r"million\s+euros|million\s+rupees)\b)",
    re.IGNORECASE,
)

_BOUNDARY_RE = re.compile(
    r"\b(?:Related\s+Posts|Related\s+Articles|Related\s+Stories|"
    r"More\s+from|Read\s+also|Read\s+more|You\s+may\s+also\s+like|"
    r"Recommended\s+for\s+you|Trending|Popular\s+posts|Latest\s+news|"
    r"Comments|Tags?:|Filed\s+under)\b",
    re.IGNORECASE,
)


def _has_financial_signal(text: str) -> bool:
    if not text:
        return False
    # Drop sidebar / related-posts text — only scan the main article region
    m = _BOUNDARY_RE.search(text[:8000])
    main = text[: m.start()] if m else text[: int(len(text) * 0.7)]
    if not main:
        return False
    hard_hits = 0
    soft_hits = 0
    for match in _FINANCIAL_SIGNAL_RE.finditer(main):
        if match.group("hard"):
            hard_hits += 1
        elif match.group("soft"):
            soft_hits += 1
    return hard_hits >= 1 or soft_hits >= 2


def _strip_rupee_clauses(s: str) -> str:
    """Strip every ₹X[.Y] Cr clause through the next sentence terminator.

    Applied iteratively so compound sentences like
      "Low priority — ₹16.9 Cr total upside, comprising ₹10.0 Cr direct
       setup and ₹6.9 Cr cascading benefits"
    collapse cleanly to "Low priority" instead of leaving fragments.
    """
    text = s or ""
    if not text:
        return ""
    # Remove every ₹...Cr clause and everything after it up to the next
    # sentence boundary (period, newline, em-dash sentence break).
    # Iterate so each pass removes one clause + its trailing junk.
    for _ in range(6):
        new = re.sub(
            r"[—\-,:;]?\s*₹\s*[\d,]+(?:\.\d+)?\s*Cr\b[^\.\n]*",
            "",
            text,
        )
        if new == text:
            break
        text = new
    # Drop any dangling fragment that looks like "0 Cr direct setup and"
    # left over when the regex couldn't match the leading ₹ but matched
    # a partial figure.
    text = re.sub(r"\b\d[\d,.]*\s*Cr\b[^\.\n]*", "", text)
    # Clean up trailing/leading punctuation, collapse whitespace.
    text = re.sub(r"\s{2,}", " ", text).strip(" -—,:;.")
    return text


def main() -> int:
    from engine.analysis.unified_analysis import split_analysis
    from engine.models import company_article_view

    out_root = Path("data/outputs")
    inputs_root = Path("data/inputs/news")
    if not out_root.exists():
        print(f"No outputs dir at {out_root}")
        return 0

    total_seen = 0
    total_patched = 0
    re_pushed = 0

    for tenant_dir in sorted(out_root.iterdir()):
        if not tenant_dir.is_dir() or tenant_dir.name.startswith("_"):
            continue
        slug = tenant_dir.name
        insights_dir = tenant_dir / "insights"
        if not insights_dir.exists():
            continue
        inputs_dir = inputs_root / slug

        for f in sorted(insights_dir.glob("*.json")):
            total_seen += 1
            d = json.loads(f.read_text(encoding="utf-8"))
            article_id = (d.get("article") or {}).get("id") or ""
            if not article_id:
                continue

            # Find the original article body
            body = ""
            if inputs_dir.exists():
                for inp in inputs_dir.glob(f"*{article_id}*.json"):
                    try:
                        inp_d = json.loads(inp.read_text(encoding="utf-8"))
                        body = inp_d.get("content") or ""
                        break
                    except Exception:
                        continue

            body_has_money = _has_financial_signal(body)
            insight = d.get("insight") or {}
            analysis = insight.get("analysis") or {}
            wim = analysis.get("why_it_matters") or {}
            exposure = wim.get("financial_exposure") or {}
            cur_source = (exposure.get("source") or "").lower()
            cur_amount = exposure.get("amount_cr")

            needs_patch = (
                not body_has_money
                and cur_source in {"engine_estimate", "primitive_engine"}
                and isinstance(cur_amount, (int, float))
                and cur_amount > 0
            )
            if not needs_patch:
                continue

            # Patch the exposure block
            wim["financial_exposure"] = {
                "kind": "non_financial_event",
                "source": "suppressed",
                "label": "Article does not quote ₹ exposure; engine extrapolation suppressed.",
            }
            wim["criticality_summary"] = _strip_rupee_clauses(
                wim.get("criticality_summary") or ""
            ) or wim.get("criticality_summary") or ""
            wim["stakes_for_company"] = _strip_rupee_clauses(
                wim.get("stakes_for_company") or ""
            ) or wim.get("stakes_for_company") or ""

            analysis["why_it_matters"] = wim
            insight["analysis"] = analysis
            d["insight"] = insight
            f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
            total_patched += 1
            print(f"  [PATCH] {slug}/{f.name} — suppressed ₹{cur_amount} Cr "
                  f"({cur_source}) on non-financial body")

            # Re-push the deck row
            try:
                shared, personalised = split_analysis(analysis)
                criticality = insight.get("criticality") or {}
                crit_score = float(criticality.get("score") or 0.0)
                crit_band = (criticality.get("band") or "MEDIUM").upper()
                # Mirror the LLM-band escalation from writer.py
                _LLM_TO_ENGINE = {
                    "CRITICAL": "CRITICAL", "HIGH": "HIGH",
                    "MODERATE": "MEDIUM", "MEDIUM": "MEDIUM", "LOW": "LOW",
                }
                _RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
                llm_mat = ((insight.get("decision_summary") or {}).get("materiality") or "").strip().upper()
                llm_band = _LLM_TO_ENGINE.get(llm_mat)
                if llm_band and _RANK.get(llm_band, 0) > _RANK.get(crit_band, 0):
                    crit_band = llm_band
                company_article_view.upsert(
                    article_id=article_id,
                    company_slug=slug,
                    personalised_analysis=personalised,
                    criticality_score=crit_score,
                    criticality_band=crit_band,
                )
                re_pushed += 1
            except Exception as exc:
                print(f"  [WARN] postgres re-push failed for {article_id}: {exc}")

    print()
    print(f"Scanned {total_seen} insights; patched {total_patched}; re-pushed {re_pushed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
