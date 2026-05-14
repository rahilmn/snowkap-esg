"""Chat persistence tests — conversations + messages + isolation + FTS5."""
from __future__ import annotations

import pytest

from engine.chat.schema import fts5_available

from engine.chat.conversations import (
    archive_conversation,
    delete_conversation,
    ensure_conversation,
    fork_conversation,
    get_conversation,
    list_conversations,
    rename_conversation,
    search_conversations,
)
from engine.chat.messages import (
    insert_assistant_message,
    insert_user_message,
    load_conversation_history,
    load_messages_for_llm,
)


# ---------------------------------------------------------------------------
# Conversation lifecycle
# ---------------------------------------------------------------------------


def test_ensure_conversation_creates_new_when_none():
    cid = ensure_conversation(
        conversation_id=None, tenant_id="t-alpha", user_id="alice",
    )
    assert cid
    summary = get_conversation(
        conversation_id=cid, tenant_id="t-alpha", user_id="alice",
    )
    assert summary is not None
    assert summary.tenant_id == "t-alpha"
    assert summary.user_id == "alice"
    assert summary.message_count == 0


def test_ensure_conversation_is_idempotent_for_owner():
    cid = ensure_conversation(
        conversation_id="conv-fixed-1", tenant_id="t-alpha", user_id="alice",
    )
    cid2 = ensure_conversation(
        conversation_id="conv-fixed-1", tenant_id="t-alpha", user_id="alice",
    )
    assert cid == cid2 == "conv-fixed-1"


def test_ensure_conversation_rejects_wrong_owner():
    ensure_conversation(
        conversation_id="conv-owned-by-alice", tenant_id="t-alpha", user_id="alice",
    )
    with pytest.raises(PermissionError):
        ensure_conversation(
            conversation_id="conv-owned-by-alice", tenant_id="t-alpha", user_id="bob",
        )


def test_list_conversations_filtered_to_user():
    ensure_conversation(conversation_id="list-1-alice", tenant_id="t-iso", user_id="alice")
    ensure_conversation(conversation_id="list-1-bob",   tenant_id="t-iso", user_id="bob")
    alice_convs = list_conversations(tenant_id="t-iso", user_id="alice")
    ids = [c.conversation_id for c in alice_convs]
    assert "list-1-alice" in ids
    assert "list-1-bob" not in ids


def test_list_conversations_excludes_archived_by_default():
    cid = ensure_conversation(
        conversation_id="archive-test", tenant_id="t-arch", user_id="charlie",
    )
    archive_conversation(conversation_id=cid, tenant_id="t-arch", user_id="charlie")
    default_list = list_conversations(tenant_id="t-arch", user_id="charlie")
    assert all(c.conversation_id != cid for c in default_list)
    with_archived = list_conversations(
        tenant_id="t-arch", user_id="charlie", include_archived=True,
    )
    assert any(c.conversation_id == cid for c in with_archived)


def test_rename_conversation():
    cid = ensure_conversation(
        conversation_id="rename-test", tenant_id="t-rn", user_id="dora",
    )
    ok = rename_conversation(
        conversation_id=cid, tenant_id="t-rn", user_id="dora", title="My renamed convo",
    )
    assert ok
    summary = get_conversation(conversation_id=cid, tenant_id="t-rn", user_id="dora")
    assert summary.title == "My renamed convo"


def test_delete_conversation_cascades_messages():
    cid = ensure_conversation(
        conversation_id="cascade-test", tenant_id="t-del", user_id="eve",
    )
    insert_user_message(
        tenant_id="t-del", conversation_id=cid, user_id="eve", content="hi",
    )
    ok = delete_conversation(conversation_id=cid, tenant_id="t-del", user_id="eve")
    assert ok
    msgs = load_conversation_history(
        conversation_id=cid, tenant_id="t-del", user_id="eve",
    )
    assert msgs == []


# ---------------------------------------------------------------------------
# Message insert + load
# ---------------------------------------------------------------------------


