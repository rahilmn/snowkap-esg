"""Phase 3 §5.3 — LLM upgrade path tests.

Validates the optional LLM polish layer:
  - Default-off (no env flag) → deterministic baselines unchanged
  - Env flag set + key absent → falls back to deterministic
  - Env flag set + LLM raises → falls back to deterministic
  - Env flag set + LLM returns malformed JSON → falls back
  - Env flag set + LLM returns valid JSON → headline/takeaways/paragraph
    swap to LLM output; hero metric / recs / panels stay deterministic
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from engine.analysis.evidence_pack import (
    CascadeBlock,
    EvidencePack,
    FrameworkHit,
)
from engine.analysis.role_generators import (
    RecommendationStub,
    generate_cfo_payload,
)
from engine.analysis.role_generators.llm_upgrade import (
    llm_upgrade_enabled,
    maybe_apply_llm_polish,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Default to env flag OFF for every test. Each test sets it
    explicitly when exercising the LLM path."""
    monkeypatch.delenv("SNOWKAP_LLM_ROLE_GENERATORS", raising=False)
    yield


# ---------------------------------------------------------------------------
# llm_upgrade_enabled
# ---------------------------------------------------------------------------


def test_llm_upgrade_disabled_when_env_flag_unset():
    assert llm_upgrade_enabled() is False


def test_llm_upgrade_disabled_when_env_flag_zero(monkeypatch):
    monkeypatch.setenv("SNOWKAP_LLM_ROLE_GENERATORS", "0")
    assert llm_upgrade_enabled() is False


def test_llm_upgrade_disabled_when_no_api_key(monkeypatch):
    monkeypatch.setenv("SNOWKAP_LLM_ROLE_GENERATORS", "1")
    with patch(
        "engine.config.get_openai_api_key", return_value="",
    ):
        assert llm_upgrade_enabled() is False


def test_llm_upgrade_enabled_with_flag_and_key(monkeypatch):
    monkeypatch.setenv("SNOWKAP_LLM_ROLE_GENERATORS", "1")
    with patch(
        "engine.config.get_openai_api_key", return_value="sk-test",
    ):
        assert llm_upgrade_enabled() is True


# ---------------------------------------------------------------------------
# maybe_apply_llm_polish — fallback paths
# ---------------------------------------------------------------------------


def _det_fields() -> dict:
    return {
        "headline": "deterministic headline",
        "role_takeaways": ["det takeaway 1", "det takeaway 2"],
        "role_paragraph": "deterministic paragraph composed from EvidencePack.",
    }


def test_polish_returns_deterministic_when_flag_off():
    out = maybe_apply_llm_polish(EvidencePack(), "cfo", _det_fields())
    assert out["headline"] == "deterministic headline"


def test_polish_returns_deterministic_when_flag_on_but_no_key(monkeypatch):
    monkeypatch.setenv("SNOWKAP_LLM_ROLE_GENERATORS", "1")
    with patch("engine.config.get_openai_api_key", return_value=""):
        out = maybe_apply_llm_polish(EvidencePack(), "cfo", _det_fields())
    assert out["headline"] == "deterministic headline"


def test_polish_returns_deterministic_when_llm_call_raises(monkeypatch):
    monkeypatch.setenv("SNOWKAP_LLM_ROLE_GENERATORS", "1")
    with patch("engine.config.get_openai_api_key", return_value="sk-test"), \
         patch(
             "engine.analysis.role_generators.llm_upgrade._call_llm",
             side_effect=RuntimeError("boom"),
         ):
        out = maybe_apply_llm_polish(EvidencePack(), "cfo", _det_fields())
    assert out["headline"] == "deterministic headline"


def test_polish_returns_deterministic_on_malformed_llm_json(monkeypatch):
    """Missing required key → fallback. Schema-strict on the LLM output."""
    monkeypatch.setenv("SNOWKAP_LLM_ROLE_GENERATORS", "1")
    with patch("engine.config.get_openai_api_key", return_value="sk-test"), \
         patch(
             "engine.analysis.role_generators.llm_upgrade._call_llm",
             return_value={"only_some_key": "value"},
         ):
        out = maybe_apply_llm_polish(EvidencePack(), "cfo", _det_fields())
    assert out["headline"] == "deterministic headline"


