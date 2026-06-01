"""Phase 48 — restore company decks to Postgres from on-disk insight JSONs.

ZERO LLM. Used when the Postgres company_article_view rows were cleared
(e.g. a reprocess run that failed mid-way on OpenRouter 402) but the
on-disk analysis at data/outputs/{slug}/insights/*.json is intact.

For each insight file: split_analysis + company_article_view.upsert with
the band from the insight. Mirrors writer._upsert_pool_and_view's deck
write but reads from disk instead of re-running the pipeline.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")
import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)5s %(message)s")
logger = logging.getLogger("restore")

_LLM = {"CRITICAL": "CRITICAL", "HIGH": "HIGH", "MODERATE": "MEDIUM", "MEDIUM": "MEDIUM", "LOW": "LOW"}
_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def main() -> int:
    from engine.db.connection import is_postgres
    if not is_postgres():
        logger.error("Postgres only."); return 2
    from engine.analysis.unified_analysis import split_analysis
    from engine.models import article_pool, company_article_view, onboarding_status
    from engine.config import get_company, load_companies
    from engine.output.writer import _upsert_pool_and_view

    out_root = _ROOT / "data/outputs"
    restored = {}
    for tenant_dir in sorted(out_root.iterdir()):
        if not tenant_dir.is_dir() or tenant_dir.name.startswith("_"):
            continue
        slug = tenant_dir.name
        ins_dir = tenant_dir / "insights"
        if not ins_dir.exists():
            continue
        files = sorted(ins_dir.glob("*.json"))
        n = 0
        for f in files:
            try:
                payload = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            insight = payload.get("insight") or {}
            analysis = insight.get("analysis") or {}
            if not analysis:
                continue
            # Reconstruct a minimal PipelineResult-like object for _upsert_pool_and_view.
            # Easiest: call _upsert_pool_and_view with a lightweight shim.
            article = payload.get("article") or {}
            class _R:  # minimal shim with the attrs _upsert_pool_and_view reads
                article_id = article.get("id") or insight.get("article_id") or ""
                url = article.get("url") or ""
                title = article.get("title") or insight.get("headline") or ""
                source = article.get("source") or ""
                published_at = article.get("published_at") or ""
                company_slug = slug
                image_url = article.get("image_url") or ""
            try:
                _upsert_pool_and_view(_R(), payload, analysis)
                n += 1
            except Exception as exc:
                logger.warning("  restore %s/%s failed: %s", slug, f.name, exc)
        if n:
            restored[slug] = n
            try:
                onboarding_status.mark_ready(slug, fetched=n, analysed=n, home_count=0,
                                             created_by_user="ci@snowkap.com")
            except Exception:
                pass
            logger.warning("restored %s: %d deck rows", slug, n)

    print("\n" + "=" * 50)
    print("  RESTORED DECKS")
    for slug, n in sorted(restored.items()):
        print(f"  {slug:24} {n}")
    print(f"  total companies: {len(restored)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
