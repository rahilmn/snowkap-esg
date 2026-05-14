"""Wiki page materialiser for autoresearcher experiment history.

Renders three markdown pages under `wiki/system/autoresearcher/`:
  - experiments.md      — full ledger, newest-first
  - top-hits.md         — top-N kept experiments by metric_delta
  - discarded.md        — discarded experiments + reasons

So the system literally reads its own learning history when an
analyst (or LLM) browses the wiki.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from engine.autoresearcher.ledger import leaderboard, read_ledger
from engine.wiki.paths import system_root


@dataclass
class AutoresearcherPagesResult:
    pages_written: int = 0
    experiments_indexed: int = 0
    warnings: list[str] = field(default_factory=list)


def _frontmatter(d: dict[str, object]) -> str:
    lines = ["---"]
    for k in sorted(d.keys()):
        v = d[k]
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
        elif v is None:
            lines.append(f"{k}: null")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")


def _experiments_page(records: list[dict]) -> str:
    front = {
        "type": "autoresearcher_experiments",
        "count": len(records),
        "rebuilt_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    parts = [_frontmatter(front), "", "# Autoresearcher experiments", ""]
    parts.append(f"Total experiments recorded: **{len(records)}**")
    parts.append("")
    if not records:
        parts.append("_The ledger is empty. Run "
                     "`python scripts/run_autoresearcher.py --tier system --budget N`._")
        return "\n".join(parts).rstrip() + "\n"

    parts.append("| Time | Knob kind | Knob id | Δ metric | Decision |")
    parts.append("|---|---|---|---|---|")
    # Newest first (records are appended in chronological order)
    for r in reversed(records):
        parts.append(
            f"| {r.get('ts', '')} | {r.get('knob_kind', '')} | "
            f"`{r.get('knob_id', '')}` | "
            f"{(r.get('metric_delta') or 0):+.4f} | "
            f"{r.get('decision', '')} |"
        )
    parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _top_hits_page(top: list[dict]) -> str:
    front = {
        "type": "autoresearcher_top_hits",
        "count": len(top),
    }
    parts = [_frontmatter(front), "", "# Autoresearcher top hits", ""]
    parts.append(
        "The best-performing knob changes the autoresearcher has discovered. "
        "Each represents a tunable in the ontology / scorer whose adjustment "
        "improved the held-out calibration metric beyond the keep threshold."
    )
    parts.append("")
    if not top:
        parts.append("_No kept experiments yet._")
        return "\n".join(parts).rstrip() + "\n"

    parts.append("| Rank | Δ metric | Knob kind | Knob id | Rationale |")
    parts.append("|---|---|---|---|---|")
    for i, r in enumerate(top, start=1):
        parts.append(
            f"| {i} | {(r.get('metric_delta') or 0):+.4f} | "
            f"{r.get('knob_kind', '')} | `{r.get('knob_id', '')}` | "
            f"{(r.get('rationale') or '')[:60]} |"
        )
    parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _discarded_page(records: list[dict]) -> str:
    discards = [r for r in records if r.get("decision") == "discard"]
    front = {
        "type": "autoresearcher_discarded",
        "count": len(discards),
    }
    parts = [_frontmatter(front), "", "# Autoresearcher discarded experiments", ""]
    parts.append(
        "Discarded knob changes — failed the keep threshold. These are "
        "institutional 'we tried this, didn't work' memory."
    )
    parts.append("")
    if not discards:
        parts.append("_No discarded experiments yet._")
        return "\n".join(parts).rstrip() + "\n"

    parts.append("| Time | Knob kind | Knob id | Δ metric | Reason |")
    parts.append("|---|---|---|---|---|")
    for r in reversed(discards[-100:]):  # last 100 discards
        parts.append(
            f"| {r.get('ts', '')} | {r.get('knob_kind', '')} | "
            f"`{r.get('knob_id', '')}` | "
            f"{(r.get('metric_delta') or 0):+.4f} | "
            f"{(r.get('rationale') or '')[:60]} |"
        )
    parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def build_autoresearcher_pages(
    *,
    base: Path | None = None,
    base_data_dir: Path | None = None,
) -> AutoresearcherPagesResult:
    """Materialise the three autoresearcher pages under wiki/system/autoresearcher/.

    Args:
        base: optional repo-root override (passed to wiki/paths)
        base_data_dir: optional override for where the ledger JSONL lives
    """
    result = AutoresearcherPagesResult()
    root = system_root(base=base, mkdir=True) / "autoresearcher"
    root.mkdir(parents=True, exist_ok=True)

    records = list(read_ledger("system", base_data_dir=base_data_dir))
    result.experiments_indexed = len(records)

    _write(root / "experiments.md", _experiments_page(records))
    result.pages_written += 1

    top = leaderboard("system", top_n=20, base_data_dir=base_data_dir)
    _write(root / "top-hits.md", _top_hits_page(top))
    result.pages_written += 1

    _write(root / "discarded.md", _discarded_page(records))
    result.pages_written += 1

    return result
