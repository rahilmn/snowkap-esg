"""W1.4 — User tier (Tier 2) materialiser.

Builds the per-analyst wiki layered ON TOP of Tier 1. For user
`alice@snowkap.com`:

  wiki/users/alice-snowkap-com/
  ├── index.md
  ├── painpoints.md      ← from persona MCQ + click affinity
  ├── history.md         ← articles read (ordered most recent first)
  ├── saved.md           ← starred articles
  ├── themes/<theme>.md  ← user-flavour theme analysis (history-filtered)
  └── log.md

User theme pages link UP to both the tenant theme page (which links UP
to the system theme page) — drill-up navigation is one click each tier.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.wiki.paths import (
    relative_link,
    system_theme_path,
    tenant_article_path,
    tenant_theme_path,
    user_history_path,
    user_index_path,
    user_log_path,
    user_painpoints_path,
    user_root,
    user_saved_path,
    user_theme_path,
)


@dataclass
class UserBuildResult:
    user_id: str
    painpoints_written: bool = False
    history_written: bool = False
    saved_written: bool = False
    themes_written: int = 0
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


def _index_page(user_id: str, theme_count: int, history_count: int, saved_count: int) -> str:
    front = {
        "type": "user_index",
        "user_id": user_id,
        "theme_count": theme_count,
        "history_count": history_count,
        "saved_count": saved_count,
    }
    parts = [_frontmatter(front), "", f"# {user_id}", ""]
    parts.append(f"- **Articles read**: {history_count}")
    parts.append(f"- **Articles saved**: {saved_count}")
    parts.append(f"- **Themes followed**: {theme_count}")
    parts.append("")
    parts.append("## Pages")
    parts.append("- [Painpoints](painpoints.md)")
    parts.append("- [Reading history](history.md)")
    parts.append("- [Saved articles](saved.md)")
    parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _painpoints_page(user_id: str, persona: dict[str, Any] | None) -> str:
    front = {"type": "user_painpoints", "user_id": user_id, "has_persona": bool(persona)}
    parts = [_frontmatter(front), "", f"# {user_id} — painpoints", ""]
    if not persona:
        parts.append("_The persona MCQ has not yet been completed by this user. Painpoints will populate after onboarding._")
        parts.append("")
        return "\n".join(parts).rstrip() + "\n"

    role = persona.get("role")
    if role:
        parts.append(f"**Role**: {role}")
    parts.append("")

    for key in ("esg_focus", "frameworks", "geographies"):
        vals = persona.get(key) or []
        if vals:
            parts.append(f"## {key.replace('_', ' ').title()}")
            parts.append("")
            for v in sorted(vals):
                parts.append(f"- {v}")
            parts.append("")

    for key in ("horizon", "decision_style", "risk_appetite"):
        v = persona.get(key)
        if v:
            parts.append(f"**{key.replace('_', ' ').title()}**: {v}")

    parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _history_page(user_id: str, history: list[dict[str, Any]], *, base: Path | None) -> str:
    page_path = user_history_path(user_id, base=base)
    front = {"type": "user_history", "user_id": user_id, "count": len(history)}
    parts = [_frontmatter(front), "", f"# {user_id} — reading history", ""]
    parts.append(f"**Articles read**: {len(history)}")
    parts.append("")
    # Sort by read_at DESC; missing read_at sorts last
    ordered = sorted(history, key=lambda x: x.get("read_at") or "", reverse=True)
    for entry in ordered:
        article_id = entry.get("article_id") or ""
        tenant_slug = entry.get("tenant_slug") or ""
        title = entry.get("title") or entry.get("url") or article_id
        read_at = entry.get("read_at") or ""
        if tenant_slug and article_id:
            dst = tenant_article_path(tenant_slug, article_id, base=base)
            parts.append(f"- {read_at} [{title}]({relative_link(page_path, dst)}) (tenant: {tenant_slug})")
        else:
            parts.append(f"- {read_at} {title}")
    parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _saved_page(user_id: str, saved: list[dict[str, Any]], *, base: Path | None) -> str:
    page_path = user_saved_path(user_id, base=base)
    front = {"type": "user_saved", "user_id": user_id, "count": len(saved)}
    parts = [_frontmatter(front), "", f"# {user_id} — saved articles", ""]
    if not saved:
        parts.append("_No saved articles yet._")
        parts.append("")
        return "\n".join(parts).rstrip() + "\n"
    for entry in saved:
        article_id = entry.get("article_id") or ""
        tenant_slug = entry.get("tenant_slug") or ""
        title = entry.get("title") or entry.get("url") or article_id
        if tenant_slug and article_id:
            dst = tenant_article_path(tenant_slug, article_id, base=base)
            parts.append(f"- [{title}]({relative_link(page_path, dst)})")
        else:
            parts.append(f"- {title}")
    parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _user_theme_page(
    user_id: str, theme: str, articles: list[dict[str, Any]], *, base: Path | None,
) -> str:
    page_path = user_theme_path(user_id, theme, base=base)
    front = {
        "type": "user_theme",
        "user_id": user_id,
        "theme": theme,
        "article_count": len(articles),
    }
    parts = [_frontmatter(front), "", f"# {user_id} → {theme}", ""]
    parts.append(f"**Articles read on `{theme}`**: {len(articles)}")
    parts.append("")
    # Cross-tier links: tenant theme view + system theme view.
    # For tenant link, pick the first tenant in the user's articles
    # (in practice users mostly operate in one tenant context).
    first_tenant = next(
        (a.get("tenant_slug") for a in articles if a.get("tenant_slug")),
        None,
    )
    if first_tenant:
        tenant_dst = tenant_theme_path(first_tenant, theme, base=base)
        parts.append(f"- [{first_tenant} view of {theme}]({relative_link(page_path, tenant_dst)})")
    sys_dst = system_theme_path(theme, base=base)
    parts.append(f"- [Global view of {theme}]({relative_link(page_path, sys_dst)})")
    parts.append("")

    parts.append("## Articles")
    parts.append("")
    for art in sorted(articles, key=lambda x: x.get("read_at") or "", reverse=True):
        article_id = art.get("article_id") or ""
        tenant_slug = art.get("tenant_slug") or ""
        title = art.get("title") or art.get("url") or article_id
        if tenant_slug and article_id:
            dst = tenant_article_path(tenant_slug, article_id, base=base)
            parts.append(f"- [{title}]({relative_link(page_path, dst)})")
        else:
            parts.append(f"- {title}")
    parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def build_user_tier(
    *,
    user_id: str,
    history: list[dict[str, Any]],
    saved: list[dict[str, Any]],
    persona: dict[str, Any] | None,
    base: Path | None = None,
) -> UserBuildResult:
    """Materialise the Tier-2 wiki for one user.

    Args:
        user_id: email or UUID
        history: list of read-article entries (each with article_id,
            tenant_slug, themes, read_at)
        saved: list of saved-article entries
        persona: persona MCQ payload (or None if not completed)
        base: optional repo-root override
    """
    result = UserBuildResult(user_id=user_id)
    user_root(user_id, base=base, mkdir=True)

    # Group history by theme for the per-theme user pages
    theme_articles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in history:
        if not isinstance(entry, dict):
            continue
        for t in entry.get("themes") or []:
            theme_articles[t].append(entry)

    # 1) Painpoints
    _write(user_painpoints_path(user_id, base=base),
           _painpoints_page(user_id, persona))
    result.painpoints_written = True

    # 2) History
    _write(user_history_path(user_id, base=base),
           _history_page(user_id, history, base=base))
    result.history_written = True

    # 3) Saved
    _write(user_saved_path(user_id, base=base),
           _saved_page(user_id, saved, base=base))
    result.saved_written = True

    # 4) Theme pages
    for theme, articles in theme_articles.items():
        _write(
            user_theme_path(user_id, theme, base=base),
            _user_theme_page(user_id, theme, articles, base=base),
        )
        result.themes_written += 1

    # 5) Index
    _write(
        user_index_path(user_id, base=base),
        _index_page(
            user_id,
            theme_count=len(theme_articles),
            history_count=len(history),
            saved_count=len(saved),
        ),
    )

    # 6) Log
    _append_log(user_id, result, base=base)
    return result


def _append_log(user_id: str, result: UserBuildResult, *, base: Path | None) -> None:
    path = user_log_path(user_id, base=base)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user_id": user_id,
        "themes_written": result.themes_written,
        "painpoints_written": result.painpoints_written,
        "history_written": result.history_written,
        "saved_written": result.saved_written,
        "warnings": result.warnings,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
    result.log_appended = True
