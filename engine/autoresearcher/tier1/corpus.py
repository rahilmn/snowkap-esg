"""Tier-1 corpus — held-out articles filtered to one tenant.

Builds on the Tier-0 corpus loader but filters to articles
belonging to a single tenant_slug.
"""
from __future__ import annotations

from pathlib import Path

from engine.autoresearcher.corpus import CorpusArticle, load_held_out_corpus


def load_tenant_corpus(
    *,
    tenant_slug: str,
    min_age_days: int = 0,
    holdout_fraction: float = 0.20,
    repo_root: Path | None = None,
) -> list[CorpusArticle]:
    """Return the held-out corpus for one tenant."""
    full = load_held_out_corpus(
        min_age_days=min_age_days,
        holdout_fraction=holdout_fraction,
        repo_root=repo_root,
    )
    return [a for a in full if a.tenant_slug == tenant_slug]
