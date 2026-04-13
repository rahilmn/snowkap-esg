"""Phase 17b: Clear stale insight/recommendation/perspective data from all articles.

Keeps pipeline (stages 1-9) and article metadata intact.
Next time user clicks "View Insights", on-demand enrichment runs fresh
with primitive-enriched prompts.

Usage:
    python scripts/clear_stale_insights.py           # Dry run (report only)
    python scripts/clear_stale_insights.py --apply    # Actually clear
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUTPUTS = ROOT / "data" / "outputs"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Actually modify files (default: dry run)")
    args = parser.parse_args()

    if not OUTPUTS.exists():
        print(f"No outputs dir: {OUTPUTS}")
        return 1

    total = 0
    cleared = 0
    skipped = 0

    for slug_dir in sorted(OUTPUTS.iterdir()):
        if not slug_dir.is_dir():
            continue
        insights_dir = slug_dir / "insights"
        if not insights_dir.exists():
            continue

        for json_path in sorted(insights_dir.glob("*.json")):
            total += 1
            try:
                payload = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"  [parse-err] {json_path.name}: {exc}")
                continue

            insight = payload.get("insight")
            if not insight or not insight.get("headline"):
                skipped += 1
                continue

            if args.apply:
                # Null out stages 10-12 data, keep pipeline (stages 1-9) + article metadata
                payload["insight"] = None
                payload["recommendations"] = None
                payload["perspectives"] = {}
                json_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"  [cleared] {slug_dir.name}/{json_path.name}")
            else:
                title = payload.get("article", {}).get("title", "")[:60]
                print(f"  [would-clear] {slug_dir.name}/{json_path.name} — {title}")
            cleared += 1

    action = "Cleared" if args.apply else "Would clear"
    print(f"\n{action}: {cleared}/{total} articles ({skipped} already empty)")
    if not args.apply and cleared > 0:
        print("Run with --apply to execute.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
