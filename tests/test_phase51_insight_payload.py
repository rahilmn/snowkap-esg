"""Phase 51.B — durable insight_payload store + DB-fallback read.

The full insight detail payload was persisted ONLY to the ephemeral
container filesystem; on Railway a restart wiped it and `insight_detail`
returned HTTP 202 "regenerating" — a billable LLM re-run. The writer now
mirrors the payload into Postgres and `insight_detail` falls back to that
mirror when the disk file is gone.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
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


# ---------------------------------------------------------------------------
# Phase 51.B (cont.) — WRITE-side resilience. A read-only / non-writable data
# dir (Railway without a mounted volume) raised PermissionError mid-onboard and
# mid-weekly-refresh, because the on-disk JSON write was fatal and ran BEFORE
# the Postgres mirror. Disk writes are now best-effort; Postgres is the durable
# source of truth, so onboarding + the weekly deck refresh survive a read-only
# filesystem.
# ---------------------------------------------------------------------------
def _readonly_mkdir(self, *a, **k):  # noqa: ANN001, ARG001
    raise PermissionError(13, "Permission denied", str(self))


def _stub_result(article_id: str = "art-ro", slug: str = "test-co"):
    return SimpleNamespace(
        article_id=article_id, title="Read-only test",
        url="https://example.com/ro", source="Reuters",
        published_at="2026-05-10T00:00:00Z", company_slug=slug, image_url="",
        rejected=False,
        to_dict=lambda: {"article_id": article_id, "frameworks": [], "causal_chains": []},
        risk=None, frameworks=[], causal_chains=[],
    )


def _stub_insight():
    return SimpleNamespace(to_dict=lambda: {
        "headline": "H", "decision_summary": {"materiality": "HIGH"},
        "event_polarity": "negative", "criticality": {"score": 0.7},
    })


def test_write_helper_survives_readonly_disk(tmp_path) -> None:
    """The shared _write helper logs + returns the *intended* path (file absent)
    instead of raising when the data dir can't be written — covers write_insight
    AND write_light_insight."""
    from engine.output.writer import _write
    target = tmp_path / "insights" / "x.json"
    with patch.object(Path, "mkdir", _readonly_mkdir):
        out = _write(target, {"a": 1})
    assert out == target     # intended path recorded …
    assert not out.exists()  # … but nothing was written to disk


def test_write_insight_survives_readonly_disk_and_mirrors_to_db(tmp_path) -> None:
    """End-to-end: a read-only data dir → write_insight does NOT raise and the
    full payload still lands in Postgres, so the deck + detail view work.

    Patches Path.write_text (the JSON file write) rather than Path.mkdir, because
    the SQLite mirror itself needs mkdir to create its data dir."""
    from engine.output import writer as writer_mod
    aid = "art-ro-mirror"

    def _readonly_write_text(self, *a, **k):  # noqa: ANN001, ARG001
        raise PermissionError(13, "Permission denied", str(self))

    with patch.object(writer_mod, "get_output_dir", return_value=tmp_path / "test-co"), \
         patch.object(Path, "write_text", _readonly_write_text):
        written = writer_mod.write_insight(
            result=_stub_result(aid), insight=_stub_insight(),
            perspectives={}, recommendations=None,
        )
    # Path recorded (json_path is NOT NULL) but the file was never written …
    assert written.insight is not None and not written.insight.exists()
    # … and the full payload is durable in the Postgres mirror.
    got = ip.get(aid)
    assert got is not None and got["article"]["id"] == aid


def test_news_fetcher_disk_writes_are_best_effort() -> None:
    """A read-only data dir must not break the news fetch: _write_article and
    _save_processed log + continue (the articles still flow to the pipeline +
    Postgres), so onboarding + the weekly refresh keep working."""
    from engine.ingestion import news_fetcher as nf
    art = nf.IngestedArticle(
        id="ro-1", title="T", content="c", summary="s", source="Reuters",
        url="https://e.com/ro1", published_at="2026-05-10T00:00:00Z",
        company_slug="test-co", source_type="newsapi_ai",
    )
    with patch.object(Path, "mkdir", _readonly_mkdir):
        assert nf._write_article(art) is None  # logged + skipped
        nf._save_processed({"deadbeef"})       # must not raise
