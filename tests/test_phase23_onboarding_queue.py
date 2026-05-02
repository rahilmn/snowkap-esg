"""Phase 23 — Onboarding job queue + worker handoff.

Pins the new contract introduced when ``_background_onboard`` was
moved off FastAPI's BackgroundTasks pool and into a dedicated worker
process (``scripts/onboarding_worker.py``):

* The API enqueues onboarding jobs via
  :func:`api.routes.admin_onboard.enqueue_onboarding`, which writes
  exactly one row to the SQLite-backed ``onboard_jobs`` table.
* ``onboard_queue.claim_next`` is atomic — two callers cannot grab
  the same row.
* The worker's ``_run_one`` invokes ``_background_onboard`` and marks
  the job ``done`` on success, ``failed`` on exception. A failure
  must NEVER raise out of the worker loop.

Why these tests matter
----------------------
Pre-Phase-23 a slow yfinance lookup, a NewsAPI 429, or a long
ontology query inside ``_background_onboard`` blocked event-loop
threads inside the API process — login + feed latency degraded for
every other tenant on the worker. The tests below pin the boundary
(API enqueue → SQLite queue → worker drain) so a future refactor
can't quietly re-introduce in-process pipeline execution.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from engine.jobs import onboard_queue


@pytest.fixture(autouse=True)
def _isolated_queue_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the onboard_queue at a per-test SQLite file.

    The Onboarding Worker workflow runs against the production
    ``data/snowkap.db`` file. If these tests wrote to that DB, the
    worker would race-claim our test rows and the FIFO / state-machine
    assertions below would flap. Redirecting ``onboard_queue.DB_PATH``
    to a tmp file gives us deterministic isolation while still
    exercising real SQLite + ``BEGIN IMMEDIATE`` semantics.

    The schema-ready memo on ``onboard_queue`` is also reset so the
    new DB gets its tables created on first use.
    """
    db = tmp_path / "test_onboard_queue.db"
    # `onboard_queue` uses two module-local imports of DB_PATH and
    # _ensure_wal_mode from engine.index.sqlite_index — patch both so
    # neither path leaks back to the real DB.
    monkeypatch.setattr(onboard_queue, "DB_PATH", db)
    monkeypatch.setattr(onboard_queue, "_ensure_wal_mode", lambda: None)
    monkeypatch.setattr(onboard_queue, "_SCHEMA_READY", False)
    yield db


def _wipe_queue() -> None:
    onboard_queue._truncate_all()


def test_enqueue_onboarding_writes_a_queued_row():
    """``enqueue_onboarding`` must persist a row with ``state='queued'``
    plus all the kwargs the worker needs to call ``_background_onboard``."""
    from api.routes.admin_onboard import enqueue_onboarding

    _wipe_queue()
    job_id = enqueue_onboarding(
        slug="phase23-prospect",
        name="Phase 23 Prospect",
        ticker_hint="P23.NS",
        domain="phase23-prospect.test",
        limit=7,
    )
    assert job_id > 0

    job = onboard_queue.get(job_id)
    assert job is not None
    assert job.slug == "phase23-prospect"
    assert job.name == "Phase 23 Prospect"
    assert job.ticker_hint == "P23.NS"
    assert job.domain == "phase23-prospect.test"
    assert job.item_limit == 7
    assert job.state == "queued"
    assert job.attempts == 0
    assert job.error is None


def test_claim_next_is_fifo_and_marks_running():
    """Two queued rows must be drained in insertion order; the second
    ``claim_next`` cannot return the same row as the first."""
    from api.routes.admin_onboard import enqueue_onboarding

    _wipe_queue()
    j1 = enqueue_onboarding(slug="p23-fifo-1", name=None,
                            ticker_hint=None, domain=None, limit=10)
    j2 = enqueue_onboarding(slug="p23-fifo-2", name=None,
                            ticker_hint=None, domain=None, limit=10)

    first = onboard_queue.claim_next(worker_id="test-worker")
    assert first is not None
    assert first.id == j1
    assert first.slug == "p23-fifo-1"
    assert first.state == "running"
    assert first.worker_id == "test-worker"
    assert first.attempts == 1

    second = onboard_queue.claim_next(worker_id="test-worker")
    assert second is not None
    assert second.id == j2
    assert second.slug == "p23-fifo-2"

    # Queue should now be empty for `claim_next`.
    assert onboard_queue.claim_next(worker_id="test-worker") is None