def test_insert_user_then_assistant_message():
    cid = ensure_conversation(
        conversation_id="msg-test-1", tenant_id="t-msg", user_id="alice",
    )
    user_msg = insert_user_message(
        tenant_id="t-msg", conversation_id=cid, user_id="alice", content="What is water risk?",
    )
    assistant_msg = insert_assistant_message(
        tenant_id="t-msg", conversation_id=cid,
        content="Water risk is the financial exposure...",
        model_used="gpt-4.1",
        usage={"prompt_tokens": 10, "completion_tokens": 20, "cost": 0.0008},
        finish_reason="stop",
    )
    assert user_msg.role == "user"
    assert assistant_msg.role == "assistant"
    assert assistant_msg.usage["cost"] == 0.0008

    msgs = load_conversation_history(
        conversation_id=cid, tenant_id="t-msg", user_id="alice",
    )
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[1].role == "assistant"


def test_insert_message_bumps_message_count():
    cid = ensure_conversation(
        conversation_id="count-test", tenant_id="t-cnt", user_id="alice",
    )
    summary_before = get_conversation(
        conversation_id=cid, tenant_id="t-cnt", user_id="alice",
    )
    assert summary_before.message_count == 0
    insert_user_message(
        tenant_id="t-cnt", conversation_id=cid, user_id="alice", content="x",
    )
    insert_assistant_message(
        tenant_id="t-cnt", conversation_id=cid, content="y",
    )
    summary_after = get_conversation(
        conversation_id=cid, tenant_id="t-cnt", user_id="alice",
    )
    assert summary_after.message_count == 2


def test_load_history_rejects_wrong_owner():
    cid = ensure_conversation(
        conversation_id="auth-test", tenant_id="t-auth", user_id="alice",
    )
    insert_user_message(
        tenant_id="t-auth", conversation_id=cid, user_id="alice", content="hi",
    )
    # bob can't read alice's conversation
    msgs = load_conversation_history(
        conversation_id=cid, tenant_id="t-auth", user_id="bob",
    )
    assert msgs == []


def test_load_messages_for_llm_returns_openai_shape():
    cid = ensure_conversation(
        conversation_id="llm-shape", tenant_id="t-llm", user_id="alice",
    )
    insert_user_message(
        tenant_id="t-llm", conversation_id=cid, user_id="alice", content="q1",
    )
    insert_assistant_message(
        tenant_id="t-llm", conversation_id=cid, content="a1",
    )
    msgs = load_messages_for_llm(
        conversation_id=cid, tenant_id="t-llm", user_id="alice",
    )
    assert msgs == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]


def test_insert_message_with_toulmin_and_tags_roundtrips():
    cid = ensure_conversation(
        conversation_id="tag-test", tenant_id="t-tag", user_id="alice",
    )
    insert_assistant_message(
        tenant_id="t-tag", conversation_id=cid, content="answer",
        toulmin={"claim": "x", "grounds": ["y"], "warrant": "z"},
        phase_k_tags={"scope": "tenant", "signal_type": "analyst_judgment",
                       "attribution": "chat_agent", "uncertainty": "moderate"},
    )
    msgs = load_conversation_history(
        conversation_id=cid, tenant_id="t-tag", user_id="alice",
    )
    assert msgs[0].toulmin["claim"] == "x"
    assert msgs[0].phase_k_tags["scope"] == "tenant"


def test_invalid_role_raises():
    cid = ensure_conversation(
        conversation_id="role-test", tenant_id="t-role", user_id="alice",
    )
    from engine.chat.messages import _insert_message
    with pytest.raises(ValueError, match="role"):
        _insert_message(
            tenant_id="t-role", conversation_id=cid, user_id="alice",
            role="bogus", content="x",
        )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_conversations_matches_message_content():
    cid = ensure_conversation(
        conversation_id="search-1", tenant_id="t-srch", user_id="alice",
    )
    insert_user_message(
        tenant_id="t-srch", conversation_id=cid, user_id="alice",
        content="What about water scarcity in Adani Power?",
    )
    hits = search_conversations(
        tenant_id="t-srch", user_id="alice", query="water scarcity",
    )
    assert len(hits) >= 1
    assert "water scarcity" in hits[0]["snippet"].lower() or "water" in hits[0]["snippet"].lower()


