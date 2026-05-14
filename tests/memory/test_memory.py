"""Memory store + extractor + retrieval tests."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from engine.chat.conversations import ensure_conversation
from engine.chat.messages import insert_assistant_message, insert_user_message
from engine.memory.extractor import extract_memories_from_conversation
from engine.memory.retrieval import retrieve_for_injection
from engine.memory.store import delete_memory, insert_memory, list_memories


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def test_insert_memory_round_trip():
    rec = insert_memory(
        tenant_id="t-mem", user_id="alice", scope="personal", fact_kind="fact",
        content="Alice prefers concise executive summaries.",
    )
    assert rec.memory_id
    items = list_memories(tenant_id="t-mem", user_id="alice")
    assert any(m.memory_id == rec.memory_id for m in items)


def test_insert_rejects_invalid_scope():
    with pytest.raises(ValueError, match="scope"):
        insert_memory(
            tenant_id="t", user_id="alice", scope="bogus",
            fact_kind="fact", content="x",
        )


def test_insert_rejects_empty_content():
    with pytest.raises(ValueError, match="content"):
        insert_memory(
            tenant_id="t", user_id="alice", scope="personal",
            fact_kind="fact", content="   ",
        )


def test_personal_memory_isolated_per_user():
    insert_memory(
        tenant_id="t-iso", user_id="alice", scope="personal",
        fact_kind="preference", content="Alice's secret prefs",
    )
    alice = list_memories(tenant_id="t-iso", user_id="alice")
    bob = list_memories(tenant_id="t-iso", user_id="bob")
    assert any("Alice" in m.content for m in alice)
    assert all("Alice" not in m.content for m in bob)


def test_shared_memory_visible_to_all_users_in_tenant():
    insert_memory(
        tenant_id="t-shr", user_id=None, scope="shared",
        fact_kind="fact", content="Company's Q4 PMF target",
    )
    for uid in ("alice", "bob"):
        ms = list_memories(tenant_id="t-shr", user_id=uid)
        assert any("Q4 PMF" in m.content for m in ms)


def test_soft_delete_hides_from_default_list():
    rec = insert_memory(
        tenant_id="t-del", user_id="alice", scope="personal",
        fact_kind="fact", content="ephemeral fact",
    )
    delete_memory(memory_id=rec.memory_id, tenant_id="t-del", user_id="alice")
    visible = list_memories(tenant_id="t-del", user_id="alice")
    assert all(m.memory_id != rec.memory_id for m in visible)
    visible_incl = list_memories(
        tenant_id="t-del", user_id="alice", include_deactivated=True,
    )
    assert any(m.memory_id == rec.memory_id for m in visible_incl)


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def test_retrieval_finds_matching_memory():
    insert_memory(
        tenant_id="t-ret", user_id="alice", scope="personal", fact_kind="fact",
        content="Alice tracks water risk for Adani Power weekly.",
    )
    insert_memory(
        tenant_id="t-ret", user_id="alice", scope="personal", fact_kind="fact",
        content="Alice doesn't follow JSW Steel.",
    )
    hits = retrieve_for_injection(
        tenant_id="t-ret", user_id="alice", query="water risk Adani",
    )
    assert hits
    assert any("water risk" in h.content.lower() for h in hits)


def test_retrieval_empty_query_returns_empty():
    assert retrieve_for_injection(
        tenant_id="t", user_id="alice", query="",
    ) == []


def test_retrieval_respects_user_scope():
    insert_memory(
        tenant_id="t-scope", user_id="alice", scope="personal", fact_kind="fact",
        content="alice-specific tagging preference",
    )
    bob_hits = retrieve_for_injection(
        tenant_id="t-scope", user_id="bob", query="alice-specific tagging",
    )
    assert bob_hits == []


# ---------------------------------------------------------------------------
# Extractor (stubbed LLM)
# ---------------------------------------------------------------------------


class _StubLLMClient:
    """Returns a canned JSON response."""
    def __init__(self, payload: dict):
        self._payload = payload

    def complete(self, **kwargs):
        return SimpleNamespace(text=json.dumps(self._payload))


def test_extractor_inserts_memories_from_stub_response():
    cid = ensure_conversation(
        conversation_id="ext-1", tenant_id="t-ext", user_id="alice",
    )
    insert_user_message(
        tenant_id="t-ext", conversation_id=cid, user_id="alice",
        content="I want to track water risk for ICICI Bank quarterly.",
    )
    insert_assistant_message(
        tenant_id="t-ext", conversation_id=cid,
        content="Noted — I'll surface water-related ICICI articles.",
    )
    stub = _StubLLMClient({"memories": [
        {"fact_kind": "preference", "scope": "personal",
         "content": "Alice tracks water risk for ICICI Bank quarterly.",
         "confidence": 0.9},
        {"fact_kind": "open_thread", "scope": "personal",
         "content": "Surface water-related ICICI articles to Alice.",
         "confidence": 0.7},
    ]})
    recs = extract_memories_from_conversation(
        conversation_id=cid, tenant_id="t-ext", user_id="alice", client=stub,
    )
    assert len(recs) == 2
    fact_kinds = {r.fact_kind for r in recs}
    assert {"preference", "open_thread"}.issubset(fact_kinds)


def test_extractor_returns_empty_on_malformed_json():
    cid = ensure_conversation(
        conversation_id="ext-bad", tenant_id="t-extbad", user_id="alice",
    )
    insert_user_message(
        tenant_id="t-extbad", conversation_id=cid, user_id="alice", content="hi",
    )

    class _Bad:
        def complete(self, **kwargs):
            return SimpleNamespace(text="not json")

    recs = extract_memories_from_conversation(
        conversation_id=cid, tenant_id="t-extbad", user_id="alice", client=_Bad(),
    )
    assert recs == []


def test_extractor_returns_empty_for_unknown_conversation():
    recs = extract_memories_from_conversation(
        conversation_id="does-not-exist", tenant_id="t", user_id="alice",
        client=_StubLLMClient({"memories": []}),
    )
    assert recs == []
