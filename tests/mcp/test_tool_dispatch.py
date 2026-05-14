"""Phase C — MCP tool adapter dispatch tests.

Walks the real adapter handlers (no stubs) on a tmp data dir to
prove every tool can be invoked end-to-end with empty / minimal
state and returns a well-shaped payload. Audit-trigger gate +
verbatim sign-off enforcement is covered separately in
`test_chat_integration.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

from engine.mcp.server import build_server


def _seed_minimal_data(base_data: Path) -> None:
    """Lay down the directory skeleton the handlers expect."""
    (base_data / "audit").mkdir(parents=True, exist_ok=True)
    (base_data / "autoresearcher" / "system").mkdir(parents=True, exist_ok=True)
    (base_data / "outputs" / "adani-power" / "insights").mkdir(parents=True, exist_ok=True)
    # 1 ledger entry so leaderboard has something to consider
    ledger = base_data / "autoresearcher" / "system" / "experiments.jsonl"
    ledger.write_text(
        json.dumps({
            "experiment_id": "exp-1",
            "ts": "2025-01-01T00:00:00Z",
            "decision": "keep",
            "metric_delta": 0.03,
            "tier": "system",
        }) + "\n",
        encoding="utf-8",
    )


def test_wiki_search_handles_missing_wiki_root(tmp_path):
    _seed_minimal_data(tmp_path)
    handle = build_server(data_dir=tmp_path)
    out = handle.invoke("wiki-search", {"q": "water"})
    assert out["ok"] is True
    # Returns either empty hits or a wiki_root_missing flag depending on layout
    body = out["result"]
    assert "hits" in body


def test_autoresearcher_leaderboard_returns_keeps(tmp_path):
    _seed_minimal_data(tmp_path)
    handle = build_server(data_dir=tmp_path)
    out = handle.invoke("autoresearcher-leaderboard", {"tier": "system", "limit": 5})
    assert out["ok"] is True
    rows = out["result"]["leaderboard"]
    assert any(r.get("decision") == "keep" for r in rows)


def test_autoresearcher_experiments_returns_recent(tmp_path):
    _seed_minimal_data(tmp_path)
    handle = build_server(data_dir=tmp_path)
    out = handle.invoke("autoresearcher-experiments", {"tier": "system", "limit": 10})
    assert out["ok"] is True
    assert out["result"]["total_seen"] >= 1


def test_article_list_empty_state(tmp_path, monkeypatch):
    _seed_minimal_data(tmp_path)
    # `query_feed` reads the global SQLite DB; we don't have a live one
    # in the test env, so just confirm the call shape doesn't crash on
    # a slug we don't have data for.
    monkeypatch.setenv("OPENAI_DISABLED", "1")
    handle = build_server(data_dir=tmp_path)
    out = handle.invoke("article-list", {"tenant": "adani-power", "limit": 5})
    assert out["ok"] is True
    assert "articles" in out["result"]


def test_agent_state_get_initial_state(tmp_path):
    _seed_minimal_data(tmp_path)
    handle = build_server(data_dir=tmp_path)
    out = handle.invoke("agent-state-get", {"tenant": "icici-bank"})
    assert out["ok"] is True
    # Fresh agent → StageInitializing (matches load_from_disk semantics)
    assert out["result"]["state"] == "StageInitializing"


def test_agent_beliefs_get_returns_empty_for_unknown_tenant(tmp_path):
    _seed_minimal_data(tmp_path)
    handle = build_server(data_dir=tmp_path)
    out = handle.invoke("agent-beliefs-get", {"tenant": "no-such-tenant"})
    assert out["ok"] is True
    assert out["result"]["beliefs"] == []


def test_advisor_queue_empty_when_no_events(tmp_path):
    _seed_minimal_data(tmp_path)
    handle = build_server(data_dir=tmp_path)
    out = handle.invoke("advisor-queue", {})
    assert out["ok"] is True
    assert out["result"]["count"] == 0


def test_advisor_resolve_unknown_event(tmp_path):
    _seed_minimal_data(tmp_path)
    handle = build_server(data_dir=tmp_path)
    out = handle.invoke("advisor-resolve", {
        "event_id": "nonexistent",
        "resolution": "approve",
        "rationale": "test",
    })
    assert out["ok"] is True  # tool ran; payload signals semantic failure
    assert out["result"]["ok"] is False
    assert "no advisor event" in out["result"]["error"]


def test_advisor_resolve_validation_rejects_bad_resolution(tmp_path):
    _seed_minimal_data(tmp_path)
    handle = build_server(data_dir=tmp_path)
    # Pydantic regex pattern rejects anything outside approve/reject
    out = handle.invoke("advisor-resolve", {
        "event_id": "x",
        "resolution": "defer",   # ← invalid
        "rationale": "",
    })
    assert out["ok"] is False
    assert out["error"]["code"] == "input_validation_error"
