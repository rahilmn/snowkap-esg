"""Phase 49.2 — repair degraded criticality_summaries already on decks (ZERO LLM).

After the Phase-49 cascade-₹ suppression, a few cards shipped a summary that
collapsed to just the band prefix ("Low priority") or the generic
"developing story: this story" stub — because the in-composer collapse check
missed the full two-word band prefix. The code is fixed for future composes;
this script repairs the EXISTING deck rows in place by re-grounding the summary
in the article HEADLINE (no synthetic ₹, no LLM), mirroring the corrected
_build_why_it_matters rebuild.

Usage:
    python scripts/fix_degraded_summaries.py          # dry-run
    python scripts/fix_degraded_summaries.py --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

import logging

logging.basicConfig(level=logging.WARNING, format="%(levelname)5s %(message)s")
logger = logging.getLogger("fix_summaries")

_BAND_PREFIX = {"CRITICAL": "Critical", "HIGH": "High priority",
                "MEDIUM": "Worth reviewing", "LOW": "Low priority"}
_GENERIC_STUB = re.compile(r"developing story:\s*(this story|this article)\.?\s*$", re.IGNORECASE)


def _loadj(v):
    return v if isinstance(v, dict) else json.loads(v or "{}")


_ALL_BAND_PREFIXES = {p.lower() for p in _BAND_PREFIX.values()}


def _is_degraded(summary: str, band_prefix: str) -> bool:
    s = (summary or "").strip()
    if not s:
        return True
    # bare band prefix — match ANY of the 4 prefixes, not just the current
    # band's. IDFC/SBI carry summary "Low priority" while their band escalated
    # to MEDIUM, so comparing only to the current prefix missed them.
    if s.lower().rstrip(".") in _ALL_BAND_PREFIXES:
        return True
    if _GENERIC_STUB.search(s):
        return True
    return False


def _headline_topic(title: str) -> str:
    topic = (title or "").strip()
    for sep in (" — ", " - ", " | ", " : ", ": "):
        if sep in topic:
            topic = topic.split(sep, 1)[0]
    return topic.strip().rstrip(".")[:100]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    from engine.db.connection import connect, is_postgres
    if not is_postgres():
        logger.error("Postgres only.")
        return 2

    out_root = _ROOT / "data" / "outputs"
    fixed: list[str] = []

    with connect() as c:
        rows = c.execute(
            """
            SELECT v.company_slug AS slug, v.article_id AS aid, v.criticality_band AS band,
                   v.personalised_analysis AS pa, p.title AS title
            FROM company_article_view v JOIN article_pool p ON p.id = v.article_id
            ORDER BY v.company_slug
            """
        ).fetchall()

        for r in rows:
            pa = _loadj(r["pa"])
            wim = pa.get("why_it_matters") or {}
            band = (r["band"] or wim.get("materiality_band") or "LOW").upper()
            band_prefix = _BAND_PREFIX.get(band, "Worth reviewing")
            summary = wim.get("criticality_summary") or ""
            if not _is_degraded(summary, band_prefix):
                continue
            topic = _headline_topic(r["title"] or pa.get("what_changed", {}).get("headline") or "")
            exp_kind = (wim.get("financial_exposure") or {}).get("kind")
            if exp_kind == "non_financial_event":
                new_summary = f"{band_prefix} — non-financial event; no ₹ exposure quoted in the article."
            elif topic:
                new_summary = f"{band_prefix} — {topic}."
            else:
                new_summary = f"{band_prefix} — material ESG development for your company."
            fixed.append(f"{r['slug']:<20} | {summary[:40]!r} -> {new_summary[:70]!r}")
            if args.apply:
                wim["criticality_summary"] = new_summary[:280]
                pa["why_it_matters"] = wim
                c.execute(
                    "UPDATE company_article_view SET personalised_analysis = ? "
                    "WHERE company_slug = ? AND article_id = ?",
                    (json.dumps(pa), r["slug"], r["aid"]),
                )
                # keep the on-disk insight in sync
                ins_dir = out_root / r["slug"] / "insights"
                if ins_dir.exists():
                    for f in ins_dir.glob(f"*{r['aid']}*.json"):
                        try:
                            d = json.loads(f.read_text(encoding="utf-8"))
                            an = (d.get("insight") or {}).get("analysis") or {}
                            if an.get("why_it_matters"):
                                an["why_it_matters"]["criticality_summary"] = new_summary[:280]
                                f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("disk sync failed %s: %s", f.name, exc)
        if args.apply:
            c.commit()

    print("\n" + "=" * 64)
    print(f"  DEGRADED SUMMARIES {'FIXED' if args.apply else '(dry-run)'}: {len(fixed)}")
    for line in fixed:
        print(f"  {line}")
    if not args.apply and fixed:
        print("\n  (re-run with --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
