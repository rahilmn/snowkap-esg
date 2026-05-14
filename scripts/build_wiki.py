"""W1.7 — CLI for building the 3-tier wiki from existing engine outputs.

Usage:
    python scripts/build_wiki.py --system
    python scripts/build_wiki.py --tenant adani-power
    python scripts/build_wiki.py --user alice@snowkap.com
    python scripts/build_wiki.py --all

Reads:
    data/outputs/*/insights/*.json   (insight payloads)
    data/agents/<tenant>/beliefs.json (CompanyAgent snapshots)
    engine/persona/*                  (persona MCQ + click affinity)

Writes:
    wiki/system/...
    wiki/tenants/<slug>/...
    wiki/users/<id>/...

Idempotent: re-running with the same inputs produces byte-identical
output (except for `log.md` which appends one line per run).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))


def _flatten_insight(raw: dict, tenant_slug: str, fallback_id: str) -> dict:
    """Flatten the nested {article, pipeline, insight, ...} JSON shape
    into the flat dict the wiki builders expect.

    The real-world output files are:
      - top-level: {article, pipeline, insight, recommendations, perspectives, meta}
      - article.url / article.title / article.published_at / article.id
      - pipeline.themes.primary_theme (string) + pipeline.themes.secondary_themes
      - pipeline.event.event_id or pipeline.event_id
    """
    article = raw.get("article") or {}
    pipeline = raw.get("pipeline") or {}
    insight = raw.get("insight") or {}

    themes_block = pipeline.get("themes") or {}
    themes: list[str] = []
    if isinstance(themes_block, dict):
        primary = themes_block.get("primary_theme")
        if primary:
            themes.append(str(primary))
        secondary = themes_block.get("secondary_themes")
        if isinstance(secondary, list):
            themes.extend(str(t) for t in secondary if t)
    elif isinstance(themes_block, list):
        themes = [str(t) for t in themes_block if t]

    event_block = pipeline.get("event") or {}
    event_id = ""
    if isinstance(event_block, dict):
        event_id = str(event_block.get("event_id") or "")
    if not event_id:
        event_id = str(pipeline.get("event_id") or "")

    return {
        "article_id": str(
            article.get("id")
            or pipeline.get("article_id")
            or raw.get("article_id")
            or fallback_id
        ),
        "tenant_slug": str(article.get("company_slug") or tenant_slug),
        "url": article.get("url") or pipeline.get("url") or "",
        "title": article.get("title") or pipeline.get("title") or "",
        "published_at": article.get("published_at") or pipeline.get("published_at") or "",
        "summary": (insight.get("net_impact_summary") or "")[:1000],
        "themes": themes,
        "event_id": event_id,
        "materiality": (insight.get("decision_summary") or {}).get("materiality") or "",
        "tier": pipeline.get("tier") or "",
        "decision_summary": insight.get("decision_summary") or {},
    }


def _load_insights(repo_root: Path) -> list[dict]:
    """Walk data/outputs/<tenant>/insights/*.json and return all insight dicts.

    Each entry is flattened into the shape the wiki builders expect.
    """
    out: list[dict] = []
    outputs_dir = repo_root / "data" / "outputs"
    if not outputs_dir.exists():
        return out
    for tenant_dir in outputs_dir.iterdir():
        if not tenant_dir.is_dir():
            continue
        insights_dir = tenant_dir / "insights"
        if not insights_dir.exists():
            continue
        for json_path in insights_dir.glob("*.json"):
            try:
                raw = json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            flat = _flatten_insight(raw, tenant_slug=tenant_dir.name, fallback_id=json_path.stem)
            out.append(flat)
    return out


def _build_system(insights: list[dict]) -> None:
    from engine.wiki.system_builder import build_system_tier
    result = build_system_tier(insights)
    print(f"system: {result.articles_written} articles, "
          f"{result.themes_written} themes, {result.events_written} events, "
          f"{result.entities_written} entities")


def _build_tenant(slug: str, insights: list[dict]) -> None:
    from engine.wiki.tenant_builder import build_tenant_tier
    # Pull competitors from companies.json if available
    competitors: list[str] = []
    try:
        from engine.config import load_companies
        companies = load_companies()
        for c in companies:
            if c.slug == slug:
                # The Company dataclass may carry competitors; tolerate absence
                competitors = list(getattr(c, "competitors", []) or [])
                break
    except Exception:
        pass
    result = build_tenant_tier(
        tenant_slug=slug,
        insights=insights,
        competitors=competitors,
    )
    print(f"tenant {slug}: {result.articles_written} articles, "
          f"{result.themes_written} themes")


def _build_user(user_id: str) -> None:
    """Build Tier 2 from the user's persona + history.

    History + saved are read from the engine.persona store if available;
    otherwise default to empty lists (the builder handles that case).
    """
    from engine.wiki.user_builder import build_user_tier
    persona: dict | None = None
    history: list[dict] = []
    saved: list[dict] = []
    try:
        from engine.persona import get_persona
        p = get_persona(user_id)
        if p:
            persona = {
                "role": getattr(p, "role", None),
                "esg_focus": list(getattr(p, "esg_focus", []) or []),
                "frameworks": list(getattr(p, "frameworks", []) or []),
                "geographies": list(getattr(p, "geographies", []) or []),
                "horizon": getattr(p, "horizon", None),
                "decision_style": getattr(p, "decision_style", None),
                "risk_appetite": getattr(p, "risk_appetite", None),
            }
    except Exception:
        pass
    result = build_user_tier(
        user_id=user_id,
        history=history,
        saved=saved,
        persona=persona,
    )
    print(f"user {user_id}: {result.themes_written} themes, "
          f"persona={result.painpoints_written}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system", action="store_true", help="rebuild Tier 0 (system)")
    parser.add_argument("--tenant", help="rebuild Tier 1 for a tenant slug")
    parser.add_argument("--user", help="rebuild Tier 2 for a user_id")
    parser.add_argument("--all", action="store_true", help="rebuild all tiers")
    args = parser.parse_args()

    if not (args.system or args.tenant or args.user or args.all):
        parser.print_help()
        return 1

    insights = _load_insights(_REPO_ROOT)
    print(f"Loaded {len(insights)} insights from data/outputs/")

    if args.system or args.all:
        _build_system(insights)

    if args.all:
        # Build every tenant we have insights for
        seen_tenants = sorted({i.get("tenant_slug") for i in insights if i.get("tenant_slug")})
        for slug in seen_tenants:
            _build_tenant(slug, insights)
    elif args.tenant:
        _build_tenant(args.tenant, insights)

    if args.user:
        _build_user(args.user)

    return 0


if __name__ == "__main__":
    sys.exit(main())
