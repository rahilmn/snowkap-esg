"""Phase 24 (W4) — analyst session state regression tests.

Three layers tested:

  A. ``engine.models.analyst_session`` — dual-backend store: get / upsert /
     append_follow_up / remove_follow_up.
  B. ``api.routes.session`` Pydantic schemas + router registration.
  C. ``snowkap.hooks.session_start`` — banner composition is silent on
     missing data, hard-cap respected, no exceptions on corrupt logs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Per-test isolation — point engine.models.analyst_session at a tmp DB
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Redirect engine.db.connect() to a tmp SQLite DB for each test so we
    don't pollute the real data/snowkap.db.

    The dual-backend abstraction picks SQLite by default (no env override),
    so we just redirect the file path."""
    db_path = tmp_path / "test.db"
    # engine.db.connection.get_sqlite_path() reads from
    # engine.index.sqlite_index.DB_PATH at import time. The simplest
    # isolation is to monkeypatch DB_PATH itself + reset the schema flag.
    from engine.index import sqlite_index
    from engine.models import analyst_session
    monkeypatch.setattr(sqlite_index, "DB_PATH", db_path)
    monkeypatch.setattr(analyst_session, "DB_PATH", db_path)
    monkeypatch.setattr(analyst_session, "_SCHEMA_READY", False)
    # The engine.db module reads the path at connect time; force its cache
    # to also reset by clearing the engine.db.connection module-level state.
    try:
        from engine.db import connection as _conn_mod
        for attr in ("_SQLITE_PATH", "_PATH_CACHE"):
            if hasattr(_conn_mod, attr):
                monkeypatch.setattr(_conn_mod, attr, None)
    except Exception:
        pass
    yield db_path


# ---------------------------------------------------------------------------
# A. analyst_session model
# ---------------------------------------------------------------------------


class TestAnalystSessionModel:
    def test_get_returns_none_for_unknown_user(self, tmp_db):
        from engine.models import analyst_session
        assert analyst_session.get("does-not-exist@x.com") is None

    def test_upsert_creates_new_row(self, tmp_db):
        from engine.models import analyst_session
        s = analyst_session.upsert(
            "alice@snowkap.com",
            phase="monthly_review",
            active_company_slug="adani-power",
            active_perspective="cfo",
            activity={"current_action": "reading_insight", "insight_id": "art-1"},
        )
        assert s.user_id == "alice@snowkap.com"
        assert s.phase == "monthly_review"
        assert s.active_company_slug == "adani-power"
        assert s.active_perspective == "cfo"
        assert s.activity["insight_id"] == "art-1"
        assert s.updated_at  # timestamp written

    def test_upsert_updates_existing_row(self, tmp_db):
        from engine.models import analyst_session
        analyst_session.upsert("bob@x.com", phase="monthly_review",
                                active_perspective="cfo")
        # Update only one field; others persist
        s2 = analyst_session.upsert("bob@x.com", active_perspective="ceo")
        assert s2.phase == "monthly_review"  # untouched
        assert s2.active_perspective == "ceo"  # changed

    def test_upsert_only_updates_timestamp_when_no_fields(self, tmp_db):
        from engine.models import analyst_session
        s1 = analyst_session.upsert("c@x.com", phase="monthly_review")
        # Touch only timestamp
        import time
        time.sleep(1)
        s2 = analyst_session.upsert("c@x.com")
        assert s2.phase == "monthly_review"
        assert s2.updated_at >= s1.updated_at

    def test_activity_roundtrip_preserves_dict(self, tmp_db):
        from engine.models import analyst_session
        activity = {"current_action": "reviewing_recs", "insight_id": "abc",
                    "started_at": "2026-05-04T10:00:00+00:00"}
        analyst_session.upsert("d@x.com", activity=activity)
        s = analyst_session.get("d@x.com")
        assert s.activity == activity

    def test_invalid_field_silently_ignored(self, tmp_db):
        from engine.models import analyst_session
        # Unknown field 'bogus' silently ignored (never written to DB)
        analyst_session.upsert("e@x.com", phase="monthly_review", bogus=123)
        s = analyst_session.get("e@x.com")
        assert s.phase == "monthly_review"
        assert not hasattr(s, "bogus")


# ---------------------------------------------------------------------------
# B. Follow-up queue
# ---------------------------------------------------------------------------