def test_search_isolates_by_user():
    """Alice's content shouldn't surface in bob's search."""
    cid_a = ensure_conversation(
        conversation_id="iso-alice", tenant_id="t-iso2", user_id="alice",
    )
    insert_user_message(
        tenant_id="t-iso2", conversation_id=cid_a, user_id="alice",
        content="secret-keyword-xyz",
    )
    bob_hits = search_conversations(
        tenant_id="t-iso2", user_id="bob", query="secret-keyword-xyz",
    )
    assert bob_hits == []
    alice_hits = search_conversations(
        tenant_id="t-iso2", user_id="alice", query="secret-keyword-xyz",
    )
    assert len(alice_hits) >= 1


@pytest.mark.skipif(not fts5_available(), reason="FTS5 unavailable on this SQLite build")
def test_fts5_multi_token_search_matches_logical_and():
    """FTS5 should match documents containing ALL query terms, not just any."""
    cid = ensure_conversation(
        conversation_id="fts-multi", tenant_id="t-fts", user_id="alice",
    )
    insert_user_message(
        tenant_id="t-fts", conversation_id=cid, user_id="alice",
        content="climate transition risk for Indian power sector",
    )
    insert_user_message(
        tenant_id="t-fts", conversation_id=cid, user_id="alice",
        content="climate alone",
    )
    insert_user_message(
        tenant_id="t-fts", conversation_id=cid, user_id="alice",
        content="risk alone",
    )
    # Multi-term query — FTS5 should rank docs with both terms above docs with one
    hits = search_conversations(
        tenant_id="t-fts", user_id="alice", query="climate risk",
    )
    snippets = [h["snippet"].lower() for h in hits]
    assert any("climate" in s and "risk" in s for s in snippets)


@pytest.mark.skipif(not fts5_available(), reason="FTS5 unavailable on this SQLite build")
def test_fts5_search_ignores_one_char_tokens():
    """Single-char tokens are dropped before sending to FTS5 to avoid syntax errors."""
    cid = ensure_conversation(
        conversation_id="fts-short", tenant_id="t-fts2", user_id="alice",
    )
    insert_user_message(
        tenant_id="t-fts2", conversation_id=cid, user_id="alice",
        content="water risk",
    )
    # 'a' is < 2 chars and should be dropped, leaving just 'water'
    hits = search_conversations(
        tenant_id="t-fts2", user_id="alice", query="a water",
    )
    assert len(hits) >= 1


# ---------------------------------------------------------------------------
# Fork
# ---------------------------------------------------------------------------


def test_fork_creates_new_conversation_with_copied_messages():
    src = ensure_conversation(
        conversation_id="fork-src", tenant_id="t-fork", user_id="alice",
    )
    insert_user_message(
        tenant_id="t-fork", conversation_id=src, user_id="alice", content="m1",
    )
    insert_assistant_message(
        tenant_id="t-fork", conversation_id=src, content="m2",
    )
    forked_id = fork_conversation(
        source_conversation_id=src, tenant_id="t-fork", user_id="alice",
    )
    assert forked_id != src
    forked_msgs = load_conversation_history(
        conversation_id=forked_id, tenant_id="t-fork", user_id="alice",
    )
    assert len(forked_msgs) == 2
    assert [m.content for m in forked_msgs] == ["m1", "m2"]


def test_fork_rejects_when_user_does_not_own_source():
    src = ensure_conversation(
        conversation_id="fork-private", tenant_id="t-frk2", user_id="alice",
    )
    with pytest.raises(PermissionError):
        fork_conversation(
            source_conversation_id=src, tenant_id="t-frk2", user_id="bob",
        )
