"""Phase 53 polish — targeted, grounded cleanup of the idfc-first-bank demo deck.

Pure data patch (NO LLM, NO re-fetch): fixes the degraded fallback lede on the
CREST bail-denial card and tags every "~Rs140 Cr modeled" figure with the
spec-mandated "(engine estimate)" marker (CLAUDE.md s1) so a viewer can't mistake
the modeled total for the Rs83 Cr sourced amount. Patches BOTH the deck card
(company_article_view.personalised_analysis) and the detail mirror
(insight_payload) for consistency. Idempotent.
"""
from __future__ import annotations

import os
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass
from dotenv import load_dotenv

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")
os.environ["JWT_SECRET"] = "x" * 40
os.environ.setdefault("ENVIRONMENT", "production")
os.environ["OPENROUTER_API_KEY"] = ""

SLUG = "idfc-first-bank"
# The weak-lede card (CREST ex-project director bail denial).
WEAK_LEDE_ID = "f7c847b4e001a0ce"
NEW_LEDE = (
    "A Chandigarh court has denied bail to the CREST ex-project director in the "
    "Rs 83 crore IDFC First Bank fraud case, keeping the matter live and public. "
    "The engine models a wider Rs 140 Cr total exposure (engine estimate) once "
    "legal, reputational and cost-of-capital effects are layered onto the alleged "
    "Rs 83 crore diversion."
).replace("Rs ", "₹").replace("Rs", "₹")
NEW_WHY = (
    "Critical — alleged ₹83 crore CREST fund diversion at an IDFC First Bank "
    "branch; ~₹140 Cr total modeled exposure (engine estimate) once legal and "
    "reputational costs are layered in raises governance and disclosure risk."
)


def _tag_estimate(text: str) -> str:
    """Append (engine estimate) to a bare modeled-Rs phrase if not already tagged."""
    if not text:
        return text
    import re
    # add the marker right after a "modeled" Rs figure that isn't already tagged
    out = re.sub(
        r"(modeled exposure)(?!\s*\(engine estimate\))",
        r"\1 (engine estimate)",
        text,
    )
    out = re.sub(
        r"(modeled)(?!\s*\(engine estimate\))(?=[^()]*?$)",
        r"\1 (engine estimate)",
        out,
    ) if "(engine estimate)" not in out else out
    return out


def _patch_analysis(pa: dict, article_id: str) -> tuple[dict, list[str]]:
    changed: list[str] = []
    if not isinstance(pa, dict):
        return pa, changed
    lede = pa.get("lede") if isinstance(pa.get("lede"), dict) else {}
    wim = pa.get("why_it_matters") if isinstance(pa.get("why_it_matters"), dict) else {}
    # 1. fix the degraded lede on the weak card
    if article_id == WEAK_LEDE_ID and isinstance(lede, dict):
        cur = (lede.get("text") or "")
        if "Worth a scan" in cur or cur.strip().startswith("~"):
            lede["text"] = NEW_LEDE
            pa["lede"] = lede
            changed.append("lede")
    # 2. fix the weak card's why_it_matters
    if article_id == WEAK_LEDE_ID and isinstance(wim, dict):
        cur = (wim.get("criticality_summary") or "")
        if cur.strip().startswith("Critical — ~") or "modeled exposure from CREST" in cur:
            wim["criticality_summary"] = NEW_WHY
            pa["why_it_matters"] = wim
            changed.append("why")
    # 3. tag any remaining bare "(modeled)" Rs figure across cards
    if isinstance(wim, dict) and wim.get("criticality_summary"):
        tagged = _tag_estimate(wim["criticality_summary"])
        if tagged != wim["criticality_summary"]:
            wim["criticality_summary"] = tagged
            pa["why_it_matters"] = wim
            changed.append("why_tag")
    if isinstance(lede, dict) and lede.get("text"):
        tagged = _tag_estimate(lede["text"])
        if tagged != lede["text"]:
            lede["text"] = tagged
            pa["lede"] = lede
            changed.append("lede_tag")
    return pa, changed


def main() -> int:
    from engine.config import load_companies
    from engine.models import company_article_view as cav
    from engine.models import insight_payload as ip
    from engine.models.company_article_view import deck_for_company

    co = [c for c in load_companies() if c.slug == SLUG][0]
    rows, _ = deck_for_company(co.slug, co.industry, max_age_days=45, limit=10)
    crit = [r for r in rows if r.get("tier") == "critical"]
    print(f"idfc criticals: {len(crit)}")
    for r in crit:
        aid = r.get("article_id")
        pa = r.get("personalised_analysis") or {}
        new_pa, changed = _patch_analysis(dict(pa), aid)
        if not changed:
            print(f"  [{aid}] no change")
            continue
        # write deck card (preserve the existing criticality score/band)
        cav.upsert(
            article_id=aid,
            company_slug=co.slug,
            personalised_analysis=new_pa,
            criticality_score=float(r.get("criticality_score") or 0.0),
            criticality_band=str(r.get("criticality_band") or "HIGH"),
        )
        # write detail mirror (insight_payload.analysis), best-effort
        try:
            payload = ip.get(aid) or {}
            ins = payload.get("insight") or {}
            if isinstance(ins, dict) and isinstance(ins.get("analysis"), dict):
                ins["analysis"]["lede"] = new_pa.get("lede")
                ins["analysis"]["why_it_matters"] = new_pa.get("why_it_matters")
                payload["insight"] = ins
                ip.upsert(aid, co.slug, payload)
                changed.append("payload")
        except Exception as exc:  # noqa: BLE001
            print(f"  [{aid}] payload patch warn: {exc}")
        print(f"  [{aid}] patched: {changed}")
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
