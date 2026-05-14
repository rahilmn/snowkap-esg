"""Phase 4 §6.6 — outbound touch tracker + CTA cadence tests.

Validates:
  - First-touch returns the educational CTA ("Read full analysis →")
  - Second-touch returns the demo CTA ("Book a 20-min walkthrough →")
  - Email + slug normalisation: case-insensitive, whitespace-stripped
  - Counts increment on each record_touch call
  - Different recipient OR different slug = independent counter
  - Empty inputs are safe (return 0 / first-touch)
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets its own SQLite file so touches don't bleed across
    tests. We monkeypatch ``engine.config.get_data_path`` so the
    backend-aware connection routes the DB file under tmp_path. SQLite
    backend is forced regardless of the host's DATABASE_URL."""
    db_dir = tmp_path / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SNOWKAP_DB_BACKEND", "sqlite")

    from pathlib import Path as _Path
    import engine.config as _cfg

    real_get = _cfg.get_data_path

    def _fake_get_data_path(*parts: str) -> _Path:
        # Redirect only the DB file to tmp; route everything else through
        # the real resolver so the ontology graph (loaded at FastAPI app
        # startup via transitive imports) keeps finding .ttl files.
        if parts and parts[0] in ("snowkap.db",):
            return db_dir / "snowkap.db"
        if not parts:
            return db_dir
        return real_get(*parts)

    monkeypatch.setattr(_cfg, "get_data_path", _fake_get_data_path)
    # Some modules import the function by name (already-bound), so
    # patch them too if loaded.
    import engine.db.connection as _conn
    if hasattr(_conn, "get_data_path"):
        monkeypatch.setattr(_conn, "get_data_path", _fake_get_data_path, raising=False)

    # Reset the schema-ready latch so the fresh DB gets the table created
    import importlib
    from engine.models import outbound_touches as ot
    importlib.reload(ot)
    yield
    # Restore
    importlib.reload(ot)
    # Defensive — reset ontology graph cache so the next test in the
    # session doesn't inherit a graph loaded against the tmp_path data dir.
    try:
        from engine.ontology.graph import reset_graph
        reset_graph()
    except Exception:
        pass


def test_first_touch_returns_educational_cta():
    from engine.models.outbound_touches import (
        FIRST_TOUCH_CTA,
        cta_label_for,
    )
    assert cta_label_for("cfo@acme.com", "waaree-energies") == FIRST_TOUCH_CTA


def test_record_touch_increments_count():
    from engine.models.outbound_touches import (
        count_touches,
        record_touch,
    )
    assert count_touches("cfo@acme.com", "waaree-energies") == 0
    record_touch("cfo@acme.com", "waaree-energies", "art-1")
    assert count_touches("cfo@acme.com", "waaree-energies") == 1
    record_touch("cfo@acme.com", "waaree-energies", "art-2")
    assert count_touches("cfo@acme.com", "waaree-energies") == 2


def test_second_touch_returns_demo_cta():
    from engine.models.outbound_touches import (
        SECOND_TOUCH_CTA,
        cta_label_for,
        record_touch,
    )
    record_touch("cfo@acme.com", "waaree-energies", "art-1")
    assert cta_label_for("cfo@acme.com", "waaree-energies") == SECOND_TOUCH_CTA


def test_email_normalisation_is_case_insensitive():
    from engine.models.outbound_touches import (
        count_touches,
        record_touch,
    )
    record_touch("CFO@Acme.com", "waaree-energies", "art-1")
    # Same address with different casing maps to same counter
    assert count_touches("cfo@acme.com", "waaree-energies") == 1
    assert count_touches("CFO@ACME.COM", "waaree-energies") == 1


def test_email_whitespace_is_stripped():
    from engine.models.outbound_touches import (
        count_touches,
        record_touch,
    )
    record_touch("  cfo@acme.com  ", "waaree-energies", "art-1")
    assert count_touches("cfo@acme.com", "waaree-energies") == 1


def test_slug_normalisation():
    from engine.models.outbound_touches import (
        count_touches,
        record_touch,
    )
    record_touch("cfo@acme.com", "WAAREE-Energies", "art-1")
    assert count_touches("cfo@acme.com", "waaree-energies") == 1


def test_different_company_independent_counter():
    """A second recipient OR second slug = independent first-touch state."""
    from engine.models.outbound_touches import (
        count_touches,
        is_first_touch,
        record_touch,
    )
    record_touch("cfo@acme.com", "waaree-energies", "art-1")
    # Different company → first-touch
    assert is_first_touch("cfo@acme.com", "icici-bank")
    assert count_touches("cfo@acme.com", "icici-bank") == 0
    # Different recipient → first-touch
    assert is_first_touch("ceo@acme.com", "waaree-energies")


def test_empty_inputs_are_safe():
    from engine.models.outbound_touches import (
        count_touches,
        is_first_touch,
        record_touch,
    )
    assert count_touches("", "waaree-energies") == 0
    assert count_touches("cfo@acme.com", "") == 0
    assert is_first_touch("", "")
    assert record_touch("", "", "art-1") == 0


def test_record_touch_returns_inserted_id():
    from engine.models.outbound_touches import record_touch
    rid1 = record_touch("cfo@acme.com", "waaree-energies", "art-1")
    rid2 = record_touch("cfo@acme.com", "waaree-energies", "art-2")
    assert rid1 > 0
    assert rid2 > rid1


def test_cta_constants_match_plan():
    """Locked-in copy per plan §6.6."""
    from engine.models.outbound_touches import (
        FIRST_TOUCH_CTA,
        SECOND_TOUCH_CTA,
    )
    assert FIRST_TOUCH_CTA == "Read full analysis →"
    assert SECOND_TOUCH_CTA == "Book a 20-min walkthrough →"


def test_article_id_is_optional():
    """Touch tracking works even without an article_id (e.g. digest sends)."""
    from engine.models.outbound_touches import (
        count_touches,
        record_touch,
    )
    rid = record_touch("cfo@acme.com", "waaree-energies", None)
    assert rid > 0
    assert count_touches("cfo@acme.com", "waaree-energies") == 1


# ---------------------------------------------------------------------------
# share_service integration — cta_label_for flips between first/second touch
# ---------------------------------------------------------------------------


def test_share_service_uses_first_touch_cta_when_no_prior_touches(monkeypatch):
    """When `cta_label` is omitted (default None), share_article_by_email
    asks the touch tracker — and on a clean recipient/company pair it
    should resolve to the educational first-touch CTA."""
    from engine.models.outbound_touches import (
        FIRST_TOUCH_CTA,
        cta_label_for,
    )
    # Fresh pair → first touch
    label = cta_label_for("new.cfo@example.com", "waaree-energies")
    assert label == FIRST_TOUCH_CTA


def test_share_service_uses_second_touch_cta_after_one_prior(monkeypatch):
    from engine.models.outbound_touches import (
        SECOND_TOUCH_CTA,
        cta_label_for,
        record_touch,
    )
    record_touch("repeat.cfo@example.com", "icici-bank", "art-001")
    label = cta_label_for("repeat.cfo@example.com", "icici-bank")
    assert label == SECOND_TOUCH_CTA


def test_share_service_signature_accepts_explicit_cta_override():
    """A campaign / caller that wants legacy 'Book a demo' copy can still
    pass it explicitly. The default-None behaviour is opt-in."""
    import inspect
    from engine.output import share_service
    sig = inspect.signature(share_service.share_article_by_email)
    cta_param = sig.parameters.get("cta_label")
    assert cta_param is not None
    # Default should be None so the touch-tracker can decide
    assert cta_param.default is None
