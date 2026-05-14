"""LLM proposer tests — env-flag gating + stub-client smoke + fallback."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from engine.autoresearcher.knob_kinds.ordinal_mapping import (
    OrdinalMappingKnob,
    OrdinalMappingState,
)
from engine.autoresearcher.llm_proposer import (
    is_llm_proposer_enabled,
    llm_propose,
)
from engine.autoresearcher.ontology_introspector import KnobRegistry


class _StubClient:
    def __init__(self, response_text: str, raise_exc: Exception | None = None):
        self._response = response_text
        self._raise = raise_exc
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        if self._raise:
            raise self._raise
        msg = SimpleNamespace(content=self._response)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _make_registry():
    state = OrdinalMappingState(values={
        ("confidence_band", "low"): 0.30,
        ("confidence_band", "high"): 0.85,
    })
    reg = KnobRegistry()
    reg.ordinal_state = state
    reg.knobs = [
        OrdinalMappingKnob(category="confidence_band", label="low", state=state),
        OrdinalMappingKnob(category="confidence_band", label="high", state=state),
    ]
    return reg


def test_proposer_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SNOWKAP_AUTORESEARCHER_LLM_PROPOSER", raising=False)
    assert is_llm_proposer_enabled() is False


def test_proposer_enabled_when_env_set(monkeypatch):
    monkeypatch.setenv("SNOWKAP_AUTORESEARCHER_LLM_PROPOSER", "1")
    assert is_llm_proposer_enabled() is True


def test_proposer_returns_none_when_disabled(monkeypatch):
    monkeypatch.delenv("SNOWKAP_AUTORESEARCHER_LLM_PROPOSER", raising=False)
    result = llm_propose(registry=_make_registry())
    assert result is None


def test_proposer_returns_proposal_on_valid_llm_response(monkeypatch):
    monkeypatch.setenv("SNOWKAP_AUTORESEARCHER_LLM_PROPOSER", "1")
    raw = json.dumps({
        "knob_id": "confidence_band:low",
        "new_value": 0.32,
        "rationale": "lift low-band slightly",
    })
    result = llm_propose(registry=_make_registry(), client=_StubClient(raw))
    assert result is not None
    assert result.knob.knob_id == "confidence_band:low"
    assert result.new_value == 0.32


def test_proposer_returns_none_on_malformed_json(monkeypatch):
    monkeypatch.setenv("SNOWKAP_AUTORESEARCHER_LLM_PROPOSER", "1")
    result = llm_propose(
        registry=_make_registry(),
        client=_StubClient("not json"),
    )
    assert result is None


def test_proposer_returns_none_on_api_error(monkeypatch):
    monkeypatch.setenv("SNOWKAP_AUTORESEARCHER_LLM_PROPOSER", "1")
    result = llm_propose(
        registry=_make_registry(),
        client=_StubClient("", raise_exc=RuntimeError("timeout")),
    )
    assert result is None


def test_proposer_returns_none_when_knob_id_unknown(monkeypatch):
    monkeypatch.setenv("SNOWKAP_AUTORESEARCHER_LLM_PROPOSER", "1")
    raw = json.dumps({
        "knob_id": "does:not:exist",
        "new_value": 0.5,
        "rationale": "x",
    })
    result = llm_propose(registry=_make_registry(), client=_StubClient(raw))
    assert result is None


def test_proposer_returns_none_for_empty_registry(monkeypatch):
    monkeypatch.setenv("SNOWKAP_AUTORESEARCHER_LLM_PROPOSER", "1")
    raw = json.dumps({"knob_id": "x", "new_value": 0.5, "rationale": "x"})
    result = llm_propose(registry=KnobRegistry(), client=_StubClient(raw))
    assert result is None
