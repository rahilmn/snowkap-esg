"""Phase 50.1 — apply summary/headline accuracy fixes (A/B/C) to EXISTING deck
cards across all companies. ZERO LLM (text-only; uses the on-disk article body
for grounding).

  Fix A — drop ungrounded ₹ from the criticality_summary (kills absurd engine
          estimates like "~₹16 Cr" surfaced for "15.59% of turnover").
  Fix B — repair orphan-unit garble ("...Invest Cr...") in headline/summary by
          re-deriving from the genuine article title (now that lakh-cr
          normalisation grounds "₹6.5 Lakh Cr" correctly).
  Fix C — rebuild bare band-prefix / empty summaries ("Low priority") from the
          article headline using the CURRENT band.

The deck's company-specific personalised_analysis row is patched in place + the
on-disk insight kept in sync. Mirrors the composer's Phase-50.1 final guards so
future fresh composes and this back-fill agree.

Usage:
    python scripts/recompose_decks.py            # dry-run
    python scripts/recompose_decks.py --apply
    python scripts/recompose_decks.py --apply --only adani-power
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

import logging

logging.basicConfig(level=logging.WARNING, format="%(levelname)5s %(message)s")
logger = logging.getLogger("recompose")

_BAND_PREFIX = {"CRITICAL": "Critical", "HIGH": "High priority",
                "MEDIUM": "Worth reviewing", "LOW": "Low priority"}
_ALL_BAND = {"critical", "high priority", "worth reviewing", "low priority"}
_BAND_WORDS = {w for p in _ALL_BAND for w in p.split()}
_UNITS = {"cr", "crore", "crores", "lakh", "lakhs", "billion", "bn", "million", "mn", "trillion"}


def _loadj(v):
    return v if isinstance(v, dict) else json.loads(v or "{}")


def _has_orphan_unit(text: str) -> bool:
    """A money unit with no digit in the 2 preceding tokens = strip garble
    ('Invest Cr'). '₹6.5 Lakh Cr' is fine (6.5 sits in the window)."""
    toks = (text or "").split()
    for i, t in enumerate(toks):
        if t.lower().strip(".,;:()") in _UNITS:
            window = " ".join(toks[max(0, i - 2):i])
            if not any(c.isdigit() for c in window):
                return True
    return False


def _headline_topic(title: str) -> str:
    topic = (title or "").strip()
    for sep in (" — ", " - ", " | ", " : ", ": "):
        if sep in topic:
            topic = topic.split(sep, 1)[0]
    return topic.strip().rstrip(".")[:110]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only")
    args = ap.parse_args()

    from engine.db.connection import connect, is_postgres
    if not is_postgres():
        logger.error("Postgres only.")
        return 2
    from engine.analysis.article_financials import money_grounded

    # body lookup by article_id (across all company input dirs)
    news_root = _ROOT / "data" / "inputs" / "news"
    body_by_id: dict[str, str] = {}
    for d in news_root.iterdir() if news_root.exists() else []:
        if not d.is_dir():
            continue
        for f in d.glob("*.json"):
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
                if j.get("id"):
                    body_by_id[j["id"]] = j.get("content") or ""
            except Exception:
                continue

    out_root = _ROOT / "data" / "outputs"
    from collections import defaultdict
    changed = defaultdict(list)

    with connect() as c:
        where = "WHERE v.company_slug = ?" if args.only else ""
        params = (args.only,) if args.only else ()
        rows = c.execute(
            f"""SELECT v.company_slug AS slug, v.article_id AS aid, v.criticality_band AS band,
                       v.personalised_analysis AS pa, p.title AS title
                FROM company_article_view v JOIN article_pool p ON p.id = v.article_id
                {where} ORDER BY v.company_slug""",
            params,
        ).fetchall()

        for r in rows:
            pa = _loadj(r["pa"])
            wim = pa.get("why_it_matters") or {}
            wc = pa.get("what_changed") or {}
            band = (r["band"] or wim.get("materiality_band") or "LOW").upper()
            prefix = _BAND_PREFIX.get(band, "Worth reviewing")
            title = r["title"] or ""
            body = body_by_id.get(r["aid"], "")
            summary = wim.get("criticality_summary") or ""
            headline = wc.get("headline") or ""
            touched = []

            # Fix B (headline) — orphan-unit garble → use the genuine title
            if headline and _has_orphan_unit(headline) and title:
                wc["headline"] = title[:240]
                headline = title
                touched.append("headline")

            # Decide if the summary needs a rebuild
            need_rebuild = False
            reason = ""
            norm = summary.strip().lower().rstrip(".")
            if not norm:
                need_rebuild, reason = True, "empty"
            elif norm in _ALL_BAND or norm in _BAND_WORDS:
                need_rebuild, reason = True, "bare-band(C)"
            elif _has_orphan_unit(summary):
                need_rebuild, reason = True, "orphan(B)"
            elif body and not money_grounded(summary, body)[0]:
                need_rebuild, reason = True, "ungrounded-₹(A)"

            if need_rebuild:
                topic = _headline_topic(headline or title)
                new_summary = (f"{prefix} — {topic}." if topic
                               else f"{prefix} — material ESG development for your company.")
                if new_summary != summary:
                    wim["criticality_summary"] = new_summary[:280]
                    touched.append(f"summary[{reason}]")

            if touched:
                pa["why_it_matters"] = wim
                pa["what_changed"] = wc
                changed[r["slug"]].append((r["aid"], ", ".join(touched),
                                           (wim.get("criticality_summary") or "")[:80]))
                if args.apply:
                    c.execute(
                        "UPDATE company_article_view SET personalised_analysis = ? "
                        "WHERE company_slug = ? AND article_id = ?",
                        (json.dumps(pa), r["slug"], r["aid"]),
                    )
                    ins_dir = out_root / r["slug"] / "insights"
                    if ins_dir.exists():
                        for f in ins_dir.glob(f"*{r['aid']}*.json"):
                            try:
                                d = json.loads(f.read_text(encoding="utf-8"))
                                an = (d.get("insight") or {}).get("analysis") or {}
                                if an:
                                    an["why_it_matters"] = wim
                                    an["what_changed"] = wc
                                    f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
                            except Exception as exc:  # noqa: BLE001
                                logger.warning("disk sync %s: %s", f.name, exc)
        if args.apply:
            c.commit()

    print("\n" + "=" * 70)
    print(f"  RECOMPOSE {'APPLIED' if args.apply else '(dry-run)'}")
    total = 0
    for slug in sorted(changed):
        print(f"\n  {slug} ({len(changed[slug])})")
        for aid, what, newsum in changed[slug]:
            print(f"      [{what}] -> {newsum!r}")
        total += len(changed[slug])
    print(f"\n  total cards changed: {total}")
    if not args.apply and total:
        print("  (re-run with --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
