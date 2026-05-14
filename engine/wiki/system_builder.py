"""W1.2 — System tier (Tier 0) materialiser.

Walks an iterable of insight dicts and materialises:
  - One article page per unique URL (aggregating analyses from
    multiple tenants on the same URL)
  - One theme page per ESG theme seen
  - One event-type page per `event_id`
  - One entity page per tenant_slug encountered
  - A system index.md catalog
  - An append-only log.md (one line per build)

The insight dict shape is the same one produced by the engine's
output writer: `{article_id, tenant_slug, url, title, published_at,
themes, event_id, summary, ...}`. Missing fields are tolerated; the
builder never raises on a single bad insight.

Idempotency rule: rebuilding with the same inputs produces byte-
identical files (except for `log.md`, which is the build log). The
generated frontmatter is sorted; content sections are deterministic.
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
    system_entity_path,
    system_event_path,
    system_index_path,
    system_log_path,
    system_root,
    system_theme_path,
)


@dataclass
class SystemBuildResult:
    articles_written: int = 0
    themes_written: int = 0
    events_written: int = 0
    entities_written: int = 0
    log_appended: bool = False
    warnings: list[str] = field(default_factory=list)


def _frontmatter(d: dict[str, Any]) -> str:
    """Render a YAML-ish frontmatter block. Keeps key order stable for
    idempotency."""
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
    """Write idempotently — only touch the file when content changed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")


