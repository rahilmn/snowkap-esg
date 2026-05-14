"""W1.1 — Wiki path conventions.

Single source of truth for the 3-tier layout. NEVER hand-roll a wiki
path elsewhere in the codebase — always import from here. The layout
is:

    wiki/
    ├── system/
    │   ├── articles/<YYYY>/<MM>/<hash>.md
    │   ├── themes/<theme>.md
    │   ├── entities/<entity_slug>.md
    │   ├── events/<event_type>.md
    │   └── log.md
    │
    ├── tenants/<tenant_slug>/
    │   ├── index.md
    │   ├── articles/<article_id>.md
    │   ├── themes/<theme>.md
    │   ├── relations.md
    │   ├── beliefs.md
    │   └── log.md
    │
    └── users/<user_slug>/
        ├── index.md
        ├── painpoints.md
        ├── history.md
        ├── saved.md
        ├── themes/<theme>.md
        └── log.md

The `base` argument lets tests redirect to a tmp_path; production
defaults to repo-root/wiki.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime
from pathlib import Path

WIKI_ROOT_DIRNAME = "wiki"


# ---------------------------------------------------------------------------
# Slug + hash helpers
# ---------------------------------------------------------------------------


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Lowercase, ASCII-only, hyphen-separated slug for filenames + URLs.

    Strips punctuation, collapses repeated dashes, trims leading/trailing
    dashes. Non-ASCII characters are dropped (the ESG corpus is overwhelmingly
    English; tenants with non-ASCII names should provide an explicit slug
    via companies.json).
    """
    if not value:
        return ""
    # Decompose unicode, drop combining marks, encode ASCII (drop non-ASCII)
    normalised = unicodedata.normalize("NFKD", value)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    dashed = _SLUG_RE.sub("-", lowered)
    return dashed.strip("-")


def article_hash(url: str) -> str:
    """Deterministic short hash for an article URL.

    Used as the filename for Tier-0 article pages. 12 hex chars =
    48 bits = 2.8E14 collision space; ample for the article corpus.
    """
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return h[:12]


# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Repo root = three levels up from this file (engine/wiki/paths.py)."""
    return Path(__file__).resolve().parent.parent.parent


def wiki_root(*, base: Path | None = None, mkdir: bool = False) -> Path:
    """Top-level `wiki/` dir. `base` overrides for tests."""
    root = (base if base is not None else _repo_root()) / WIKI_ROOT_DIRNAME
    if mkdir:
        root.mkdir(parents=True, exist_ok=True)
    return root


def system_root(*, base: Path | None = None, mkdir: bool = False) -> Path:
    p = wiki_root(base=base) / "system"
    if mkdir:
        p.mkdir(parents=True, exist_ok=True)
    return p


def tenant_root(tenant_slug: str, *, base: Path | None = None, mkdir: bool = False) -> Path:
    p = wiki_root(base=base) / "tenants" / tenant_slug
    if mkdir:
        p.mkdir(parents=True, exist_ok=True)
    return p


def user_root(user_id: str, *, base: Path | None = None, mkdir: bool = False) -> Path:
    """Per-user dir. `user_id` may be an email or a UUID; we slugify it
    so the filesystem path is portable."""
    slug = slugify(user_id) or "anonymous"
    p = wiki_root(base=base) / "users" / slug
    if mkdir:
        p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# System tier paths (Tier 0)
# ---------------------------------------------------------------------------


def system_article_path(
    *,
    published_at: str | None,
    url: str,
    base: Path | None = None,
) -> Path:
    """Tier-0 article page path: wiki/system/articles/<YYYY>/<MM>/<hash>.md.

    `published_at` accepts ISO-8601 or date-only. When missing/invalid
    we bucket under 0000/00 as a catch-all so the file still has a stable
    location.
    """
    year, month = "0000", "00"
    if published_at:
        try:
            # Handle 'Z' suffix and timezone offsets uniformly
            ts = published_at.rstrip("Z").replace("Z", "")
            dt = datetime.fromisoformat(ts)
            year, month = f"{dt.year:04d}", f"{dt.month:02d}"
        except (TypeError, ValueError):
            pass
    return system_root(base=base) / "articles" / year / month / f"{article_hash(url)}.md"