def test_claim_next_skips_running_and_done_rows():
    """A row already in ``running`` or ``done`` state must NOT be
    re-claimed — only ``queued`` rows are eligible."""
    from api.routes.admin_onboard import enqueue_onboarding

    _wipe_queue()
    enqueue_onboarding(slug="p23-claimed", name=None,
                       ticker_hint=None, domain=None, limit=10)
    enqueue_onboarding(slug="p23-fresh", name=None,
                       ticker_hint=None, domain=None, limit=10)

    first = onboard_queue.claim_next()
    assert first is not None and first.slug == "p23-claimed"

    # Mark first as done and add a third — claim_next should skip the
    # running/done rows and pick the next queued one (p23-fresh, then
    # nothing).
    onboard_queue.mark_done(first.id)
    second = onboard_queue.claim_next()
    assert second is not None and second.slug == "p23-fresh"
    assert onboard_queue.claim_next() is None


def test_admin_onboard_route_does_not_run_pipeline_inline():
    """POST /api/admin/onboard must enqueue + return 202 WITHOUT ever
    calling ``_background_onboard``. The worker is the only process
    allowed to invoke the pipeline body."""
    from fastapi.testclient import TestClient
    from api.main import app
    from api.auth_context import SUPER_ADMIN_PERMISSIONS, mint_bearer
    import os

    os.environ.setdefault("JWT_SECRET", "test-secret-xxxxxxxxxxxxxxxxxxxxxx")
    token = mint_bearer({"sub": "sales@snowkap.com",
                         "permissions": list(SUPER_ADMIN_PERMISSIONS)})
    client = TestClient(app)

    _wipe_queue()
    with patch("api.routes.admin_onboard._background_onboard") as mock_bg:
        r = client.post(
            "/api/admin/onboard",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Phase 23 Inline Guard"},
        )

    assert r.status_code == 202, r.text
    # The pipeline body must NEVER run inside the request — that's the
    # whole point of moving onboarding off the event loop.
    mock_bg.assert_not_called()

    # ...and the queue must have a fresh row for the worker to drain.
    assert onboard_queue.queue_depth() >= 1


def test_worker_run_one_marks_done_on_success():
    """``onboarding_worker._run_one`` calls ``_background_onboard`` and
    flips the job to ``done`` when the pipeline returns normally."""
    from scripts import onboarding_worker
    from api.routes.admin_onboard import enqueue_onboarding

    _wipe_queue()
    job_id = enqueue_onboarding(slug="p23-worker-ok", name="OK",
                                ticker_hint=None, domain=None, limit=4)
    job = onboard_queue.claim_next(worker_id="test")
    assert job is not None and job.id == job_id

    with patch("api.routes.admin_onboard._background_onboard",
               return_value=None) as mock_bg:
        onboarding_worker._run_one(job)

    mock_bg.assert_called_once()
    call_kwargs = mock_bg.call_args.kwargs
    assert call_kwargs == {
        "slug": "p23-worker-ok",
        "name": "OK",
        "ticker_hint": None,
        "domain": None,
        "limit": 4,
    }
    final = onboard_queue.get(job_id)
    assert final is not None
    assert final.state == "done"
    assert final.error is None
    assert final.finished_at is not None


def test_worker_run_one_marks_failed_on_exception_and_does_not_raise():
    """An exception inside the pipeline must NOT escape the worker
    loop — it gets recorded on the job row so the next ``claim_next``
    keeps draining the queue."""
    from scripts import onboarding_worker
    from api.routes.admin_onboard import enqueue_onboarding

    _wipe_queue()
    job_id = enqueue_onboarding(slug="p23-worker-boom", name="Boom",
                                ticker_hint=None, domain=None, limit=1)
    job = onboard_queue.claim_next(worker_id="test")
    assert job is not None

    def _explode(**_kwargs):
        raise RuntimeError("yfinance hung")

    with patch("api.routes.admin_onboard._background_onboard",
               side_effect=_explode):
        # Critical: this must NOT raise. The worker must keep going.
        onboarding_worker._run_one(job)

    final = onboard_queue.get(job_id)
    assert final is not None
    assert final.state == "failed"
    assert final.error is not None
    assert "yfinance hung" in final.error
