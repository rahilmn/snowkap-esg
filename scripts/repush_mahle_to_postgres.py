"""Re-push every on-disk MAHLE insight to Postgres so the updated
split_analysis (Phase 47.P — lede included in personalised) lands
in company_article_view.

Reads each insight JSON, re-computes split + upserts to the two
deck tables. No LLM calls — pure re-write.
"""
from __future__ import annotations

import json
import logging
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
for n in ("urllib3", "httpx", "rdflib"):
    logging.getLogger(n).setLevel(logging.WARNING)


def main() -> int:
    from engine.analysis.unified_analysis import split_analysis
    from engine.models import article_pool, company_article_view

    slug = "mahle"
    out_dir = Path("data/outputs") / slug / "insights"
    files = sorted(out_dir.glob("*.json"))
    print(f"Found {len(files)} MAHLE insights to re-push")

    re_pushed = 0
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        article = d.get("article") or {}
        insight = d.get("insight") or {}
        analysis = insight.get("analysis") or {}

        article_id = article.get("id") or insight.get("article_id")
        if not article_id:
            print(f"  [SKIP] {f.name}: no article_id")
            continue

        # Re-split with the Phase 47.P fix
        shared, personalised = split_analysis(analysis)
        has_lede = bool((personalised.get("lede") or {}).get("text"))

        # Phase 47.P — patch the materiality_band inside why_it_matters
        # to match the LLM-escalated band (mirrors unified_analysis fix).
        _LLM_TO_ENGINE_INNER = {
            "CRITICAL": "CRITICAL", "HIGH": "HIGH",
            "MODERATE": "MEDIUM", "MEDIUM": "MEDIUM", "LOW": "LOW",
        }
        _RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
        wim = personalised.get("why_it_matters") or {}
        cur_band = (wim.get("materiality_band") or "").upper()
        llm_inner_mat = ((insight.get("decision_summary") or {}).get("materiality") or "").strip().upper()
        llm_inner_band = _LLM_TO_ENGINE_INNER.get(llm_inner_mat)
        if llm_inner_band and _RANK.get(llm_inner_band, 0) > _RANK.get(cur_band, 0):
            wim["materiality_band"] = llm_inner_band

            # Also rewrite the band-prefix in criticality_summary so the
            # text doesn't say "Low priority — ..." when the chip says MEDIUM.
            cur_summary = wim.get("criticality_summary") or ""
            band_prefix = {
                "CRITICAL": "Critical",
                "HIGH": "High priority",
                "MEDIUM": "Worth reviewing",
                "LOW": "Low priority",
            }
            old_prefixes = ("Low priority", "Worth reviewing", "High priority", "Critical")
            for prefix in old_prefixes:
                if cur_summary.startswith(prefix):
                    cur_summary = band_prefix.get(llm_inner_band, "Worth reviewing") + cur_summary[len(prefix):]
                    break
            wim["criticality_summary"] = cur_summary
            personalised["why_it_matters"] = wim
        print(f"  Re-pushing {article_id[:16]}  lede_in_personalised={has_lede}  "
              f"inner_band={wim.get('materiality_band')}")

        # Pull the criticality from disk
        criticality = insight.get("criticality") or {}
        crit_score = float(criticality.get("score") or 0.0)
        crit_band = (criticality.get("band") or "MEDIUM").upper()

        # Phase 47.P — mirror the writer's LLM-band escalation.
        _LLM_TO_ENGINE = {
            "CRITICAL": "CRITICAL", "HIGH": "HIGH",
            "MODERATE": "MEDIUM", "MEDIUM": "MEDIUM", "LOW": "LOW",
        }
        _BAND_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
        llm_mat = ((insight.get("decision_summary") or {}).get("materiality") or "").strip().upper()
        llm_band = _LLM_TO_ENGINE.get(llm_mat)
        if llm_band and _BAND_RANK.get(llm_band, 0) > _BAND_RANK.get(crit_band, 0):
            crit_band = llm_band

        try:
            company_article_view.upsert(
                article_id=article_id,
                company_slug=slug,
                personalised_analysis=personalised,
                criticality_score=crit_score,
                criticality_band=crit_band,
            )
            re_pushed += 1
        except Exception as exc:
            print(f"  [FAIL] {article_id}: {exc}")
            continue

    print(f"\n[OK] re-pushed {re_pushed}/{len(files)} MAHLE rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
