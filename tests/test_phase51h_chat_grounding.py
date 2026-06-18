"""Phase 51.H — chat/Ask grounding fixes.

Repairs the cold-path Ask which produced ungrounded boilerplate: it
confabulated peer sets ("typically NTPC, Tata Power, JSW Energy") and
deflected to non-existent UI panels. Fixes: (a) bidirectional
query_competitors so asymmetric competessWith edges still surface peers,
(b) wire the real peer set into the chat system prompt, (c) replace the
"name the panel" mandate with a strict anti-confabulation rule.

Run: python -m pytest tests/test_phase51h_chat_grounding.py -q
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Bidirectional competitor query
# ---------------------------------------------------------------------------


def test_query_competitors_bidirectional():
    """competessWith edges are hand-authored and asymmetric (waaree->adani
    exists but not the reverse); the query must match BOTH directions so
    adani-power's peer set includes Waaree, not just JSW."""
    from engine.ontology.intelligence import query_competitors

    peers = query_competitors("adani-power")
    if not peers:
        pytest.skip("ontology graph not loaded with company competessWith edges")
    assert "JSW Energy" in peers          # outbound edge
    assert "Waaree Energies" in peers     # inbound (asymmetric) edge — the fix
    joined = " ".join(peers)
    # the LLM's invented peers are NOT in the 7-company graph
    assert "NTPC" not in joined
    assert "Tata Power" not in joined


def test_query_competitors_symmetric_unchanged():
    """The already-symmetric bank cluster must still resolve cleanly."""
    from engine.ontology.intelligence import query_competitors

    peers = query_competitors("icici-bank")
    if not peers:
        pytest.skip("ontology graph not loaded")
    assert "YES Bank" in peers
    assert "IDFC First Bank" in peers


# ---------------------------------------------------------------------------
# Competitor context block
# ---------------------------------------------------------------------------


def test_load_competitor_context_block():
    from api.routes.chat import _load_competitor_context

    block = _load_competitor_context("adani-power")
    if block is None:
        pytest.skip("ontology graph not loaded")
    assert "PEER SET" in block
    assert "JSW Energy" in block
    assert "Waaree Energies" in block
    # the instruction that pins the model to the real set
    assert "ONLY tracked competitors" in block


def test_load_competitor_context_none_for_no_peers():
    """A company with no competessWith edges (SBI) yields None — so the chat
    injects no peer block and the anti-confab rule makes the model say it
    has no peer data rather than inventing one."""
    from api.routes.chat import _load_competitor_context

    assert _load_competitor_context("state-bank-of-india") is None


# ---------------------------------------------------------------------------
# System prompt: competitor injection + anti-confabulation
# ---------------------------------------------------------------------------


def test_system_prompt_injects_competitor_block():
    from api.routes.chat import _build_system_prompt

    prompt = _build_system_prompt(
        memories=[], tenant="t", user="u",
        competitor_context="PEER SET (...): JSW Energy, Waaree Energies",
    )
    assert "PEER SET" in prompt
    assert "JSW Energy" in prompt


def test_system_prompt_drops_panel_deflection_mandate():
    """The old prompt instructed the LLM to 'name the panel that has it' —
    the confabulation driver behind both bad outputs. The new prompt forbids
    inventing panels/peers/figures."""
    from api.routes.chat import _build_system_prompt

    prompt = _build_system_prompt(memories=[], tenant="t", user="u")
    assert "name the panel that has it" not in prompt
    assert "GROUNDING DISCIPLINE" in prompt
    assert "NEVER fabricate" in prompt
