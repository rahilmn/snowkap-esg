"""Phase 24 (W2) — admin discovery review surface regression tests.

Two layers tested:

  A. ``engine.ontology.discovery.promoter.manual_decide`` — the helper
     that applies promote/reject/defer with required Toulmin
     justification and writes both the legacy + new audit logs.
  B. The ``DecideRequest`` Pydantic schema — argument validation for the
     ``POST /api/admin/discovery/decide`` route (covering the new
     'defer' decision + Toulmin requirement on reject/defer).

The router itself is auth-gated via dependencies that pull from
production JWT context, so a full HTTP integration test is out of
scope here — callers use the existing Phase 19 endpoint shape and the
unit tests below cover the behaviour. A sanity smoke at the API
boundary is added at the end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine import audit
from engine.ontology.discovery.candidates import (
    CATEGORY_ENTITY,
    CATEGORY_THEME,
    DiscoveryCandidate,
    DiscoveryBuffer,
    STATUS_PENDING,
    STATUS_PROMOTED,
    STATUS_REJECTED,
)
from engine.ontology.discovery.promoter import (
    STATUS_DEFERRED,
    DecideResult,
    manual_decide,
)


@pytest.fixture
def fresh_buffer(tmp_path, monkeypatch):
    """Replace the singleton DiscoveryBuffer with a tmp-backed one for
    each test so we don't pollute the real staging file."""
    staging = tmp_path / "discovery_staging.json"
    new_buffer = DiscoveryBuffer(staging_path=staging)
    monkeypatch.setattr(
        "engine.ontology.discovery.candidates._buffer", new_buffer
    )
    return new_buffer


@pytest.fixture
def patched_audit_dir(monkeypatch, tmp_path):
    """Redirect audit writers to a tmp dir so the promotion log isn't
    polluted across test runs."""
    audit_dir = tmp_path / "audit_root"

    def _resolve(_base=None):
        d = audit_dir / "audit"
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(audit, "_resolve_audit_dir", _resolve)
    return audit_dir


def _make_candidate(label="Test Co", slug="test_co", category=CATEGORY_ENTITY):
    return DiscoveryCandidate(
        category=category,
        label=label,
        slug=slug,
        article_ids=["a1", "a2", "a3"],
        sources=["Reuters", "Bloomberg"],
        companies=["icici-bank"],
        confidence=0.85,
        first_seen="2026-05-01T00:00:00+00:00",
        last_seen="2026-05-04T00:00:00+00:00",
        data={"entity_type": "competitor"},
        status=STATUS_PENDING,
    )


# ---------------------------------------------------------------------------
# A. manual_decide — basic decision branches
# ---------------------------------------------------------------------------


class TestManualDecideBasics:
    def test_unknown_decision_returns_error(self, fresh_buffer):
        result = manual_decide("entity:foo", "approve")  # 'approve' not allowed
        assert isinstance(result, DecideResult)
        assert result.ok is False
        assert "unknown decision" in result.message

    def test_invalid_candidate_id_format_rejected(self, fresh_buffer):
        result = manual_decide("not_a_valid_id", "promote")
        assert result.ok is False
        assert "category:slug" in result.message

    def test_missing_candidate_returns_error(self, fresh_buffer):
        # Empty buffer
        result = manual_decide("entity:does_not_exist", "promote")
        assert result.ok is False
        assert "not found" in result.message

    def test_reject_without_toulmin_blocked(self, fresh_buffer, patched_audit_dir):
        cand = _make_candidate()
        fresh_buffer.add(cand)
        # Reject without toulmin → error
        result = manual_decide("entity:test_co", "reject", toulmin=None)
        assert result.ok is False
        assert "toulmin justification" in result.message
        # Buffer status unchanged
        assert fresh_buffer.get(CATEGORY_ENTITY, "test_co").status == STATUS_PENDING

    def test_defer_without_toulmin_blocked(self, fresh_buffer, patched_audit_dir):
        cand = _make_candidate()
        fresh_buffer.add(cand)
        result = manual_decide("entity:test_co", "defer", toulmin=None)
        assert result.ok is False
        assert "toulmin" in result.message.lower()


# ---------------------------------------------------------------------------
# B. Successful decisions update buffer + write promotion log
# ---------------------------------------------------------------------------


