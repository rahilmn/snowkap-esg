"""Phase C — Multi-layer hint suppression.

Five layers (we simplified Base Version's 6-layer to remove
A/B-test-cohort gating, which isn't shipping in v1):

  1. **Dedup** — same `dedup_key` within `dedup_window` → drop.
  2. **Dismissal** — user explicitly dismissed this hint kind →
     suppress further hints of the same kind for `dismissal_cooldown`.
  3. **Per-kind cooldown** — same coach can only fire every
     `kind_cooldown` minutes.
  4. **Session volume cap** — at most `session_volume_cap` hints
     per (tenant, user) per session.
  5. **Global volume cap** — at most `global_volume_cap` hints
     server-wide per minute (cheap rate limit).

Layers run in order; the first that fires wins, and the engine
records `suppression_reason` so the UI can show a debug panel.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque


# Defaults — overrideable per AdvisorEngine instance
_DEFAULTS = {
    "dedup_window_s": 600,        # 10 min
    "dismissal_cooldown_s": 86_400,  # 24 h
    "kind_cooldown_s": 1_800,     # 30 min
    "session_volume_cap": 3,
    "global_volume_cap": 30,
    "global_window_s": 60,
}


@dataclass
class SuppressionState:
    """In-process state. NOT process-safe — single-worker only.

    For multi-worker deployment this needs to be backed by SQLite +
    a tiny `advisor_suppression` table. Today the chat process is
    expected to be single-worker (matches Base Version's stance).
    """
    last_seen_dedup_key: dict[str, float] = field(default_factory=dict)
    last_kind_fire: dict[str, float] = field(default_factory=dict)
    dismissed_kinds: dict[tuple[str, str], float] = field(default_factory=dict)
    session_count: dict[tuple[str, str], int] = field(default_factory=dict)
    global_fires: Deque[float] = field(default_factory=deque)

    def dismiss(self, kind: str, tenant: str | None, user: str | None) -> None:
        key = (kind, f"{tenant}:{user}")
        self.dismissed_kinds[key] = time.time()

    def reset_session(self, tenant: str, user: str) -> None:
        self.session_count.pop((tenant, user), None)


def evaluate_suppression(
    state: SuppressionState,
    *,
    kind: str,
    dedup_key: str,
    tenant: str | None,
    user: str | None,
    config: dict | None = None,
) -> str | None:
    """Return a suppression reason string (e.g. "dedup", "dismissal",
    "kind_cooldown") if the hint should be suppressed, else None.

    Mutates `state` on PASS (records the fire); does NOT mutate on
    suppression — so a hint suppressed at layer 3 doesn't bump the
    session counter at layer 4.
    """
    cfg = {**_DEFAULTS, **(config or {})}
    now = time.time()
    tu = (tenant or "_", user or "_")

    # 1. Dedup
    last_seen = state.last_seen_dedup_key.get(dedup_key)
    if last_seen and (now - last_seen) < cfg["dedup_window_s"]:
        return "dedup"

    # 2. Dismissal
    dismiss_key = (kind, f"{tenant}:{user}")
    dismissed_at = state.dismissed_kinds.get(dismiss_key)
    if dismissed_at and (now - dismissed_at) < cfg["dismissal_cooldown_s"]:
        return "dismissal"

    # 3. Per-kind cooldown
    last_fire = state.last_kind_fire.get(kind)
    if last_fire and (now - last_fire) < cfg["kind_cooldown_s"]:
        return "kind_cooldown"

    # 4. Session volume cap
    count = state.session_count.get(tu, 0)
    if count >= cfg["session_volume_cap"]:
        return "session_volume_cap"

    # 5. Global volume cap (sliding window)
    cutoff = now - cfg["global_window_s"]
    while state.global_fires and state.global_fires[0] < cutoff:
        state.global_fires.popleft()
    if len(state.global_fires) >= cfg["global_volume_cap"]:
        return "global_volume_cap"

    # PASS — record the fire
    state.last_seen_dedup_key[dedup_key] = now
    state.last_kind_fire[kind] = now
    state.session_count[tu] = count + 1
    state.global_fires.append(now)
    return None
