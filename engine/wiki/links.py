"""W1.5 — Bidirectional backlinks + broken-link detection for the wiki.

Pure functions over the on-disk wiki tree. Used by:
  - the W1.8 API to surface "what links here" on every page
  - a CI test to catch broken cross-tier links before they ship

Only relative `.md` links are considered (the wiki uses them
exclusively for cross-tier navigation; external http URLs are not our
concern).
"""
from __future__ import annotations

import re
from pathlib import Path

# [label](path) — capture group 1 is the path. Excludes the leading `!`
# so image references don't get picked up.
_MD_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")


def scan_links_in_file(path: Path) -> list[str]:
    """Return the raw target strings of all markdown links in a file.

    Filters to .md targets only (the wiki only cares about markdown
    cross-references). External URLs are dropped.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    raw_targets = _MD_LINK_RE.findall(text)
    return [t for t in raw_targets if t.endswith(".md")]


def _resolve_target(source: Path, target_raw: str) -> Path:
    """Resolve a relative markdown link to an absolute path on disk."""
    # Strip any fragment (#anchor) or query (?q=...) before resolving
    target = target_raw.split("#", 1)[0].split("?", 1)[0]
    return (source.parent / target).resolve()


def compute_backlinks(root: Path) -> dict[Path, set[Path]]:
    """Walk the wiki tree and build {target: {sources}} mapping.

    Every `.md` file under `root` is scanned. For each markdown link
    inside, the resolved target gets the source added to its backlink
    set. Self-links (a file linking to itself) are dropped.
    """
    backlinks: dict[Path, set[Path]] = {}
    for src in root.rglob("*.md"):
        src_resolved = src.resolve()
        for raw in scan_links_in_file(src):
            target = _resolve_target(src, raw)
            if target == src_resolved:
                continue  # ignore self-links
            backlinks.setdefault(target, set()).add(src_resolved)
    return backlinks


def find_broken_links(root: Path) -> list[tuple[Path, Path]]:
    """Return (source, target) tuples for every `.md` link that points
    to a non-existent file.

    Used by CI to catch dangling cross-tier references before commit.
    """
    broken: list[tuple[Path, Path]] = []
    for src in root.rglob("*.md"):
        src_resolved = src.resolve()
        for raw in scan_links_in_file(src):
            target = _resolve_target(src, raw)
            if not target.exists():
                broken.append((src_resolved, target))
    return broken
