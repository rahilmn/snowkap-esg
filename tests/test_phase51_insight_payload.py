"""Phase 51.B — durable insight_payload store + DB-fallback read.

The full insight detail payload was persisted ONLY to the ephemeral
container filesystem; on Railway a restart wiped it and `insight_detail`
returned HTTP 202 "regenerating" — a billable LLM re-run. The writer now
mirrors the payload into Postgres and `insight_detail` falls back to that
mirror when the disk file is gone.
"""
from __future__ import annotations

from unittest.mock import patch

from starlette.testclient import TestClient

from api.main import app
from engine.models import insight_payload as ip


def _api_headers() -> dict:
    return {"X-API-Key": "test-api-key"}


def test_payload_upsert_get_roundtrip() -> None:
    payload = {
        "meta": {"schema_version": "3.3-editorial-lede"},
        "article": {"id": "p51-roundtrip", "title": "T"},
        "insight": {"analysis": {}},
    }
    ip.upsert("p51-roundtrip", "adani-power", payload)
    got = ip.get("p51-roundtrip")
    assert got is not None
    assert got["article"]["id"] == "p51-roundtrip"
    assert got["meta"]["schema_version"] == "3.3-editorial-lede"
    # Idempotent replace
    ip.upsert("p51-roundtrip", "adani-power", dict(payload, article={"id": "p51-roundtrip", "title": "T2"}))
    assert ip.get("p51-roundtrip")["article"]["title"] == "T2"


def test_payload_get_missing_returns_none() -> None:
    assert ip.get("p51-never-written") is None
    assert ip.get("") is None


def test_insight_detail_serves_from_db_when_disk_missing() -> None:
    """Durability win: disk file gone → the Postgres mirror serves 200,
    instead of a 202 regenerate that would burn an LLM call."""
    payload = {
        "meta": {"schema_version": "3.3-editorial-lede"},
        "article": {"id": "p51-mirror", "title": "Mirror"},
        "insight": {"analysis": {}},
        "perspectives": {},
    }
    ip.upsert("p51-mirror", "adani-power", payload)
    fake_row = {
        "id": "p51-mirror",
        "company_slug": "adani-power",
        "json_path": "/no/such/dir/p51-does-not-exist.json",
        "title": "test",
        "tier": "HOME",
    }
    with patch("api.routes.insights.get_by_id", return_value=fake_row):
        with TestClient(app) as client:
            r = client.get("/api/insights/p51-mirror", headers=_api_headers())
    assert r.status_code == 200, f"expected 200 from DB mirror, got {r.status_code}: {r.text[:200]}"
    assert r.json()["payload"]["article"]["id"] == "p51-mirror"


def test_insight_detail_202_when_disk_and_db_both_missing() -> None:
    """No disk file AND no DB row → the graceful 202 regenerating is preserved."""
    fake_row = {
        "id": "p51-nowhere",
        "company_slug": "adani-power",
        "json_path": "/no/such/dir/p51-nowhere.json",
        "title": "test",
        "tier": "HOME",
    }
    with patch("api.routes.insights.get_by_id", return_value=fake_row), \
         patch("api.routes.insights._trigger_background_regenerate"):
        with TestClient(app) as client:
            r = client.get("/api/insights/p51-nowhere", headers=_api_headers())
    assert r.status_code == 202, r.text
    assert r.json()["reason"] == "file_missing_on_disk"
