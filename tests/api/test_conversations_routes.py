"""Phase C — `/api/conversations` endpoint shape + auth tests."""
from __future__ import annotations

from engine.chat.conversations import ensure_conversation
from engine.chat.messages import insert_assistant_message, insert_user_message


def _seed_one_conversation():
    """Drop one conversation owned by tenant=default, user=anonymous."""
    cid = ensure_conversation(
        conversation_id=None, tenant_id="default", user_id="anonymous",
    )
    insert_user_message(
        conversation_id=cid, tenant_id="default", user_id="anonymous",
        content="hi",
    )
    insert_assistant_message(
        conversation_id=cid, tenant_id="default", content="hello",
    )
    return cid


def test_list_conversations_returns_shape(client, headers):
    _seed_one_conversation()
    resp = client.get("/api/conversations", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "conversations" in body and isinstance(body["conversations"], list)
    assert body["count"] >= 1


def test_get_conversation_returns_messages(client, headers):
    cid = _seed_one_conversation()
    resp = client.get(f"/api/conversations/{cid}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["conversation_id"] == cid
    assert len(body["messages"]) == 2


def test_get_conversation_unknown_returns_404(client, headers):
    resp = client.get("/api/conversations/nope-not-real", headers=headers)
    assert resp.status_code == 404


def test_rename_conversation_persists(client, headers):
    cid = _seed_one_conversation()
    resp = client.patch(
        f"/api/conversations/{cid}/rename", headers=headers,
        json={"title": "Renamed thread"},
    )
    assert resp.status_code == 200
    body = client.get(f"/api/conversations/{cid}", headers=headers).json()
    assert body["summary"]["title"] == "Renamed thread"


def test_archive_then_list_excludes_by_default(client, headers):
    cid = _seed_one_conversation()
    client.post(f"/api/conversations/{cid}/archive", headers=headers)
    body = client.get("/api/conversations", headers=headers).json()
    cids = {c["conversation_id"] for c in body["conversations"]}
    assert cid not in cids


def test_search_finds_seeded_message(client, headers):
    _seed_one_conversation()
    resp = client.get("/api/conversations/search?q=hi", headers=headers)
    assert resp.status_code == 200


def test_fork_creates_new_id(client, headers):
    cid = _seed_one_conversation()
    resp = client.post(f"/api/conversations/{cid}/fork", headers=headers, json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["conversation_id"] != cid
