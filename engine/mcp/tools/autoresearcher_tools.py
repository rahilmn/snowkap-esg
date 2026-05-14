"""MCP adapters for autoresearcher-experiments / autoresearcher-leaderboard."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.autoresearcher.ledger import leaderboard, read_ledger


def handle_autoresearcher_experiments(payload: dict[str, Any], base_data: Path) -> dict[str, Any]:
    tier = payload.get("tier", "system")
    limit = payload.get("limit", 20)
    rows = list(read_ledger(tier, base_data_dir=base_data))
    rows.sort(key=lambda r: r.get("ts") or "", reverse=True)
    return {"tier": tier, "total_seen": len(rows), "rows": rows[:limit]}


def handle_autoresearcher_leaderboard(payload: dict[str, Any], base_data: Path) -> dict[str, Any]:
    tier = payload.get("tier", "system")
    top_n = payload.get("limit", 20)
    rows = leaderboard(tier, top_n=top_n, base_data_dir=base_data)
    return {"tier": tier, "leaderboard": rows, "count": len(rows)}