class TestFollowUpQueue:
    def test_append_adds_entry(self, tmp_db):
        from engine.models import analyst_session
        s = analyst_session.append_follow_up(
            "f@x.com", "art-001", "needs deeper review", company_slug="adani-power",
        )
        assert len(s.follow_up_queue) == 1
        e = s.follow_up_queue[0]
        assert e["insight_id"] == "art-001"
        assert e["reason"] == "needs deeper review"
        assert e["company_slug"] == "adani-power"
        assert e["marked_at"]

    def test_re_adding_same_insight_dedups_at_head(self, tmp_db):
        from engine.models import analyst_session
        analyst_session.append_follow_up("g@x.com", "art-001", "first reason")
        analyst_session.append_follow_up("g@x.com", "art-002", "different")
        # Re-add art-001 with new reason → moves to head, no duplicate
        s = analyst_session.append_follow_up("g@x.com", "art-001", "updated reason")
        ids = [e["insight_id"] for e in s.follow_up_queue]
        assert ids == ["art-001", "art-002"]
        assert s.follow_up_queue[0]["reason"] == "updated reason"

    def test_remove_follow_up(self, tmp_db):
        from engine.models import analyst_session
        analyst_session.append_follow_up("h@x.com", "art-001")
        analyst_session.append_follow_up("h@x.com", "art-002")
        s = analyst_session.remove_follow_up("h@x.com", "art-001")
        ids = [e["insight_id"] for e in s.follow_up_queue]
        assert ids == ["art-002"]

    def test_queue_capped_at_max(self, tmp_db):
        from engine.models import analyst_session
        max_n = analyst_session.MAX_FOLLOW_UP
        for i in range(max_n + 5):
            analyst_session.append_follow_up("i@x.com", f"art-{i:03d}")
        s = analyst_session.get("i@x.com")
        assert len(s.follow_up_queue) == max_n
        # Newest first
        assert s.follow_up_queue[0]["insight_id"] == f"art-{max_n + 4:03d}"


# ---------------------------------------------------------------------------
# C. API schemas + router
# ---------------------------------------------------------------------------


class TestSessionRouter:
    def test_state_update_accepts_known_phase(self):
        from api.routes.session import StateUpdate
        u = StateUpdate(phase="monthly_review", active_perspective="cfo")
        assert u.phase == "monthly_review"
        assert u.active_perspective == "cfo"

    def test_state_update_rejects_unknown_phase(self):
        from api.routes.session import StateUpdate
        with pytest.raises(Exception):
            StateUpdate(phase="not_a_phase")

    def test_state_update_rejects_unknown_perspective(self):
        from api.routes.session import StateUpdate
        with pytest.raises(Exception):
            StateUpdate(active_perspective="cmo")  # type: ignore[arg-type]

    def test_followup_request_requires_insight_id(self):
        from api.routes.session import FollowUpRequest
        with pytest.raises(Exception):
            FollowUpRequest(insight_id="")  # type: ignore[arg-type]

    def test_router_registered_in_main(self):
        from api.main import app
        paths = {r.path for r in app.routes}
        assert "/api/session/state" in paths
        assert "/api/session/follow-up" in paths
        assert "/api/session/follow-up/{insight_id}" in paths

    def test_router_requires_auth(self):
        from api.routes.session import router
        # Single dependency: require_auth (user_id pulled from claims via
        # get_bearer_claims at endpoint level, not router level)
        assert len(router.dependencies) >= 1


# ---------------------------------------------------------------------------
# D. session_start hook — silent on missing data, never raises
# ---------------------------------------------------------------------------


class TestSessionStartHook:
    def test_compose_banner_returns_string(self):
        from snowkap.hooks.session_start import compose_banner
        banner = compose_banner()
        assert isinstance(banner, str)
        assert "Snowkap analyst banner" in banner
        # Hard cap
        assert len(banner.splitlines()) <= 80

    def test_safe_read_jsonl_tolerates_missing_file(self, tmp_path):
        from snowkap.hooks.session_start import _safe_read_jsonl
        out = _safe_read_jsonl(tmp_path / "nope.jsonl")
        assert out == []

    def test_safe_read_jsonl_skips_corrupt_lines(self, tmp_path):
        from snowkap.hooks.session_start import _safe_read_jsonl
        path = tmp_path / "x.jsonl"
        path.write_text(
            '{"ts": "2026-05-04T10:00:00+00:00", "ok": 1}\n'
            "this is not json\n"
            '{"ts": "2026-05-04T10:01:00+00:00", "ok": 2}\n',
            encoding="utf-8",
        )
        rows = _safe_read_jsonl(path)
        assert len(rows) == 2
        assert all(r.get("ok") in (1, 2) for r in rows)

    def test_recent_filter_drops_old_entries(self):
        from snowkap.hooks.session_start import _recent
        entries = [
            {"ts": "2020-01-01T00:00:00+00:00"},  # ancient
            {"ts": "2099-01-01T00:00:00+00:00"},  # future, should pass
        ]
        recent = _recent(entries, days=7)
        # Only the future one passes the cutoff
        assert recent == [entries[1]]

    def test_compose_banner_swallows_section_errors(self, monkeypatch):
        """Even if every section composer raises, compose_banner returns a
        string with the 'banner composition error' marker — never raises."""
        from snowkap.hooks import session_start as ss
        def _raise(*_a, **_kw): raise RuntimeError("boom")
        for name in ("_section_decisions", "_section_ontology_decisions",
                     "_section_preflight_failures", "_section_discovery_queue",
                     "_section_fuzz"):
            monkeypatch.setattr(ss, name, _raise)
        out = ss.compose_banner()
        assert "banner composition error" in out
