"""L7 — LLM belief refiner tests (with stub OpenAI client).

Verifies the LLM callback wiring + parsing + failure fallbacks WITHOUT
making real API calls.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from engine.governance.belief_revision import BeliefProposal, revise_from_article
from engine.governance.belief_schema import RiskBandBelief
from engine.governance.llm_belief_refiner import (
    _parse_response,
    openai_belief_refiner,
)


# ---------------------------------------------------------------------------
# Stub OpenAI client
# ---------------------------------------------------------------------------


class _StubClient:
    """Minimal stand-in for `openai.OpenAI(...)` shape used by the refiner."""

    def __init__(self, response_text: str, raise_exc: Exception | None = None):
        self._response_text = response_text
        self._raise_exc = raise_exc

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        if self._raise_exc:
            raise self._raise_exc
        # Mirror the structure `response.choices[0].message.content`
        message = SimpleNamespace(content=self._response_text)
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice])


# ---------------------------------------------------------------------------
# _parse_response — unit tests on the parser alone
# ---------------------------------------------------------------------------


def test_parse_response_accepts_valid_proposal_list():
    raw = json.dumps({
        "proposals": [
            {
                "kind": "risk_band",
                "payload": {"topic": "climate", "band": "HIGH"},
                "confidence_band": "moderate",
                "rationale": "LLM confirmed",
                "rule_id": "LLM",
            }
        ]
    })
    out = _parse_response(raw)
    assert out is not None
    assert len(out) == 1
    assert isinstance(out[0].belief, RiskBandBelief)
    assert out[0].belief.band == "HIGH"


def test_parse_response_returns_none_on_malformed_json():
    assert _parse_response("not json at all") is None


def test_parse_response_drops_invalid_individual_proposals():
    """Schema-violating proposals are dropped silently; valid ones kept."""
    raw = json.dumps({
        "proposals": [
            # 1) Valid
            {"kind": "risk_band", "payload": {"topic": "x", "band": "LOW"},
             "confidence_band": "low", "rationale": "ok", "rule_id": "LLM"},
            # 2) Unknown kind
            {"kind": "unknown_belief", "payload": {}, "rationale": "x"},
            # 3) Invalid band value
            {"kind": "risk_band", "payload": {"topic": "y", "band": "EXTREME"},
             "rationale": "bad"},
            # 4) Empty topic
            {"kind": "risk_band", "payload": {"topic": "", "band": "LOW"},
             "rationale": "bad"},
        ]
    })
    out = _parse_response(raw)
    assert out is not None
    assert len(out) == 1
    assert out[0].belief.topic == "x"


def test_parse_response_returns_none_when_proposals_key_missing():
    raw = json.dumps({"results": []})  # wrong key
    assert _parse_response(raw) is None


# ---------------------------------------------------------------------------
# openai_belief_refiner — end-to-end with stub client
# ---------------------------------------------------------------------------


def _det_proposal():
    return BeliefProposal(
        belief=RiskBandBelief(topic="climate", band="HIGH"),
        rationale="R1: deterministic baseline",
        rule_id="R1",
    )


def test_refiner_returns_llm_output_when_response_valid():
    """LLM emits a refined proposal; refiner returns it."""
    response = json.dumps({
        "proposals": [{
            "kind": "risk_band",
            "payload": {"topic": "climate", "band": "CRITICAL"},  # upgrade
            "confidence_band": "high",
            "rationale": "LLM upgraded to CRITICAL after reviewing article",
            "rule_id": "LLM",
        }]
    })
    out = openai_belief_refiner(
        proposals=[_det_proposal()],
        context={"article": {"id": "a", "event_id": "x"}},
        client=_StubClient(response),
    )
    assert len(out) == 1
    assert out[0].belief.band == "CRITICAL"
    assert out[0].rule_id == "LLM"


def test_refiner_falls_back_on_api_error():
    """API error → deterministic baseline preserved."""
    out = openai_belief_refiner(
        proposals=[_det_proposal()],
        context={"article": {"id": "a"}},
        client=_StubClient("", raise_exc=RuntimeError("timeout")),
    )
    assert len(out) == 1
    assert out[0].rule_id == "R1"      # deterministic, NOT LLM
    assert out[0].belief.band == "HIGH"


def test_refiner_falls_back_on_malformed_json():
    out = openai_belief_refiner(
        proposals=[_det_proposal()],
        context={"article": {"id": "a"}},
        client=_StubClient("not json"),
    )
    assert len(out) == 1
    assert out[0].rule_id == "R1"


def test_refiner_falls_back_when_response_empty_after_parsing():
    """If LLM returns valid JSON but no proposals survive validation,
    we still fall back to the deterministic baseline (rather than
    handing the user an empty list)."""
    response = json.dumps({"proposals": []})
    out = openai_belief_refiner(
        proposals=[_det_proposal()],
        context={"article": {"id": "a"}},
        client=_StubClient(response),
    )
    # An empty refined list IS a legitimate LLM decision ("drop the
    # proposal as a false positive"). Verify the refiner respects it.
    assert out == []


def test_refiner_returns_input_unchanged_when_no_proposals_and_no_article():
    """Empty input + empty context → no-op, no LLM call needed."""
    out = openai_belief_refiner(
        proposals=[],
        context={},
        client=_StubClient("should not be called"),
    )
    assert out == []


# ---------------------------------------------------------------------------
# Integration with revise_from_article (the full callback path)
# ---------------------------------------------------------------------------


def test_refiner_plugs_into_revise_from_article_via_callback():
    """End-to-end: deterministic proposal flows through the LLM refiner
    via `revise_from_article(llm_callback=...)`."""
    response = json.dumps({
        "proposals": [{
            "kind": "risk_band",
            "payload": {"topic": "labour", "band": "CRITICAL"},
            "confidence_band": "moderate",
            "rationale": "LLM caught a labour angle the rule missed",
            "rule_id": "LLM",
        }]
    })
    stub = _StubClient(response)

    def callback(proposals, context):
        return openai_belief_refiner(proposals, context, client=stub)

    article = {
        "id": "a", "event_id": "ev", "event_polarity": "negative",
        "materiality": "HIGH", "topic": "climate",
    }
    out = revise_from_article(article=article, llm_callback=callback)
    # R1 fired; LLM swapped it for a labour-focused CRITICAL
    assert len(out) == 1
    assert out[0].belief.topic == "labour"
    assert out[0].belief.band == "CRITICAL"
