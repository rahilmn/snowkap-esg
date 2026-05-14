"""Phase C — `/api/memory` endpoint tests."""
from __future__ import annotations


def test_memory_crud_happy_path(client, headers):
    # Insert a memory
    resp = client.post(
        "/api/memory", headers=headers,
        json={
            "content": "User prefers CFO-lens responses",
            "scope": "personal",
            "fact_kind": "preference",
            "confidence": 0.9,
            "source_conversation_id": "test-cid",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    mem_id = body["memory"]["memory_id"]

    # List
    listing = client.get("/api/memory", headers=headers).json()
    assert listing["count"] >= 1
    assert any(m["memory_id"] == mem_id for m in listing["memories"])

    # Delete
    del_resp = client.delete(f"/api/memory/{mem_id}", headers=headers)
    assert del_resp.status_code == 204


def test_memory_rejects_bad_scope(client, headers):
    resp = client.post(
        "/api/memory", headers=headers,
        json={
            "content": "x", "scope": "global",     # invalid
            "fact_kind": "fact", "confidence": 0.5,
            "source_conversation_id": "test-cid",
        },
    )
    assert resp.status_code == 422


def test_memory_rejects_bad_fact_kind(client, headers):
    resp = client.post(
        "/api/memory", headers=headers,
        json={
            "content": "x", "scope": "personal",
            "fact_kind": "rumour",                 # invalid
            "confidence": 0.5, "source_conversation_id": "test-cid",
        },
    )
    assert resp.status_code == 422


def test_memory_delete_unknown_returns_404(client, headers):
    resp = client.delete("/api/memory/no-such-mem", headers=headers)
    assert resp.status_code == 404
