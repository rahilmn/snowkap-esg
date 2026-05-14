"""Tier-2 promoter — writes accepted persona-weight changes back to
the user's persona store.

Per-user isolated: a knob change for user A never affects user B.
Smallest blast radius of any tier — auto-commits without advisor
review because the impact is contained to one user's feed ranking.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engine.autoresearcher.ledger import ExperimentRecord
from engine.autoresearcher.knob_kinds.persona_weight import PersonaWeightState


def _persona_path(user_id: str, repo_root: Path | None = None) -> Path:
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
    safe = "".join(c if c.isalnum() else "_" for c in user_id)
    return repo_root / "data" / "persona" / f"{safe}_weights.json"


def write_persona_weights(
    *,
    user_id: str,
    state: PersonaWeightState,
    repo_root: Path | None = None,
) -> Path:
    """Persist this user's tuned affinity weights."""
    path = _persona_path(user_id, repo_root=repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    user_dict = {
        key: round(value, 6)
        for (uid, key), value in state.values.items()
        if uid == user_id
    }
    path.write_text(json.dumps(user_dict, indent=2, sort_keys=True), encoding="utf-8")
    return path


def promote_user_knob(
    *,
    record: ExperimentRecord,
    user_id: str,
    state: PersonaWeightState,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Persist the user's tuned weights after a kept experiment."""
    try:
        path = write_persona_weights(
            user_id=user_id, state=state, repo_root=repo_root,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"persistence failed: {exc}"}
    return {
        "ok": True,
        "user_id": user_id,
        "path": str(path),
        "n_keys": sum(1 for (uid, _) in state.values if uid == user_id),
    }
