"""Re-run the pipeline on every cached input article so existing JSON outputs
pick up Phase 13 fixes (stakeholders, SDGs, risk_lite, theme causal chains).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.config import get_company  # noqa: E402
from engine.main import _run_article  # noqa: E402

INPUTS = ROOT / "data" / "inputs" / "news"


def main() -> int:
    if not INPUTS.exists():
        print(f"no input dir: {INPUTS}")
        return 1

    total_ok = 0
    total_err = 0
    started = time.perf_counter()

    for slug_dir in sorted(INPUTS.iterdir()):
        if not slug_dir.is_dir():
            continue
        slug = slug_dir.name
        try:
            company = get_company(slug)
        except Exception as exc:
            print(f"[skip] {slug}: {exc}")
            continue

        files = sorted(slug_dir.glob("*.json"))
        print(f"\n=== {slug} ({len(files)} cached articles) ===")

        for path in files:
            try:
                article = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"  [parse-err] {path.name}: {exc}")
                total_err += 1
                continue

            try:
                summary = _run_article(article, company)
                tag = "REJ" if summary.rejected else summary.tier
                print(
                    f"  [{tag}] {path.name} "
                    f"q={summary.ontology_queries} t={summary.elapsed_seconds}s"
                )
                total_ok += 1
            except Exception as exc:
                print(f"  [err] {path.name}: {type(exc).__name__}: {exc}")
                total_err += 1

    elapsed = round(time.perf_counter() - started, 1)
    print(f"\nDone: ok={total_ok} err={total_err} elapsed={elapsed}s")
    return 0 if total_err == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
