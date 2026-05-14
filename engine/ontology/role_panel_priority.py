"""W4d — Role panel priority lookups.

Returns the ordered list of panel IDs to render + the hidden-panel set per
role. Driven by the ``RolePanelPriority`` triples in
``data/ontology/knowledge_expansion.ttl``.

The query result is stamped onto every ``DeepInsight.role_panel_order`` so
the React frontend's ArticleDetailSheet can read
``insight.role_panel_order[role]`` and render only the relevant panels in
the right order — instead of always showing the same 14 static cards.

API:
  query_role_panel_priority(role) -> {"order": [...], "hidden": [...]}
  query_all_role_panel_priorities() -> {"cfo": {...}, "ceo": {...}, "analyst": {...}}
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)


_ROLE_TO_LENS = {
    "cfo": "lens_cfo",
    "ceo": "lens_ceo",
    "esg-analyst": "lens_esg_analyst",
    "esg_analyst": "lens_esg_analyst",
    "analyst": "lens_esg_analyst",
}


def _split_csv(s: str) -> list[str]:
    return [tok.strip() for tok in (s or "").split(",") if tok.strip()]


@lru_cache(maxsize=1)
def query_all_role_panel_priorities() -> dict[str, dict[str, list[str]]]:
    """Return ``{role: {"order": [...], "hidden": [...]}}`` for all 3 roles.

    Cached after first call — the ontology is process-stable. Tests that
    edit the TTL should call ``cache_clear()`` to force a re-read.
    """
    out: dict[str, dict[str, list[str]]] = {
        "cfo": {"order": [], "hidden": []},
        "ceo": {"order": [], "hidden": []},
        "esg-analyst": {"order": [], "hidden": []},
    }
    try:
        from engine.ontology.graph import get_graph
        graph = get_graph().graph
        sparql = """
            PREFIX snowkap: <http://snowkap.com/ontology/esg#>
            SELECT ?lens ?order ?hidden WHERE {
                ?priority a snowkap:RolePanelPriority ;
                          snowkap:forPerspective ?lens ;
                          snowkap:panelOrder ?order ;
                          snowkap:hiddenPanels ?hidden .
            }
        """
        for row in graph.query(sparql):
            lens_uri = str(row[0]).rsplit("#", 1)[-1]  # e.g. "lens_cfo"
            order_csv = str(row[1])
            hidden_csv = str(row[2])
            # Map back to canonical role keys
            for role_key, lens_name in _ROLE_TO_LENS.items():
                if lens_name == lens_uri and role_key in out:
                    out[role_key] = {
                        "order": _split_csv(order_csv),
                        "hidden": _split_csv(hidden_csv),
                    }
                    break
    except Exception as exc:  # noqa: BLE001 — additive layer, never fatal
        logger.warning("query_all_role_panel_priorities failed: %s", exc)

    return out


def query_role_panel_priority(role: str) -> dict[str, list[str]]:
    """Return ``{"order": [...], "hidden": [...]}`` for one role.

    Falls back to an empty dict on any error so the frontend can render
    a sensible default (all panels in legacy order).
    """
    role_norm = (role or "").strip().lower()
    canonical = _ROLE_TO_LENS.get(role_norm, role_norm)
    # Map back to dict key
    for k, v in _ROLE_TO_LENS.items():
        if v == canonical:
            role_norm = k
            break
    all_priorities = query_all_role_panel_priorities()
    return all_priorities.get(role_norm, {"order": [], "hidden": []})


def reset_cache() -> None:
    """Test hook — clear the lru_cache."""
    query_all_role_panel_priorities.cache_clear()
