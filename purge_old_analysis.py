"""Purge all pre-v2.2 analysis data from DB and Redis.

Clears deep_insight, rereact_recommendations for articles that don't have
_pipeline_version='2.2'. Also flushes Redis analysis cache keys.

Run: python purge_old_analysis.py
"""

import os
import sys
import json

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))


def main():
    from sqlalchemy import create_engine, text

    # Use sync DB URL
    db_url = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL", "")
    if not db_url:
        from backend.core.config import settings
        db_url = settings.DATABASE_URL_SYNC

    # Ensure sync driver
    if "asyncpg" in db_url:
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    print(f"Connecting to: {db_url[:50]}...")
    engine = create_engine(db_url)

    with engine.connect() as conn:
        # Count total articles
        total = conn.execute(text("SELECT COUNT(*) FROM articles")).scalar()
        print(f"Total articles: {total}")

        # Count articles with any deep_insight
        has_insight = conn.execute(text("SELECT COUNT(*) FROM articles WHERE deep_insight IS NOT NULL")).scalar()
        print(f"Articles with deep_insight: {has_insight}")

        # Count articles with v2.2 deep_insight
        v22 = conn.execute(text(
            "SELECT COUNT(*) FROM articles WHERE deep_insight IS NOT NULL "
            "AND deep_insight::text LIKE '%_pipeline_version%' "
            "AND deep_insight::text LIKE '%2.2%'"
        )).scalar()
        print(f"Articles with v2.2 deep_insight: {v22}")

        stale = has_insight - v22
        print(f"Stale articles to purge: {stale}")

        if stale == 0:
            print("Nothing to purge!")
            return

        # Purge old deep_insight and rereact_recommendations
        result = conn.execute(text("""
            UPDATE articles
            SET deep_insight = NULL,
                rereact_recommendations = NULL
            WHERE deep_insight IS NOT NULL
              AND (
                deep_insight::text NOT LIKE '%"_pipeline_version"%'
                OR deep_insight::text NOT LIKE '%"2.2"%'
              )
        """))
        conn.commit()
        print(f"Purged {result.rowcount} articles' deep_insight + recommendations")

        # Verify
        remaining = conn.execute(text(
            "SELECT COUNT(*) FROM articles WHERE deep_insight IS NOT NULL "
            "AND (deep_insight::text NOT LIKE '%_pipeline_version%' "
            "OR deep_insight::text NOT LIKE '%2.2%')"
        )).scalar()
        print(f"Remaining stale articles: {remaining}")

    # Flush Redis analysis cache
    try:
        import redis
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        r = redis.from_url(redis_url)
        # Delete all article_analysis keys
        keys = r.keys("*article_analysis*")
        if keys:
            r.delete(*keys)
            print(f"Flushed {len(keys)} Redis analysis cache keys")
        else:
            print("No Redis analysis cache keys found")

        # Also delete status keys
        status_keys = r.keys("*article_analysis_status*")
        if status_keys:
            r.delete(*status_keys)
            print(f"Flushed {len(status_keys)} Redis status keys")
    except Exception as e:
        print(f"Redis flush skipped: {e}")

    print("\nDone! All old analysis data purged.")
    print("Articles will regenerate with v2.2 pipeline when opened in the app.")


if __name__ == "__main__":
    main()
