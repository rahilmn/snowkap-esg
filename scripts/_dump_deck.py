"""Dump a company's live deck from prod as JSON — for demo-readiness auditing.

Read-only. Uses the SAME read path the frontend uses (deck_for_company), so what
it prints is what the demo shows. Per article: tier, criticality band/score,
source_type (industry_thematic = the new sector lane), has_lede/has_recs, the
why-it-matters summary, and each recommendation's gate-relevant fields.

Usage: python scripts/_dump_deck.py <slug>           # human-readable
       python scripts/_dump_deck.py <slug> --json     # machine-readable (audit)
"""
from __future__ import annotations

import json
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

from engine.config import load_companies  # noqa: E402
from engine.models.company_article_view import deck_for_company  # noqa: E402
from engine.models import insight_payload as _ip  # noqa: E402


def _full_recs(article_id: str) -> list:
    """Full gated recs from the Postgres insight_payload mirror (peer / framework
    / ₹ / payback / audit_trail) — the detail-view recs the demo shows on open."""
    try:
        payload = _ip.get(article_id) or {}
    except Exception:  # noqa: BLE001
        return []
    recs = (payload.get("recommendations") or {})
    if isinstance(recs, dict):
        return recs.get("recommendations") or []
    return recs if isinstance(recs, list) else []


def _company(slug: str):
    for c in load_companies():
        if c.slug == slug:
            return c
    return None


def dump(slug: str) -> dict:
    co = _company(slug)
    if co is None:
        return {"slug": slug, "error": "unknown company"}
    rows, meta = deck_for_company(co.slug, co.industry, max_age_days=45, limit=10)
    items = []
    for r in rows:
        personal = r.get("personalised_analysis") or {}
        why = (personal.get("why_it_matters") or {}) if isinstance(personal, dict) else {}
        lede = (personal.get("lede") or {}) if isinstance(personal, dict) else {}
        # Card-level action bullets (deck view) + full gated recs (detail mirror).
        card_actions = ((personal.get("what_it_triggers") or {}).get("recommended_actions") or []) \
            if isinstance(personal, dict) else []
        recs = _full_recs(r.get("article_id"))
        rec_view = []
        for rec in (recs or [])[:4]:
            if not isinstance(rec, dict):
                continue
            rec_view.append({
                "title": rec.get("title", "")[:120],
                "peer_benchmark": rec.get("peer_benchmark", ""),
                "framework_section": rec.get("framework_section", ""),
                "estimated_budget": rec.get("estimated_budget", ""),
                "payback_months": rec.get("payback_months", ""),
                "audit_trail_n": len(rec.get("audit_trail") or []),
            })
        items.append({
            "title": (r.get("title") or "")[:140],
            "source": r.get("source", ""),
            "published_at": str(r.get("published_at", ""))[:10],
            "tier": r.get("tier", ""),
            "criticality_band": r.get("criticality_band", ""),
            "criticality_score": r.get("criticality_score", ""),
            "source_type": (personal.get("source_type") or r.get("source_type") or ""),
            "primary_theme": r.get("primary_theme", ""),
            "event_id": r.get("event_id", ""),
            "has_lede": bool((lede.get("text") or "").strip()),
            "lede": (lede.get("text") or "")[:300],
            "why_it_matters": (why.get("criticality_summary") or "")[:300],
            "n_card_actions": len(card_actions),
            "n_recs": len(rec_view),
            "recs": rec_view,
        })
    crit = [i for i in items if i["tier"] == "critical"]
    return {
        "slug": slug, "industry": co.industry, "sasb": co.sasb_category,
        "meta": meta,
        "n_total": len(items), "n_critical": len(crit),
        "n_critical_with_recs": sum(1 for i in crit if i["n_recs"] > 0),
        "n_thematic": sum(1 for i in items if i["source_type"] == "industry_thematic"),
        "items": items,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: _dump_deck.py <slug> [--json]")
        return 2
    slug = sys.argv[1]
    data = dump(slug)
    if "--json" in sys.argv:
        print(json.dumps(data, default=str, ensure_ascii=False))
        return 0
    print(f"== {slug} ({data.get('industry')}, SASB={data.get('sasb')}) ==")
    print(f"meta={data.get('meta')}  total={data.get('n_total')} "
          f"critical={data.get('n_critical')} crit_with_recs={data.get('n_critical_with_recs')} "
          f"thematic={data.get('n_thematic')}\n")
    for i in data.get("items", []):
        flag = "THEMATIC" if i["source_type"] == "industry_thematic" else "named"
        print(f"[{i['tier']:8}|{i['criticality_band']:8}|{i['criticality_score']}|{flag}] {i['title']}")
        print(f"    theme={i['primary_theme']!r} event={i['event_id']!r} recs={i['n_recs']}")
        if i["tier"] == "critical":
            print(f"    lede: {i['lede'][:160]}")
            print(f"    why : {i['why_it_matters'][:160]}")
            for rec in i["recs"]:
                print(f"      • {rec['title']} | peer={rec['peer_benchmark']!r} "
                      f"fw={rec['framework_section']!r} ₹={rec['estimated_budget']!r} "
                      f"pay={rec['payback_months']!r} audit={rec['audit_trail_n']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