def test_polish_returns_deterministic_on_unknown_role(monkeypatch):
    monkeypatch.setenv("SNOWKAP_LLM_ROLE_GENERATORS", "1")
    with patch("engine.config.get_openai_api_key", return_value="sk-test"):
        out = maybe_apply_llm_polish(EvidencePack(), "marketing", _det_fields())
    assert out["headline"] == "deterministic headline"


# ---------------------------------------------------------------------------
# maybe_apply_llm_polish — LLM success path
# ---------------------------------------------------------------------------


def test_polish_swaps_three_fields_when_llm_returns_valid_json(monkeypatch):
    monkeypatch.setenv("SNOWKAP_LLM_ROLE_GENERATORS", "1")
    llm_output = {
        "headline": "P&L compresses ~₹1,900 Cr · hedge by Q3",
        "role_takeaways": [
            "P&L exposure ₹1,900 Cr concentrated in Q4 2026.",
            "Peer precedent: Tata Power SECI 4 GW (2024).",
            "Hedge 60% of USD exposure; 6mo payback at ₹50 Cr cost.",
        ],
        "role_paragraph": (
            "Margin compresses ~₹1,900 Cr through Q3. Hedge USD by 2026-09-30. "
            "Tata Power SECI precedent shows 6mo payback at peer scale."
        ),
    }
    with patch("engine.config.get_openai_api_key", return_value="sk-test"), \
         patch(
             "engine.analysis.role_generators.llm_upgrade._call_llm",
             return_value=llm_output,
         ):
        out = maybe_apply_llm_polish(EvidencePack(), "cfo", _det_fields())

    assert out["headline"] == llm_output["headline"]
    assert out["role_takeaways"] == llm_output["role_takeaways"]
    assert out["role_paragraph"] == llm_output["role_paragraph"]


# ---------------------------------------------------------------------------
# Integration: CFO generator with LLM polish enabled
# ---------------------------------------------------------------------------


def test_cfo_generator_uses_llm_polish_when_enabled(monkeypatch):
    """End-to-end: generate_cfo_payload calls the LLM upgrade and the
    returned payload reflects the LLM output, not the deterministic
    baseline."""
    monkeypatch.setenv("SNOWKAP_LLM_ROLE_GENERATORS", "1")
    llm_output = {
        "headline": "LLM-polished CFO headline ₹1,900 Cr",
        "role_takeaways": ["LLM bullet 1", "LLM bullet 2", "LLM bullet 3"],
        "role_paragraph": "LLM-polished CFO paragraph.",
    }
    pack = EvidencePack(
        cascade=CascadeBlock(total_cr=1857.6),
        frameworks=[FrameworkHit(code="BRSR:P6:Q14")],
    )
    with patch("engine.config.get_openai_api_key", return_value="sk-test"), \
         patch(
             "engine.analysis.role_generators.llm_upgrade._call_llm",
             return_value=llm_output,
         ):
        payload = generate_cfo_payload(pack)

    assert payload.headline == "LLM-polished CFO headline ₹1,900 Cr"
    assert payload.role_paragraph == "LLM-polished CFO paragraph."
    # Locked-contract fields stay deterministic — hero label, panels
    assert payload.hero_metric.label == "P&L exposure"
    assert "personal_stakes" in payload.visible_panels


def test_cfo_generator_falls_back_to_deterministic_when_flag_off():
    """Default behaviour: env flag absent → deterministic baseline runs.
    This is what the existing 16-test cfo_role_generator suite asserts —
    here we just confirm explicitly."""
    pack = EvidencePack(cascade=CascadeBlock(total_cr=1857.6))
    payload = generate_cfo_payload(pack)
    # Deterministic headline pattern
    assert "P&L" in payload.headline
    assert "₹" in payload.headline
    # And the deterministic ₹ rounding is sig-2 (1,900 not 1,857.6)
    assert "1,900" in payload.headline
