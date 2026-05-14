"""Tier-2 corpus — per-user history with click-affinity labels.

Reads `data/persona/<user_id>_clicks.json` (when present — managed by
`engine.persona`) for clicked / saved / shared signals. Each entry
becomes a CorpusArticle with a gold label of:
  - `CONFIRMED` when the user clicked AND the engine predicted HOME/HIGH
  - `OVER_STATED` when the user dismissed AND the engine predicted HIGH
  - `CONFIRMED` (true negative) when the user dismissed AND prediction was LOW
"""
from __future__ import annotations

import json
from pathlib import Path

from engine.autoresearcher.corpus import (
    CorpusArticle,
    _flatten_insight_basic,
    _parse_iso,
)


def load_user_history(
    *,
    user_id: str,
    repo_root: Path | None = None,
) -> list[dict]:
    """Load the user's recorded click history from disk.

    Tolerates missing files (fresh user, no clicks yet) by returning [].
    """
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
    safe = "".join(c if c.isalnum() else "_" for c in user_id)
    path = repo_root / "data" / "persona" / f"{safe}_clicks.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def load_user_corpus(
    *,
    user_id: str,
    holdout_fraction: float = 0.20,
    repo_root: Path | None = None,
) -> list[CorpusArticle]:
    """Build a CorpusArticle list from the user's recent click history.

    Empty when the user has no recorded actions. The held-out fraction
    is the most-recent N% of actions by timestamp.
    """
    history = load_user_history(user_id=user_id, repo_root=repo_root)
    if not history:
        return []

    # Each history entry is expected to be like:
    #   {ts, article_id, tenant_slug, action ('click'|'save'|'share'|'dismiss'),
    #    predicted_band, themes, title, url, published_at, raw_insight?}
    history.sort(key=lambda e: e.get("ts") or "")
    n_held = max(1, int(len(history) * holdout_fraction))
    held = history[-n_held:]

    out: list[CorpusArticle] = []
    for entry in held:
        action = (entry.get("action") or "").lower()
        predicted_band = entry.get("predicted_band") or ""
        # Derive gold label from action × predicted_band
        if action in ("click", "save", "share"):
            gold_band = "CONFIRMED"
            gold_advisor_verdict = "approve"
        elif action == "dismiss":
            gold_band = "OVER_STATED" if predicted_band.upper() in ("HIGH", "CRITICAL") else "CONFIRMED"
            gold_advisor_verdict = "reject"
        else:
            gold_band = "CONFIRMED"
            gold_advisor_verdict = "none"

        out.append(CorpusArticle(
            article_id=str(entry.get("article_id") or ""),
            tenant_slug=str(entry.get("tenant_slug") or ""),
            url=str(entry.get("url") or ""),
            title=str(entry.get("title") or ""),
            published_at=str(entry.get("published_at") or ""),
            predicted_tier=str(entry.get("predicted_tier") or ""),
            predicted_band=str(predicted_band),
            themes=list(entry.get("themes") or []),
            gold_tier_band=gold_band,
            gold_advisor_verdict=gold_advisor_verdict,
            gold_audit_clean=True,
            raw_insight=entry.get("raw_insight") or {},
        ))
    return out
