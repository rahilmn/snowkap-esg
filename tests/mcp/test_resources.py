"""Phase C — MCP resources (read-only asset surface) tests."""
from __future__ import annotations

from pathlib import Path

from engine.mcp.resources import list_default_resources, read_default_resource


def _seed_resources(base_data: Path) -> None:
    """Lay down a minimal set of files so the listing has something to find."""
    (base_data / "ontology").mkdir(parents=True, exist_ok=True)
    (base_data / "audit").mkdir(parents=True, exist_ok=True)
    (base_data / "autoresearcher" / "system").mkdir(parents=True, exist_ok=True)
    (base_data / "ontology" / "tiny.ttl").write_text(
        "@prefix s: <http://snowkap.com/> .\ns:x s:y \"z\" .\n", encoding="utf-8"
    )
    (base_data / "audit" / "decision_log.jsonl").write_text(
        '{"ts":"2025-01-01","decision_type":"test"}\n', encoding="utf-8"
    )
    (base_data / "autoresearcher" / "system" / "experiments.jsonl").write_text(
        '{"experiment_id":"e1"}\n', encoding="utf-8"
    )


def test_list_resources_discovers_ttl_and_audit(tmp_path):
    _seed_resources(tmp_path)
    resources = list(list_default_resources(tmp_path))
    uris = {r.uri for r in resources}
    assert "snowkap://ontology/tiny.ttl" in uris
    assert "snowkap://audit/decision_log.jsonl" in uris
    assert "snowkap://autoresearcher/system/ledger" in uris


def test_read_ontology_resource_returns_ttl(tmp_path):
    _seed_resources(tmp_path)
    out = read_default_resource("snowkap://ontology/tiny.ttl", tmp_path)
    assert out is not None
    mime, text = out
    assert mime == "text/turtle"
    assert "snowkap" in text


def test_read_audit_resource_returns_tail(tmp_path):
    _seed_resources(tmp_path)
    out = read_default_resource("snowkap://audit/decision_log.jsonl", tmp_path)
    assert out is not None
    assert out[0] == "application/x-ndjson"
    assert "decision_type" in out[1]


def test_read_autoresearcher_ledger(tmp_path):
    _seed_resources(tmp_path)
    out = read_default_resource("snowkap://autoresearcher/system/ledger", tmp_path)
    assert out is not None
    assert "experiment_id" in out[1]


def test_read_missing_resource_returns_none(tmp_path):
    _seed_resources(tmp_path)
    assert read_default_resource("snowkap://ontology/nope.ttl", tmp_path) is None
    assert read_default_resource("not-snowkap://x", tmp_path) is None


def test_resource_text_is_capped(tmp_path):
    """A huge TTL should still return within 32 KB."""
    _seed_resources(tmp_path)
    big = tmp_path / "ontology" / "big.ttl"
    big.write_text("x" * 100_000, encoding="utf-8")
    out = read_default_resource("snowkap://ontology/big.ttl", tmp_path)
    assert out is not None
    assert len(out[1]) <= 32_768
