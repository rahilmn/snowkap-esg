"""Phase 3 §5.1 — EvidencePack write-time persistence tests.

Validates that `engine.output.writer.write_insight` stamps the pack
onto the JSON payload and the pack survives a JSON round-trip.

Mocks the side-effects (file writes + sqlite upsert) so no disk state
is touched. The DUT here is the payload assembly, not the file plumbing.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def _stub_pipeline_result():
    """Bare PipelineResult-shaped stub. Only the fields write_insight reads."""
    return SimpleNamespace(
        article_id="art-1",
        title="Test article",
        url="https://example.com/a",
        source="Reuters",
        published_at="2026-05-10T00:00:00Z",
        company_slug="test-co",
        image_url="",
        to_dict=lambda: {
            "article_id": "art-1", "title": "Test article",
            "frameworks": [], "causal_chains": [],
        },
        risk=None,
        frameworks=[],
        causal_chains=[],
    )


def _stub_insight():
    """Minimal DeepInsight-shaped stub."""
    return SimpleNamespace(
        to_dict=lambda: {
            "headline": "Test headline",
            "decision_summary": {
                "financial_exposure": "₹500 Cr (engine estimate)",
                "materiality": "HIGH",
            },
            "event_polarity": "negative",
            "criticality": {
                "score": 0.7,
                "components": {"painpoint_match": 0.85},
            },
            "financial_timeline": {"next_earnings": "2026-07-22"},
        },
    )


def test_write_insight_stamps_evidence_pack_on_payload():
    from engine.output import writer as writer_mod

    captured: dict = {}

    def _capture_write(path, data):
        # Intercept the first write (the insight payload)
        if "insight" in str(path) and "insights" in str(path):
            captured["payload"] = data
        return path

    with patch.object(writer_mod, "_write", side_effect=_capture_write), \
         patch.object(writer_mod, "upsert_article"), \
         patch.object(writer_mod, "get_output_dir", return_value=Path("/tmp/test-co"), create=True):
        writer_mod.write_insight(
            result=_stub_pipeline_result(),
            insight=_stub_insight(),
            perspectives={},
            recommendations=None,
        )

    payload = captured.get("payload") or {}
    # The structured pack must be on the payload
    assert "evidence_pack" in payload
    pack = payload["evidence_pack"]
    assert pack is not None
    # Polarity inferred from event_polarity
    assert pack["polarity"] == "negative"
    # Cascade total extracted from decision_summary
    assert pack["cascade"]["total_cr"] == 500.0
    # Decision windows from financial_timeline
    assert any(w["label"] == "Next Earnings" for w in pack["decision_windows"])
    # Painpoint match surfaced from criticality components
    assert len(pack["painpoint_matches"]) >= 1


def test_write_insight_evidence_pack_survives_json_roundtrip():
    """The stamped pack must be JSON-serialisable without TypeError —
    json.dumps in writer._write would fail otherwise."""
    import json
    from engine.output import writer as writer_mod

    captured: dict = {}

    def _capture_write(path, data):
        if "insight" in str(path) and "insights" in str(path):
            captured["payload"] = data
            # Round-trip through json to confirm serialisability
            json.dumps(data, ensure_ascii=False)
        return path

    with patch.object(writer_mod, "_write", side_effect=_capture_write), \
         patch.object(writer_mod, "upsert_article"), \
         patch.object(writer_mod, "get_output_dir", return_value=Path("/tmp/test-co"), create=True):
        writer_mod.write_insight(
            result=_stub_pipeline_result(),
            insight=_stub_insight(),
            perspectives={},
            recommendations=None,
        )

    payload = captured.get("payload") or {}
    # Should not raise
    json.dumps(payload, ensure_ascii=False)


def test_write_insight_stamps_evidence_pack_when_insight_is_none():
    """REJECTED articles have insight=None — pack should still stamp
    (as an empty pack) rather than raise or omit the key."""
    from engine.output import writer as writer_mod

    captured: dict = {}

    def _capture_write(path, data):
        if "insight" in str(path) and "insights" in str(path):
            captured["payload"] = data
        return path

    with patch.object(writer_mod, "_write", side_effect=_capture_write), \
         patch.object(writer_mod, "upsert_article"), \
         patch.object(writer_mod, "get_output_dir", return_value=Path("/tmp/test-co"), create=True):
        writer_mod.write_insight(
            result=_stub_pipeline_result(),
            insight=None,
            perspectives={},
            recommendations=None,
        )

    payload = captured.get("payload") or {}
    assert "evidence_pack" in payload
    pack = payload["evidence_pack"]
    assert pack is not None
    # Empty insight → empty pack with neutral polarity
    assert pack["polarity"] == "neutral"
    assert pack["cascade"]["total_cr"] == 0.0
    assert pack["frameworks"] == []


def test_write_insight_evidence_pack_failure_does_not_break_writes():
    """If build_evidence_pack raises, the write must still complete with
    evidence_pack=None — never block a real article write on the scaffold."""
    from engine.output import writer as writer_mod

    captured: dict = {}

    def _capture_write(path, data):
        if "insight" in str(path) and "insights" in str(path):
            captured["payload"] = data
        return path

    import engine.analysis.evidence_pack as ep_mod

    def _raise(*a, **kw):
        raise RuntimeError("boom")

    with patch.object(writer_mod, "_write", side_effect=_capture_write), \
         patch.object(writer_mod, "upsert_article"), \
         patch.object(writer_mod, "get_output_dir", return_value=Path("/tmp/test-co"), create=True), \
         patch.object(ep_mod, "build_evidence_pack", side_effect=_raise):
        writer_mod.write_insight(
            result=_stub_pipeline_result(),
            insight=_stub_insight(),
            perspectives={},
            recommendations=None,
        )

    payload = captured.get("payload") or {}
    # Key still present, value is None — writes survived the failure
    assert "evidence_pack" in payload
    assert payload["evidence_pack"] is None
