"""Embedding service — generate pgvector embeddings for semantic search.

Per MASTER_BUILD_PLAN Phase 10:
- pgvector embeddings for all extracted content
- Semantic search across all tenant media
"""

import structlog

from backend.core.config import settings

logger = structlog.get_logger()


async def generate_embedding(text: str) -> list[float] | None:
    """Generate an embedding vector for text using Claude/OpenAI.

    Tries Anthropic first (via Voyage AI), falls back to OpenAI ada-002.
    Returns None if no API key is configured.
    """
    if not text.strip():
        return None

    # Truncate to ~8000 chars to stay within token limits
    text = text[:8000]

    # Try OpenAI embeddings (most common for pgvector)
    if settings.OPENAI_API_KEY:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                    json={
                        "model": "text-embedding-3-small",
                        "input": text,
                    },
                )
                response.raise_for_status()
                data = response.json()
                return data["data"][0]["embedding"]
        except Exception as e:
            logger.warning("openai_embedding_failed", error=str(e))

    # Fallback: no embeddings available
    logger.warning("no_embedding_api_configured")
    return None


async def generate_embeddings_batch(texts: list[str]) -> list[list[float] | None]:
    """Generate embeddings for a batch of texts.

    Uses batch API when available for efficiency.
    """
    if not texts:
        return []

    if settings.OPENAI_API_KEY:
        try:
            import httpx
            # OpenAI supports batch embeddings
            truncated = [t[:8000] for t in texts if t.strip()]
            if not truncated:
                return [None] * len(texts)

            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                    json={
                        "model": "text-embedding-3-small",
                        "input": truncated,
                    },
                )
                response.raise_for_status()
                data = response.json()
                embeddings = [item["embedding"] for item in data["data"]]

                # Map back to original indices (Nones for empty texts)
                result = []
                embed_idx = 0
                for t in texts:
                    if t.strip():
                        result.append(embeddings[embed_idx] if embed_idx < len(embeddings) else None)
                        embed_idx += 1
                    else:
                        result.append(None)
                return result
        except Exception as e:
            logger.warning("openai_batch_embedding_failed", error=str(e))

    return [None] * len(texts)


async def semantic_search(
    tenant_id: str,
    query: str,
    limit: int = 10,
    db=None,
) -> list[dict]:
    """Semantic search across media chunks using pgvector cosine similarity.

    Requires a running PostgreSQL with pgvector extension and populated embeddings.
    """
    if not db:
        return []

    query_embedding = await generate_embedding(query)
    if not query_embedding:
        return []

    try:
        from sqlalchemy import text as sql_text

        # pgvector cosine distance query
        # Note: requires pgvector extension and embedding column to be vector type
        result = await db.execute(
            sql_text("""
                SELECT mc.id, mc.content, mc.media_file_id, mc.page_number,
                       mf.filename, mf.original_filename,
                       1 - (mc.embedding <=> :query_vec::vector) as similarity
                FROM media_chunks mc
                JOIN media_files mf ON mc.media_file_id = mf.id
                WHERE mc.tenant_id = :tenant_id
                  AND mc.embedding IS NOT NULL
                ORDER BY mc.embedding <=> :query_vec::vector
                LIMIT :limit
            """),
            {
                "tenant_id": tenant_id,
                "query_vec": str(query_embedding),
                "limit": limit,
            },
        )
        rows = result.fetchall()

        return [
            {
                "chunk_id": row.id,
                "content": row.content,
                "media_file_id": row.media_file_id,
                "filename": row.original_filename,
                "page_number": row.page_number,
                "similarity": round(float(row.similarity), 4),
            }
            for row in rows
        ]
    except Exception as e:
        logger.warning("semantic_search_failed", error=str(e))
        return []