def _article_page(
    *,
    url: str,
    title: str,
    published_at: str | None,
    summary: str,
    tenants: list[str],
    themes: list[str],
    event_id: str | None,
    tenant_article_ids: dict[str, str],
    base: Path | None,
) -> str:
    """Build the system-tier article page markdown."""
    page_path = system_article_path(published_at=published_at, url=url, base=base)

    front = {
        "type": "article",
        "url": url,
        "published_at": published_at or "",
        "tenants": sorted(tenants),
        "themes": sorted(themes),
        "event_id": event_id or "",
    }
    parts = [_frontmatter(front), "", f"# {title}", ""]
    parts.append(f"**Source**: {url}")
    if published_at:
        parts.append(f"**Published**: {published_at}")
    parts.append("")
    if summary:
        parts.append("## Summary")
        parts.append("")
        parts.append(summary)
        parts.append("")

    if tenants:
        parts.append("## Tenant analyses")
        parts.append("")
        for slug in sorted(tenants):
            from engine.wiki.paths import tenant_article_path
            art_id = tenant_article_ids.get(slug, "unknown")
            dst = tenant_article_path(slug, art_id, base=base)
            parts.append(f"- [{slug}]({relative_link(page_path, dst)})")
        parts.append("")

    if themes:
        parts.append("## Themes")
        parts.append("")
        for theme in sorted(themes):
            dst = system_theme_path(theme, base=base)
            parts.append(f"- [{theme}]({relative_link(page_path, dst)})")
        parts.append("")

    if event_id:
        parts.append("## Event type")
        parts.append("")
        dst = system_event_path(event_id, base=base)
        parts.append(f"- [{event_id}]({relative_link(page_path, dst)})")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _theme_page(theme: str, articles: list[dict[str, Any]], *, base: Path | None) -> str:
    page_path = system_theme_path(theme, base=base)
    front = {"type": "theme", "name": theme, "article_count": len(articles)}
    parts = [_frontmatter(front), "", f"# Theme: {theme}", ""]
    parts.append(f"**Articles tagged**: {len(articles)}")
    parts.append("")
    parts.append("## Articles")
    parts.append("")
    for art in sorted(articles, key=lambda x: x.get("published_at") or "", reverse=True):
        dst = system_article_path(
            published_at=art.get("published_at"), url=art["url"], base=base,
        )
        title = art.get("title") or art["url"]
        ts = art.get("published_at") or ""
        parts.append(f"- {ts} [{title}]({relative_link(page_path, dst)})")
    parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _event_page(
    event_id: str, articles: list[dict[str, Any]], *, base: Path | None,
) -> str:
    page_path = system_event_path(event_id, base=base)
    front = {"type": "event_type", "event_id": event_id, "article_count": len(articles)}
    parts = [_frontmatter(front), "", f"# Event type: {event_id}", ""]
    parts.append(f"**Instances**: {len(articles)}")
    parts.append("")
    parts.append("## Articles")
    parts.append("")
    for art in sorted(articles, key=lambda x: x.get("published_at") or "", reverse=True):
        dst = system_article_path(
            published_at=art.get("published_at"), url=art["url"], base=base,
        )
        title = art.get("title") or art["url"]
        parts.append(f"- [{title}]({relative_link(page_path, dst)})")
    parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _entity_page(slug: str, articles: list[dict[str, Any]], *, base: Path | None) -> str:
    page_path = system_entity_path(slug, base=base)
    front = {"type": "entity", "slug": slug, "article_count": len(articles)}
    parts = [_frontmatter(front), "", f"# Entity: {slug}", ""]
    parts.append(f"**Articles referencing this entity**: {len(articles)}")
    parts.append("")
    parts.append("## Articles")
    parts.append("")
    for art in sorted(articles, key=lambda x: x.get("published_at") or "", reverse=True):
        dst = system_article_path(
            published_at=art.get("published_at"), url=art["url"], base=base,
        )
        title = art.get("title") or art["url"]
        parts.append(f"- [{title}]({relative_link(page_path, dst)})")
    parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _index_page(
    themes: list[str], events: list[str], entities: list[str],
    article_count: int, *, base: Path | None,
) -> str:
    page_path = system_index_path(base=base)
    front = {
        "type": "system_index",
        "article_count": article_count,
        "theme_count": len(themes),
        "event_count": len(events),
        "entity_count": len(entities),
    }
    parts = [_frontmatter(front), "", "# Snowkap System Wiki", ""]
    parts.append("This is the institutional memory across all tenants. Every article ever fetched, every theme ever tagged, every event ever classified.")
    parts.append("")
    parts.append(f"- **Articles**: {article_count}")
    parts.append(f"- **Themes**: {len(themes)}")
    parts.append(f"- **Event types**: {len(events)}")
    parts.append(f"- **Entities**: {len(entities)}")
    parts.append("")

    parts.append("## Themes")
    parts.append("")
    for theme in sorted(themes):
        dst = system_theme_path(theme, base=base)
        parts.append(f"- [{theme}]({relative_link(page_path, dst)})")
    parts.append("")

    parts.append("## Event types")
    parts.append("")
    for ev in sorted(events):
        dst = system_event_path(ev, base=base)
        parts.append(f"- [{ev}]({relative_link(page_path, dst)})")
    parts.append("")

    parts.append("## Entities (tenants)")
    parts.append("")
    for ent in sorted(entities):
        dst = system_entity_path(ent, base=base)
        parts.append(f"- [{ent}]({relative_link(page_path, dst)})")
    parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def build_system_tier(
    insights_iter: Iterable[dict[str, Any]],
    *,
    base: Path | None = None,
) -> SystemBuildResult:
    """Materialise the Tier-0 wiki from an iterable of insight dicts.

    Idempotent: rebuilding produces the same files (except `log.md`,
    which appends one line per call).
    """
    result = SystemBuildResult()
    system_root(base=base, mkdir=True)

    # Aggregate by URL so multiple-tenant analyses of the same article
    # produce ONE system page (with both tenants listed)
    by_url: dict[str, dict[str, Any]] = {}
    theme_articles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    event_articles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    entity_articles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    tenant_article_ids: dict[str, dict[str, str]] = defaultdict(dict)
    # tenant_article_ids[url][tenant_slug] = article_id (so the article
    # page can link to each tenant's per-article analysis page)

    for raw in insights_iter:
        if not isinstance(raw, dict):
            result.warnings.append(f"skipping non-dict insight: {type(raw).__name__}")
            continue
        url = raw.get("url") or ""
        if not url:
            result.warnings.append("insight missing url; skipped")
            continue
        article_id = str(raw.get("article_id") or "")
        title = raw.get("title") or url
        published_at = raw.get("published_at")
        themes = list(raw.get("themes") or [])
        event_id = raw.get("event_id") or ""
        tenant = raw.get("tenant_slug") or ""
        summary = raw.get("summary") or ""

        entry = by_url.setdefault(url, {
            "url": url,
            "title": title,
            "published_at": published_at,
            "summary": summary,
            "tenants": set(),
            "themes": set(),
            "event_id": event_id,
        })
        if tenant:
            entry["tenants"].add(tenant)
            tenant_article_ids[url][tenant] = article_id
            entity_articles[tenant].append(raw)
        for t in themes:
            entry["themes"].add(t)
            theme_articles[t].append(raw)
        if event_id:
            event_articles[event_id].append(raw)

    # 1) Write article pages
    for url, entry in by_url.items():
        content = _article_page(
            url=url,
            title=entry["title"],
            published_at=entry["published_at"],
            summary=entry["summary"],
            tenants=list(entry["tenants"]),
            themes=list(entry["themes"]),
            event_id=entry.get("event_id"),
            tenant_article_ids=tenant_article_ids.get(url, {}),
            base=base,
        )
        path = system_article_path(
            published_at=entry["published_at"], url=url, base=base,
        )
        _write(path, content)
        result.articles_written += 1

    # 2) Theme pages
    for theme, articles in theme_articles.items():
        _write(system_theme_path(theme, base=base), _theme_page(theme, articles, base=base))
        result.themes_written += 1

    # 3) Event pages
    for event_id, articles in event_articles.items():
        _write(system_event_path(event_id, base=base), _event_page(event_id, articles, base=base))
        result.events_written += 1

    # 4) Entity pages (per tenant slug)
    for slug, articles in entity_articles.items():
        _write(system_entity_path(slug, base=base), _entity_page(slug, articles, base=base))
        result.entities_written += 1

    # 5) Index
    _write(
        system_index_path(base=base),
        _index_page(
            themes=list(theme_articles.keys()),
            events=list(event_articles.keys()),
            entities=list(entity_articles.keys()),
            article_count=len(by_url),
            base=base,
        ),
    )

    # 6) Log (append-only, NEVER idempotent)
    _append_log(result, base=base)
    return result


def _append_log(result: SystemBuildResult, *, base: Path | None) -> None:
    path = system_log_path(base=base)
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = {
        "ts": ts,
        "articles_written": result.articles_written,
        "themes_written": result.themes_written,
        "events_written": result.events_written,
        "entities_written": result.entities_written,
        "warnings": result.warnings,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
    result.log_appended = True
