"""W1.3 — Tenant tier (Tier 1) materialiser.

Builds the per-company wiki layered ON TOP of Tier 0. For tenant
`adani-power` this produces:

  wiki/tenants/adani-power/
  ├── index.md                  ← tenant catalog
  ├── articles/<id>.md          ← per-article tenant-flavour analysis
  ├── themes/<theme>.md         ← theme view filtered to this tenant
  ├── relations.md              ← competitors / suppliers / regulators
  ├── beliefs.md                ← from CompanyAgent.load_from_disk
  └── log.md                    ← append-only log

Article pages cross-link UP to the system tier (the same URL's
Tier-0 article page). Theme pages link to the global Tier-0 theme view.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from engine.wiki.paths import (
    relative_link,
    system_article_path,
    system_theme_path,
    tenant_article_path,
    tenant_belief_path,
    tenant_index_path,
    tenant_log_path,
    tenant_relations_path,
    tenant_root,
    tenant_theme_path,
)


@dataclass
class TenantBuildResult:
    tenant_slug: str
    articles_written: int = 0
    themes_written: int = 0
    relations_written: bool = False
    beliefs_written: bool = False
    log_appended: bool = False
    warnings: list[str] = field(default_factory=list)


def _frontmatter(d: dict[str, Any]) -> str:
    lines = ["---"]
    for k in sorted(d.keys()):
        v = d[k]
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
        elif v is None:
            lines.append(f"{k}: null")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")


def _tenant_article_page(
    insight: dict[str, Any], *, tenant_slug: str, base: Path | None,
) -> str:
    article_id = insight.get("article_id") or ""
    url = insight.get("url") or ""
    title = insight.get("title") or url
    published_at = insight.get("published_at") or ""
    themes = list(insight.get("themes") or [])
    event_id = insight.get("event_id") or ""
    materiality = (insight.get("materiality") or
                   (insight.get("decision_summary") or {}).get("materiality") or "")
    tier = insight.get("tier") or ""
    decision = insight.get("decision_summary") or {}
    summary = insight.get("summary") or ""

    page_path = tenant_article_path(tenant_slug, article_id, base=base)
    front = {
        "type": "tenant_article",
        "tenant": tenant_slug,
        "article_id": article_id,
        "url": url,
        "published_at": published_at,
        "themes": sorted(themes),
        "event_id": event_id,
        "materiality": materiality,
        "tier": tier,
    }
    parts = [_frontmatter(front), "", f"# {title}", ""]
    parts.append(f"**Tenant**: {tenant_slug}")
    if materiality:
        parts.append(f"**Materiality**: {materiality}")
    if tier:
        parts.append(f"**Tier**: {tier}")
    parts.append("")

    if summary:
        parts.append("## Summary")
        parts.append("")
        parts.append(summary)
        parts.append("")

    if decision:
        fe = decision.get("financial_exposure")
        kr = decision.get("key_risk")
        op = decision.get("top_opportunity")
        if fe or kr or op:
            parts.append("## Decision summary")
            parts.append("")
            if fe:
                parts.append(f"- **Financial exposure**: {fe}")
            if kr:
                parts.append(f"- **Key risk**: {kr}")
            if op:
                parts.append(f"- **Top opportunity**: {op}")
            parts.append("")

    # System article cross-link
    sys_art = system_article_path(published_at=published_at or None, url=url, base=base)
    parts.append("## System-level view")
    parts.append("")
    parts.append(f"- [System article page]({relative_link(page_path, sys_art)})")
    parts.append("")

    # Theme cross-links (to TENANT theme pages, which link to system)
    if themes:
        parts.append("## Themes (this tenant)")
        parts.append("")
        for t in sorted(themes):
            dst = tenant_theme_path(tenant_slug, t, base=base)
            parts.append(f"- [{t}]({relative_link(page_path, dst)})")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _tenant_theme_page(
    theme: str, articles: list[dict[str, Any]], *,
    tenant_slug: str, base: Path | None,
) -> str:
    page_path = tenant_theme_path(tenant_slug, theme, base=base)
    front = {
        "type": "tenant_theme",
        "tenant": tenant_slug,
        "theme": theme,
        "article_count": len(articles),
    }
    parts = [_frontmatter(front), "", f"# {tenant_slug} → {theme}", ""]
    parts.append(f"**Articles tagged with `{theme}` for `{tenant_slug}`**: {len(articles)}")
    parts.append("")

    # Cross-link to system theme view
    sys_theme = system_theme_path(theme, base=base)
    parts.append(f"- [Global view of {theme}]({relative_link(page_path, sys_theme)})")
    parts.append("")

    parts.append("## Articles")
    parts.append("")
    for art in sorted(articles, key=lambda x: x.get("published_at") or "", reverse=True):
        art_id = art.get("article_id") or ""
        dst = tenant_article_path(tenant_slug, art_id, base=base)
        title = art.get("title") or art.get("url") or art_id
        parts.append(f"- [{title}]({relative_link(page_path, dst)})")
    parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _tenant_index_page(
    tenant_slug: str,
    article_count: int,
    themes: list[str],
    *,
    base: Path | None,
) -> str:
    page_path = tenant_index_path(tenant_slug, base=base)
    front = {
        "type": "tenant_index",
        "tenant": tenant_slug,
        "article_count": article_count,
        "theme_count": len(themes),
    }
    parts = [_frontmatter(front), "", f"# {tenant_slug}", ""]
    parts.append(f"**Articles analysed**: {article_count}")
    parts.append(f"**Themes covered**: {len(themes)}")
    parts.append("")
    parts.append("## Themes")
    parts.append("")
    for t in sorted(themes):
        dst = tenant_theme_path(tenant_slug, t, base=base)
        parts.append(f"- [{t}]({relative_link(page_path, dst)})")
    parts.append("")
    parts.append("## Other pages")
    parts.append("")
    parts.append(f"- [Relations]({relative_link(page_path, tenant_relations_path(tenant_slug, base=base))})")
    parts.append(f"- [Beliefs]({relative_link(page_path, tenant_belief_path(tenant_slug, base=base))})")
    parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _tenant_relations_page(
    tenant_slug: str, competitors: list[str], *, base: Path | None,
) -> str:
    front = {
        "type": "tenant_relations",
        "tenant": tenant_slug,
        "competitor_count": len(competitors),
    }
    parts = [_frontmatter(front), "", f"# {tenant_slug} — relations", ""]
    parts.append("## Competitors")
    parts.append("")
    for c in sorted(competitors):
        parts.append(f"- {c}")
    parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _tenant_beliefs_page(
    tenant_slug: str, beliefs_audit_dir: Path | None, *, base: Path | None,
) -> tuple[str, bool]:
    """Pull the latest belief snapshot from CompanyAgent.load_from_disk."""
    from engine.governance.company_agent import CompanyAgent
    agent = CompanyAgent.load_from_disk(tenant=tenant_slug, audit_dir=beliefs_audit_dir)
    front = {
        "type": "tenant_beliefs",
        "tenant": tenant_slug,
        "belief_count": len(agent.beliefs),
    }
    parts = [_frontmatter(front), "", f"# {tenant_slug} — beliefs", ""]
    parts.append(f"**Tracked beliefs**: {len(agent.beliefs)}")
    parts.append("")
    if not agent.beliefs:
        parts.append("_No beliefs tracked yet. The CompanyAgent will populate this as analyst review accumulates._")
        parts.append("")
        return "\n".join(parts).rstrip() + "\n", False

    parts.append("## Beliefs")
    parts.append("")
    for name in sorted(agent.beliefs.keys()):
        b = agent.beliefs[name]
        # Format value compactly — if it's a dict (typed-belief payload),
        # extract the discriminating field
        val_str = ""
        if isinstance(b.value, dict):
            kind = b.value.get("kind", "")
            if kind == "risk_band":
                val_str = f"{b.value.get('topic')} = {b.value.get('band')}"
            elif kind == "financial_exposure":
                lo = b.value.get("exposure_cr_lo")
                hi = b.value.get("exposure_cr_hi")
                val_str = f"₹{lo}-{hi} Cr"
            else:
                val_str = str({k: v for k, v in b.value.items() if k not in ("kind",)})
        else:
            val_str = str(b.value)
        parts.append(f"- **{name}** ({b.confidence}): {val_str}")
        if b.rationale:
            parts.append(f"  - _{b.rationale}_")
    parts.append("")
    return "\n".join(parts).rstrip() + "\n", True


def build_tenant_tier(
    *,
    tenant_slug: str,
    insights: Iterable[dict[str, Any]],
    competitors: list[str] | None = None,
    beliefs_audit_dir: Path | None = None,
    base: Path | None = None,
) -> TenantBuildResult:
    """Materialise the Tier-1 wiki for one tenant.

    Args:
        tenant_slug: company slug (e.g. 'adani-power')
        insights: iterable of insight dicts; insights NOT belonging to
            this tenant are filtered out
        competitors: optional list of competitor slugs for relations.md
        beliefs_audit_dir: optional audit_dir override for
            CompanyAgent.load_from_disk (used by tests)
        base: optional repo-root override (used by tests)
    """
    result = TenantBuildResult(tenant_slug=tenant_slug)
    tenant_root(tenant_slug, base=base, mkdir=True)

    # Filter to this tenant + group by article + by theme
    by_article: dict[str, dict[str, Any]] = {}
    theme_articles: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for raw in insights:
        if not isinstance(raw, dict):
            continue
        if raw.get("tenant_slug") != tenant_slug:
            continue
        article_id = str(raw.get("article_id") or "")
        if not article_id:
            result.warnings.append("insight missing article_id; skipped")
            continue
        by_article[article_id] = raw
        for t in raw.get("themes") or []:
            theme_articles[t].append(raw)

    # 1) Per-article tenant pages
    for article_id, insight in by_article.items():
        _write(
            tenant_article_path(tenant_slug, article_id, base=base),
            _tenant_article_page(insight, tenant_slug=tenant_slug, base=base),
        )
        result.articles_written += 1

    # 2) Theme pages
    for theme, articles in theme_articles.items():
        _write(
            tenant_theme_path(tenant_slug, theme, base=base),
            _tenant_theme_page(theme, articles, tenant_slug=tenant_slug, base=base),
        )
        result.themes_written += 1

    # 3) Relations
    _write(
        tenant_relations_path(tenant_slug, base=base),
        _tenant_relations_page(tenant_slug, competitors or [], base=base),
    )
    result.relations_written = True

    # 4) Beliefs
    beliefs_content, has_beliefs = _tenant_beliefs_page(
        tenant_slug, beliefs_audit_dir=beliefs_audit_dir, base=base,
    )
    _write(tenant_belief_path(tenant_slug, base=base), beliefs_content)
    result.beliefs_written = has_beliefs

    # 5) Index
    _write(
        tenant_index_path(tenant_slug, base=base),
        _tenant_index_page(
            tenant_slug, len(by_article), list(theme_articles.keys()), base=base,
        ),
    )

    # 6) Log
    _append_log(tenant_slug, result, base=base)
    return result


def _append_log(tenant_slug: str, result: TenantBuildResult, *, base: Path | None) -> None:
    path = tenant_log_path(tenant_slug, base=base)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tenant": tenant_slug,
        "articles_written": result.articles_written,
        "themes_written": result.themes_written,
        "relations_written": result.relations_written,
        "beliefs_written": result.beliefs_written,
        "warnings": result.warnings,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
    result.log_appended = True