def system_theme_path(theme: str, *, base: Path | None = None) -> Path:
    return system_root(base=base) / "themes" / f"{slugify(theme) or 'unknown'}.md"


def system_entity_path(entity: str, *, base: Path | None = None) -> Path:
    return system_root(base=base) / "entities" / f"{slugify(entity) or 'unknown'}.md"


def system_event_path(event_type: str, *, base: Path | None = None) -> Path:
    # Event types are already snake_case (event_*) — preserve them verbatim,
    # only slugify defensively if the input is messy
    safe = event_type if event_type.startswith("event_") else slugify(event_type)
    return system_root(base=base) / "events" / f"{safe or 'unknown'}.md"


def system_log_path(*, base: Path | None = None) -> Path:
    return system_root(base=base) / "log.md"


def system_index_path(*, base: Path | None = None) -> Path:
    return system_root(base=base) / "index.md"


# ---------------------------------------------------------------------------
# Tenant tier paths (Tier 1)
# ---------------------------------------------------------------------------


def tenant_index_path(tenant_slug: str, *, base: Path | None = None) -> Path:
    return tenant_root(tenant_slug, base=base) / "index.md"


def tenant_article_path(
    tenant_slug: str, article_id: str, *, base: Path | None = None,
) -> Path:
    return tenant_root(tenant_slug, base=base) / "articles" / f"{article_id}.md"


def tenant_theme_path(
    tenant_slug: str, theme: str, *, base: Path | None = None,
) -> Path:
    return tenant_root(tenant_slug, base=base) / "themes" / f"{slugify(theme) or 'unknown'}.md"


def tenant_relations_path(tenant_slug: str, *, base: Path | None = None) -> Path:
    return tenant_root(tenant_slug, base=base) / "relations.md"


def tenant_belief_path(tenant_slug: str, *, base: Path | None = None) -> Path:
    return tenant_root(tenant_slug, base=base) / "beliefs.md"


def tenant_log_path(tenant_slug: str, *, base: Path | None = None) -> Path:
    return tenant_root(tenant_slug, base=base) / "log.md"


# ---------------------------------------------------------------------------
# User tier paths (Tier 2)
# ---------------------------------------------------------------------------


def user_index_path(user_id: str, *, base: Path | None = None) -> Path:
    return user_root(user_id, base=base) / "index.md"


def user_painpoints_path(user_id: str, *, base: Path | None = None) -> Path:
    return user_root(user_id, base=base) / "painpoints.md"


def user_history_path(user_id: str, *, base: Path | None = None) -> Path:
    return user_root(user_id, base=base) / "history.md"


def user_saved_path(user_id: str, *, base: Path | None = None) -> Path:
    return user_root(user_id, base=base) / "saved.md"


def user_theme_path(
    user_id: str, theme: str, *, base: Path | None = None,
) -> Path:
    return user_root(user_id, base=base) / "themes" / f"{slugify(theme) or 'unknown'}.md"


def user_log_path(user_id: str, *, base: Path | None = None) -> Path:
    return user_root(user_id, base=base) / "log.md"


# ---------------------------------------------------------------------------
# Cross-tier relative links (for markdown content)
# ---------------------------------------------------------------------------


def relative_link(src: Path, dst: Path) -> str:
    """Compute a markdown-friendly relative path from src to dst.

    Always returns POSIX separators ('/') so the resulting link works
    when rendered on Windows + Linux + macOS + GitHub.
    """
    # `relpath` walks up to a common ancestor, then down to dst
    rel = Path(*dst.parts).resolve() if False else dst
    try:
        from os.path import relpath
        rel_str = relpath(str(dst), start=str(src.parent))
    except ValueError:
        # Different drives on Windows — fall back to absolute
        rel_str = str(dst)
    return rel_str.replace("\\", "/")
