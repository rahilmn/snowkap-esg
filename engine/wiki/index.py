"""W1.6 — Pure-Python BM25 search over the 3-tier wiki.

Zero external dependencies. Build the index once with
`WikiIndex.build(wiki_root)` and query with `idx.search("query terms",
tier="system|tenant|user", tenant_slug="adani-power")`.

The index is held in memory and rebuilt on demand; production should
cache it per request (~50ms for a 10k-page wiki).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# BM25 constants (Robertson + Walker defaults)
_K1 = 1.5
_B = 0.75

# Tokens are sequences of alphanumeric chars, lowercased
_TOKEN_RE = re.compile(r"[a-z0-9]+")

Tier = Literal["system", "tenant", "user", "unknown"]


def tokenize(text: str) -> list[str]:
    """Lowercase + alphanumeric token extraction. Punctuation drops."""
    return _TOKEN_RE.findall(text.lower())


def tier_of(path: Path, wiki_root: Path) -> Tier:
    """Classify a page by its tier based on path position under the wiki root."""
    try:
        rel = path.resolve().relative_to(wiki_root.resolve())
    except ValueError:
        return "unknown"
    parts = rel.parts
    if not parts:
        return "unknown"
    first = parts[0]
    if first == "system":
        return "system"
    if first == "tenants":
        return "tenant"
    if first == "users":
        return "user"
    return "unknown"


@dataclass
class SearchHit:
    path: Path
    score: float
    tier: Tier
    excerpt: str = ""


@dataclass
class _Doc:
    path: Path
    tier: Tier
    tenant_slug: str | None
    user_slug: str | None
    tokens: list[str]
    term_freq: dict[str, int]
    length: int


def _extract_tenant_or_user_slug(path: Path, wiki_root: Path) -> tuple[str | None, str | None]:
    """Pull tenant_slug / user_slug from the path, depending on tier."""
    try:
        rel = path.resolve().relative_to(wiki_root.resolve())
    except ValueError:
        return None, None
    parts = rel.parts
    if len(parts) >= 2 and parts[0] == "tenants":
        return parts[1], None
    if len(parts) >= 2 and parts[0] == "users":
        return None, parts[1]
    return None, None


class WikiIndex:
    def __init__(self, wiki_root: Path):
        self.wiki_root = wiki_root.resolve()
        self._docs: list[_Doc] = []
        self._df: dict[str, int] = {}  # document frequency per term
        self._avgdl: float = 0.0

    def __len__(self) -> int:
        return len(self._docs)

    @classmethod
    def build(cls, wiki_root: Path) -> "WikiIndex":
        idx = cls(wiki_root)
        total_length = 0
        for md_path in idx.wiki_root.rglob("*.md"):
            try:
                text = md_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            tokens = tokenize(text)
            tf: dict[str, int] = {}
            for tok in tokens:
                tf[tok] = tf.get(tok, 0) + 1
            tenant_slug, user_slug = _extract_tenant_or_user_slug(md_path, idx.wiki_root)
            doc = _Doc(
                path=md_path.resolve(),
                tier=tier_of(md_path, idx.wiki_root),
                tenant_slug=tenant_slug,
                user_slug=user_slug,
                tokens=tokens,
                term_freq=tf,
                length=len(tokens),
            )
            idx._docs.append(doc)
            total_length += doc.length
            # Update DF (once per term per doc)
            for tok in tf.keys():
                idx._df[tok] = idx._df.get(tok, 0) + 1
        idx._avgdl = (total_length / len(idx._docs)) if idx._docs else 0.0
        return idx

    def _score(self, doc: _Doc, query_terms: list[str]) -> float:
        """BM25 score for a single doc against the query terms."""
        if doc.length == 0:
            return 0.0
        N = len(self._docs) or 1
        score = 0.0
        for term in query_terms:
            df = self._df.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
            tf = doc.term_freq.get(term, 0)
            if tf == 0:
                continue
            numerator = tf * (_K1 + 1)
            denominator = tf + _K1 * (1 - _B + _B * doc.length / (self._avgdl or 1.0))
            score += idf * (numerator / denominator)
        return score

    def search(
        self,
        query: str,
        *,
        tier: Tier | None = None,
        tenant_slug: str | None = None,
        user_slug: str | None = None,
        top_k: int = 20,
    ) -> list[SearchHit]:
        """Score every (filtered) doc against the query; return top-k.

        Filters:
          - tier: 'system' | 'tenant' | 'user' (None = all tiers)
          - tenant_slug: when tier='tenant', further filters to one slug
          - user_slug: when tier='user', further filters to one user
        """
        query_terms = tokenize(query)
        if not query_terms:
            return []
        hits: list[SearchHit] = []
        for doc in self._docs:
            if tier and doc.tier != tier:
                continue
            if tier == "tenant" and tenant_slug and doc.tenant_slug != tenant_slug:
                continue
            if tier == "user" and user_slug and doc.user_slug != user_slug:
                continue
            s = self._score(doc, query_terms)
            if s <= 0:
                continue
            hits.append(SearchHit(path=doc.path, score=s, tier=doc.tier))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]
