"""Rebuild the SQLite index from JSON files in ``data/outputs/``.

Useful after:
- Cloning the repo on a new machine
- Manually editing JSON files
- Schema changes in the index

Usage::

    python -m engine.index.reindex
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.config import get_data_path, load_companies
from engine.index.sqlite_index import DB_PATH, ensure_schema, stats, upsert_article

logger = logging.getLogger(__name__)


def reindex_all() -> dict[str, int]:
    """Walk every company's insights/ folder and re-upsert into the index."""
    ensure_schema()
    summary: dict[str, int] = {}
    for company in load_companies():
        folder = get_data_path("outputs", company.slug, "insights")
        if not folder.exists():
            summary[company.slug] = 0
            continue
        count = 0
        for path in folder.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                upsert_article(payload, path)
                count += 1
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("reindex: failed to load %s: %s", path, exc)
        summary[company.slug] = count
    return summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print(f"Reindexing {DB_PATH}...")
    summary = reindex_all()
    print("\nPer-company reindex counts:")
    for slug, count in summary.items():
        print(f"  {slug}: {count}")
    print("\nIndex stats:")
    s = stats()
    print(f"  total: {s['total']}")
    print(f"  by_tier: {s['by_tier']}")
    print(f"  by_company: {s['by_company']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
