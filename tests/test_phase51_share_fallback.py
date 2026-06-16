"""Phase 51.C — share/email reads the durable Postgres mirror when the on-disk
JSON is absent.

On Railway the data dir is read-only / ephemeral, so insight JSON written at
analysis time isn't on disk for the share path to scan — share_service returned
"article ... not found in outputs (scanned 0 candidates)". It now falls back to
the insight_payload mirror (parity with insight_detail), so an article that's in
Postgres can be shared without a re-onboard.
"""
from __future__ import annotations

from engine.models import insight_payload as ip
from engine.output.share_service import share_article_by_email

_AID = "share-fallback-1"


def _payload() -> dict:
    return {
        "article": {
            "id": _AID, "title": "Adani Power posts record Q1 profit",
            "url": "https://example.com/adani-q1", "source": "Mint",
            "published_at": "2026-06-01T00:00:00Z", "company_slug": "adani-power",
            "image_url": "",
        },
        "pipeline": {"relevance": {}, "themes": {}},
        "insight": {
            "headline": "Adani Power posts record Q1 profit",
            "analysis": {
                "what_changed": "Q1 net profit rose 15% YoY.",
                "why_it_matters": "Material to FY26 earnings and the capex plan.",
                "what_it_triggers": "Likely analyst upgrades and a re-rating.",
                "what_to_watch": "Next-quarter merchant-power realisations.",
            },
            "decision_summary": {
                "financial_exposure": "₹500 Cr (engine estimate)",
                "materiality": "HIGH", "key_risk": "merchant price volatility",
            },
            "event_polarity": "positive",
            "criticality": {"band": "HIGH", "score": 0.8},
        },
        "recommendations": None,
        "perspectives": {},
        "evidence_pack": None,
        "role_payloads": {},
        "meta": {"schema_version": "3.3-editorial-lede", "tier": "critical"},
    }


def test_share_falls_back_to_db_mirror_when_disk_empty(tmp_path) -> None:
    """Disk has nothing (outputs_root points at an empty tmp dir) but the
    article is in the Postgres mirror → share renders instead of erroring."""
    ip.upsert(_AID, "adani-power", _payload())
    result = share_article_by_email(
        article_id=_AID,
        company_slug="adani-power",
        recipient_email="reader@example.com",
        outputs_root=tmp_path,  # empty → on-disk scan returns 0 candidates
        dry_run=True,           # render only, no send
    )
    assert result.article_id == _AID
    # The key regression: must NOT be the "not found" failure any more …
    assert not (result.status == "failed" and "not found" in (result.error or "")), result.error
    # … and something actually rendered from the mirrored payload.
    assert result.html_length > 0


def test_share_still_errors_when_absent_everywhere(tmp_path) -> None:
    """No disk file AND no mirror row → the graceful not-found error stands."""
    result = share_article_by_email(
        article_id="does-not-exist-anywhere",
        company_slug="adani-power",
        recipient_email="reader@example.com",
        outputs_root=tmp_path,
        dry_run=True,
    )
    assert result.status == "failed"
    assert "not found" in (result.error or "")