class TestSuccessfulDecisions:
    def test_promote_marks_promoted_and_logs(self, fresh_buffer, patched_audit_dir):
        cand = _make_candidate()
        fresh_buffer.add(cand)
        toulmin = audit.make_toulmin(
            claim="Tier-1 entity, auto-promote acceptable",
            grounds=["confidence 0.85", "3 articles", "2 sources"],
            warrant="Phase 19 Tier-1 framework auto-promotion rule",
        )
        result = manual_decide(
            "entity:test_co", "promote",
            toulmin=toulmin, user_id="admin@snowkap.com",
        )
        # promote may fail to insert if graph isn't loadable in test env,
        # but the buffer status update + log entry must still happen.
        assert result.decision == "promote"
        assert fresh_buffer.get(CATEGORY_ENTITY, "test_co").status == STATUS_PROMOTED
        # Promotion log entry written
        entries = list(audit.read_promotion_log(patched_audit_dir))
        assert len(entries) == 1
        e = entries[0]
        assert e["decision"] == "promote"
        assert e["candidate_id"] == "entity:test_co"
        assert e["category"] == "entity"
        assert e["user_id"] == "admin@snowkap.com"
        assert e["toulmin"]["claim"].startswith("Tier-1 entity")
        assert e["confidence"] == 0.85

    def test_reject_marks_rejected_and_logs(self, fresh_buffer, patched_audit_dir):
        cand = _make_candidate(label="Spam Co", slug="spam_co")
        fresh_buffer.add(cand)
        toulmin = audit.make_toulmin(
            claim="reject: not an ESG-relevant entity",
            grounds=["entity is a spam-text artefact"],
            warrant="Snowkap entity admissibility policy",
        )
        result = manual_decide(
            "entity:spam_co", "reject",
            toulmin=toulmin, user_id="ontology@snowkap.com",
        )
        assert result.ok is True
        assert result.new_status == STATUS_REJECTED
        assert fresh_buffer.get(CATEGORY_ENTITY, "spam_co").status == STATUS_REJECTED
        entries = list(audit.read_promotion_log(patched_audit_dir))
        e = entries[0]
        assert e["decision"] == "reject"
        assert e["toulmin"]["claim"].startswith("reject")

    def test_defer_marks_deferred_keeps_in_buffer(
        self, fresh_buffer, patched_audit_dir
    ):
        cand = _make_candidate(label="Maybe Co", slug="maybe_co")
        fresh_buffer.add(cand)
        toulmin = audit.make_toulmin(
            claim="defer: insufficient evidence to decide now",
            grounds=["only 3 articles", "single-source"],
            warrant="Snowkap deferred-review policy",
            rebuttal="if 2+ more sources surface within 14 days, re-review",
        )
        result = manual_decide(
            "entity:maybe_co", "defer",
            toulmin=toulmin, user_id="analyst@snowkap.com",
        )
        assert result.ok is True
        assert result.new_status == STATUS_DEFERRED
        # Candidate still in buffer (defer ≠ remove)
        assert fresh_buffer.get(CATEGORY_ENTITY, "maybe_co") is not None
        assert fresh_buffer.get(CATEGORY_ENTITY, "maybe_co").status == STATUS_DEFERRED


# ---------------------------------------------------------------------------
# C. Pydantic schema validation (DecideRequest)
# ---------------------------------------------------------------------------


class TestDecideRequestValidation:
    def test_unknown_decision_rejected_at_schema(self):
        from api.routes.discovery import DecideRequest
        with pytest.raises(Exception):
            DecideRequest(candidate_id="entity:tata_power", decision="approve")  # type: ignore[arg-type]

    def test_candidate_id_pattern_enforced(self):
        from api.routes.discovery import DecideRequest
        # 'category:slug' format
        ok = DecideRequest(candidate_id="entity:tata_power", decision="promote")
        assert ok.decision == "promote"
        # Bare slug rejected
        with pytest.raises(Exception):
            DecideRequest(candidate_id="tata_power", decision="promote")

    def test_toulmin_optional_for_promote(self):
        from api.routes.discovery import DecideRequest
        # Promote without toulmin is valid
        req = DecideRequest(candidate_id="entity:x", decision="promote")
        assert req.toulmin is None

    def test_toulmin_block_min_grounds_enforced(self):
        from api.routes.discovery import DecideRequest, ToulminBlock
        # grounds must have at least 1 entry
        with pytest.raises(Exception):
            ToulminBlock(claim="X is wrong", grounds=[], warrant="policy")

    def test_toulmin_block_accepts_full_shape(self):
        from api.routes.discovery import ToulminBlock
        t = ToulminBlock(
            claim="reject: spam entity",
            grounds=["no real-world referent"],
            warrant="Snowkap entity admissibility",
            qualifier="confidence 0.9",
            rebuttal="if a corporate filing surfaces, re-promote",
        )
        assert t.qualifier == "confidence 0.9"


# ---------------------------------------------------------------------------
# D. Router import + registration smoke
# ---------------------------------------------------------------------------


class TestRouterRegistration:
    def test_discovery_router_registered_in_main(self):
        from api.main import app
        paths = {r.path for r in app.routes}
        assert "/api/admin/discovery/staged" in paths
        assert "/api/admin/discovery/decide" in paths
        assert "/api/admin/discovery/history" in paths

    def test_routes_require_auth(self):
        # The router must mount with require_auth + require_bearer_permission
        from api.routes.discovery import router
        deps = router.dependencies
        # Two dependencies expected: auth + permission
        assert len(deps) == 2
