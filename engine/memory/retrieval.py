"""BM25-lite retrieval over tenant_memory.

Reuses the simple TF×IDF scorer pattern from `engine/wiki/index.py`
but scoped per-(tenant, user). Returns top-N memories ranked by
content match against a query string.

Embedding/pgvector retrieval is deferred (would need sqlite-vec).
"""
from __future__ import annotations

import math
import re
from typing import Any

from engine.memory.store import MemoryRecord, _touch_access, list_memories

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_K1 = 1.5
_B = 0.75


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _bm25_score(
    doc_tokens: list[str],
    query_terms: list[str],
    df: dict[str, int],
    n_docs: int,
    avgdl: float,
) -> float:
    if not doc_tokens or n_docs == 0:
        return 0.0
    doc_len = len(doc_tokens)
    tf: dict[str, int] = {}
    for t in doc_tokens:
        tf[t] = tf.get(t, 0) + 1
    score = 0.0
    for term in query_terms:
        f = tf.get(term, 0)
        if f == 0:
            continue
        d = df.get(term, 0)
        if d == 0:
            continue
        idf = math.log(1 + (n_docs - d + 0.5) / (d + 0.5))
        numerator = f * (_K1 + 1)
        denominator = f + _K1 * (1 - _B + _B * doc_len / (avgdl or 1.0))
        score += idf * (numerator / denominator)
    return score


def retrieve_for_injection(
    *,
    tenant_id: str,
    user_id: str | None,
    query: str,
    top_n: int = 8,
) -> list[MemoryRecord]:
    """Top-N memories most relevant to `query`, scoped to (tenant, user).

    Side effect: touches last_accessed + access_count on returned rows
    so frequent memories age slower.
    """
    query_terms = _tokenize(query)
    if not query_terms:
        return []

    candidates = list_memories(
        tenant_id=tenant_id, user_id=user_id,
        include_deactivated=False, limit=500,
    )
    if not candidates:
        return []

    # Build a tiny BM25 index in memory
    docs_tokens: list[list[str]] = [_tokenize(m.content) for m in candidates]
    n_docs = len(docs_tokens)
    avgdl = sum(len(d) for d in docs_tokens) / max(1, n_docs)
    df: dict[str, int] = {}
    for tokens in docs_tokens:
        for t in set(tokens):
            df[t] = df.get(t, 0) + 1

    scored = [
        (i, _bm25_score(docs_tokens[i], query_terms, df, n_docs, avgdl))
        for i in range(n_docs)
    ]
    scored = [s for s in scored if s[1] > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    top_idx = [i for i, _ in scored[:top_n]]
    top_records = [candidates[i] for i in top_idx]
    _touch_access([m.memory_id for m in top_records])
    return top_records
