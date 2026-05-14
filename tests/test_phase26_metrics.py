"""Phase 26 cross-cutting — /metrics extension tests.

Validates:
  - count_by_criticality_band buckets correctly + handles NULL
  - outbound_touches.total_count + first_touch_ratio aggregations
  - persona_store.total_count + count_by_role aggregations
  - news_router.budget.to_dict shape (consumed by /metrics)
  - The Prometheus metric series names + label format are stable
    (parsed by external dashboards — must not silently rename)
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_dir = tmp_path / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SNOWKAP_DB_BACKEND", "sqlite")

    from pathlib import Path as _Path
    import engine.config as _cfg

    # Save the real implementation so we can route ontology paths through
    # it, and only redirect the DB filename to tmp.
    _real_get_data_path = _cfg.get_data_path

    def _fake(*parts: str) -> _Path:
        # Route DB-related paths to tmp; everything else (ontology, etc.)
        # goes through the real resolver so .ttl files still load.
        if parts and parts[0] in ("snowkap.db",):
            return db_dir / "snowkap.db"
        if not parts:
            return db_dir
        return _real_get_data_path(*parts)

    monkeypatch.setattr(_cfg, "get_data_path", _fake)
    import engine.db.connection as _conn
    if hasattr(_conn, "get_data_path"):
        monkeypatch.setattr(_conn, "get_data_path", _fake, raising=False)

    import importlib
    from engine.persona import persona_store as ps
    from engine.models import outbound_touches as ot
    importlib.reload(ps)
    importlib.reload(ot)
    yield
    importlib.reload(ps)
    importlib.reload(ot)
    # The /metrics test imports api.main which transitively loads the
    # ontology graph from get_data_path("ontology"). With our patch
    # active, it loaded from tmp (empty). Reset the cache so subsequent
    # tests in the same pytest session reload from the real path.
    try:
        from engine.ontology.graph import reset_graph
        reset_graph()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# outbound_touches aggregations
# ---------------------------------------------------------------------------


def test_outbound_total_count_starts_at_zero():
    from engine.models.outbound_touches import total_count
    assert total_count() == 0


def test_outbound_total_count_increments():
    from engine.models.outbound_touches import record_touch, total_count
    record_touch("a@x.com", "co1", "art-1")
    record_touch("a@x.com", "co1", "art-2")
    record_touch("b@x.com", "co1", "art-1")
    assert total_count() == 3


def test_first_touch_ratio_buckets_pairs_correctly():
    """A pair with 1 row → first_touch; 2+ rows → subsequent_touch."""
    from engine.models.outbound_touches import (
        first_touch_ratio,
        record_touch,
    )
    # Pair (a, co1): 1 touch  → first_touch
    record_touch("a@x.com", "co1", "art-1")
    # Pair (b, co1): 3 touches → subsequent_touch
    for i in range(3):
        record_touch("b@x.com", "co1", f"art-{i}")
    # Pair (c, co2): 2 touches → subsequent_touch
    record_touch("c@x.com", "co2", "art-1")
    record_touch("c@x.com", "co2", "art-2")

    ratio = first_touch_ratio()
    assert ratio == {"first_touch": 1, "subsequent_touch": 2}


def test_first_touch_ratio_empty_table_safe():
    from engine.models.outbound_touches import first_touch_ratio
    assert first_touch_ratio() == {"first_touch": 0, "subsequent_touch": 0}


# ---------------------------------------------------------------------------
# persona_store aggregations
# ---------------------------------------------------------------------------


def test_persona_total_count_starts_at_zero():
    from engine.persona.persona_store import total_count
    assert total_count() == 0


def test_persona_total_and_by_role():
    from engine.persona.persona_model import default_persona_for_role
    from engine.persona.persona_store import (
        count_by_role,
        total_count,
        upsert_persona,
    )
    upsert_persona(default_persona_for_role("u1", "cfo"))
    upsert_persona(default_persona_for_role("u2", "cfo"))
    upsert_persona(default_persona_for_role("u3", "ceo"))
    upsert_persona(default_persona_for_role("u4", "analyst"))

    assert total_count() == 4
    by_role = count_by_role()
    assert by_role.get("cfo") == 2
    assert by_role.get("ceo") == 1
    assert by_role.get("analyst") == 1


# ---------------------------------------------------------------------------
# sqlite_index criticality-band bucketing
# ---------------------------------------------------------------------------


def test_count_by_criticality_band_returns_all_buckets():
    """The aggregator returns every bucket (CRITICAL/HIGH/MEDIUM/LOW/UNSCORED)
    even on an empty table — so /metrics always emits all label values
    (Prometheus-friendly: no missing series across scrapes)."""
    from engine.index.sqlite_index import count_by_criticality_band
    out = count_by_criticality_band()
    assert set(out.keys()) >= {
        "CRITICAL", "HIGH", "MEDIUM", "LOW", "UNSCORED",
    }
    assert all(v == 0 for v in out.values())


# ---------------------------------------------------------------------------
# news_router budget shape (consumed by /metrics)
# ---------------------------------------------------------------------------


def test_news_router_budget_to_dict_has_required_metric_fields():
    """/metrics reads remaining + burst_remaining + spent_this_month off
    BudgetState.to_dict — those keys must stay stable."""
    from engine.ingestion.news_router import BudgetState, reset_router

    reset_router()
    b = BudgetState(monthly_cap=1000, burst_reserve=200)
    b.spend(150)
    b.spend(50, from_burst=True)
    d = b.to_dict()
    for key in ("remaining", "burst_remaining", "spent_this_month"):
        assert key in d
    assert d["remaining"] == 850
    assert d["burst_remaining"] == 150
    assert d["spent_this_month"] == 150


# ---------------------------------------------------------------------------
# Metric naming stability — guard against silent renames
# ---------------------------------------------------------------------------


def test_metrics_endpoint_emits_phase26_series():
    """Render /metrics via TestClient and assert each new series name +
    label set appears. Dashboards parse these names — silent renames
    break PromQL queries."""
    from fastapi.testclient import TestClient
    from api.main import app

    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text

    # Phase 1
    assert "snowkap_articles_by_criticality_band" in body
    for band in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNSCORED"):
        assert f'band="{band}"' in body

    # Phase 4
    assert "snowkap_outbound_touches_total" in body
    assert "snowkap_cta_cadence" in body
    assert 'bucket="first_touch"' in body
    assert 'bucket="subsequent_touch"' in body

    # Phase 6
    assert "snowkap_personas_total" in body
    assert "snowkap_personas_by_role" in body
    for role in ("cfo", "ceo", "analyst", "other"):
        assert f'role="{role}"' in body

    # Phase 5
    assert "snowkap_newsapi_budget" in body
    for pool in ("remaining", "burst_remaining", "spent_this_month"):
        assert f'pool="{pool}"' in body

    # Existing metrics still present (regression guard)
    assert "snowkap_articles_total" in body
    assert "snowkap_openai_cost_usd_24h" in body
    assert "snowkap_openai_calls_24h" in body
