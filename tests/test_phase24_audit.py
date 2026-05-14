"""Phase 24 — engine/audit.py append/read/concurrency regression tests.

Validates the four append-only JSONL writers:

  * ``append_decision``  → ``decision_log.jsonl``
  * ``append_edit``      → ``ontology_edits.jsonl``
  * ``append_promotion`` → ``promotion_log.jsonl``
  * ``append_preflight`` → ``preflight_log.jsonl``

Plus their tolerant readers, the Toulmin helper, and basic concurrency
safety (multiple threads appending simultaneously).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from engine import audit


# ---------------------------------------------------------------------------
# Per-test isolation — every test gets a fresh data dir
# ---------------------------------------------------------------------------


@pytest.fixture
def audit_dir(tmp_path: Path) -> Path:
    """Tmp dir that engine.audit will treat as the data root."""
    return tmp_path


# ---------------------------------------------------------------------------
# 1. append_decision — basic shape + decision_type fidelity
# ---------------------------------------------------------------------------


class TestAppendDecision:
    def test_writes_required_fields(self, audit_dir: Path):
        audit.append_decision(
            "materiality_downgrade",
            article_id="art-1",
            company_slug="adani-power",
            before={"materiality": "HIGH"},
            after={"materiality": "MODERATE"},
            base_data_dir=audit_dir,
        )
        entries = list(audit.read_decision_log(audit_dir))
        assert len(entries) == 1
        e = entries[0]
        assert e["decision_type"] == "materiality_downgrade"
        assert e["article_id"] == "art-1"
        assert e["company_slug"] == "adani-power"
        assert e["before"] == {"materiality": "HIGH"}
        assert e["after"] == {"materiality": "MODERATE"}
        assert e["automated"] is True
        assert "ts" in e and e["ts"].endswith("+00:00")

    def test_carries_toulmin_block(self, audit_dir: Path):
        toulmin = audit.make_toulmin(
            claim="materiality reduced from HIGH to MODERATE",
            grounds=["positive event with negative narrative framing"],
            warrant="coherence verifier (Phase 12.4)",
            qualifier="confidence ~0.85",
            rebuttal="if a follow-up regulator action surfaces, re-elevate",
        )
        audit.append_decision(
            "coherence_warning_applied",
            article_id="art-2",
            toulmin=toulmin,
            base_data_dir=audit_dir,
        )
        entry = next(audit.read_decision_log(audit_dir))
        assert entry["toulmin"]["claim"].startswith("materiality reduced")
        assert entry["toulmin"]["rebuttal"].startswith("if a follow-up")
        assert len(entry["toulmin"]["grounds"]) == 1

    def test_user_id_marks_human_decisions(self, audit_dir: Path):
        audit.append_decision(
            "do_nothing_recommended",
            article_id="art-3",
            user_id="analyst@snowkap.com",
            automated=False,
            base_data_dir=audit_dir,
        )
        entry = next(audit.read_decision_log(audit_dir))
        assert entry["automated"] is False
        assert entry["user_id"] == "analyst@snowkap.com"

    def test_extra_field_passes_through(self, audit_dir: Path):
        audit.append_decision(
            "hallucination_audit_fired",
            article_id="art-4",
            extra={"unsupported_claims": 4, "auto_downgraded": True},
            base_data_dir=audit_dir,
        )
        entry = next(audit.read_decision_log(audit_dir))
        assert entry["extra"]["unsupported_claims"] == 4


# ---------------------------------------------------------------------------
# 2. append_edit — TTL/config edits with diff + Toulmin
# ---------------------------------------------------------------------------


class TestAppendEdit:
    def test_records_target_and_hashes(self, audit_dir: Path):
        audit.append_edit(
            "ontology_ttl_edit",
            target_path="data/ontology/knowledge_expansion.ttl",
            before_hash="abc123",
            after_hash="def456",
            diff_summary="+1 HeadlineRule for CFO water risk",
            user_id="ontology-admin@snowkap.com",
            base_data_dir=audit_dir,
        )
        entry = next(audit.read_ontology_edits(audit_dir))
        assert entry["edit_type"] == "ontology_ttl_edit"
        assert entry["target_path"].endswith("knowledge_expansion.ttl")
        assert entry["before_hash"] == "abc123"
        assert entry["after_hash"] == "def456"
        assert entry["automated"] is False  # edits default to human-driven

    def test_requires_target_path(self, audit_dir: Path):
        with pytest.raises(TypeError):
            audit.append_edit("ontology_ttl_edit", base_data_dir=audit_dir)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# 3. append_promotion — self-evolving ontology decisions
# ---------------------------------------------------------------------------


class TestAppendPromotion:
    def test_promote_decision(self, audit_dir: Path):
        audit.append_promotion(
            "promote",
            candidate_id="entity::SEBI",
            category="entity",
            candidate_payload={"label": "SEBI", "type": "Regulator"},
            confidence=0.85,
            toulmin=audit.make_toulmin(
                claim="SEBI auto-promotes (Tier-1 regulator)",
                grounds=["3 articles", "2 sources", "confidence 0.85"],
                warrant="Phase 19 Tier-1 framework auto-promotion rule",
                rebuttal="if confidence drops below 0.7 on re-evaluation, demote",
            ),
            base_data_dir=audit_dir,
        )
        entry = next(audit.read_promotion_log(audit_dir))
        assert entry["decision"] == "promote"
        assert entry["category"] == "entity"
        assert entry["confidence"] == 0.85
        assert entry["candidate_payload"]["label"] == "SEBI"

    def test_reject_and_defer_supported(self, audit_dir: Path):
        for decision in ("reject", "defer"):
            audit.append_promotion(
                decision,  # type: ignore[arg-type]
                candidate_id=f"weight::test_{decision}",
                category="weight",
                candidate_payload={},
                base_data_dir=audit_dir,
            )
        entries = list(audit.read_promotion_log(audit_dir))
        assert {e["decision"] for e in entries} == {"reject", "defer"}


# ---------------------------------------------------------------------------
# 4. append_preflight — CFO-credibility gate logging (W3 wires this)
# ---------------------------------------------------------------------------


class TestAppendPreflight:
    def test_pass_and_fail_recorded_separately(self, audit_dir: Path):
        audit.append_preflight(
            "financial_impact_quantified",
            article_id="art-w3-1",
            company_slug="icici-bank",
            passed=True,
            base_data_dir=audit_dir,
        )
        audit.append_preflight(
            "no_stale_data",
            article_id="art-w3-1",
            company_slug="icici-bank",
            passed=False,
            reason="published_at is 95 days ago, freshness window is 30d for regulatory",
            base_data_dir=audit_dir,
        )
        entries = list(audit.read_preflight_log(audit_dir))
        assert len(entries) == 2
        gates = {e["gate"]: e["passed"] for e in entries}
        assert gates == {"financial_impact_quantified": True, "no_stale_data": False}
        fail_entry = next(e for e in entries if not e["passed"])
        assert "freshness window" in fail_entry["reason"]

    def test_default_perspective_is_cfo(self, audit_dir: Path):
        audit.append_preflight(
            "framework_mapped",
            article_id="art-w3-2",
            company_slug="adani-power",
            passed=True,
            base_data_dir=audit_dir,
        )
        entry = next(audit.read_preflight_log(audit_dir))
        assert entry["perspective"] == "cfo"


# ---------------------------------------------------------------------------
# 5. Reader tolerance — corrupt lines must skip, not crash
# ---------------------------------------------------------------------------


class TestReaderTolerance:
    def test_corrupt_line_is_skipped(self, audit_dir: Path):
        # Append a good entry, then a corrupt one, then another good one
        audit.append_decision(
            "tier_shift",
            article_id="art-r-1",
            base_data_dir=audit_dir,
        )
        log_path = audit_dir / "audit" / audit.DECISION_LOG
        log_path.open("a", encoding="utf-8").write("{not valid json\n")
        audit.append_decision(
            "tier_shift",
            article_id="art-r-2",
            base_data_dir=audit_dir,
        )
        entries = list(audit.read_decision_log(audit_dir))
        # Corrupt line skipped — both good entries returned
        assert len(entries) == 2
        assert {e["article_id"] for e in entries} == {"art-r-1", "art-r-2"}

    def test_missing_file_returns_empty(self, audit_dir: Path):
        # Nothing written yet; reading must not raise
        assert list(audit.read_decision_log(audit_dir)) == []
        assert list(audit.read_ontology_edits(audit_dir)) == []
        assert list(audit.read_promotion_log(audit_dir)) == []
        assert list(audit.read_preflight_log(audit_dir)) == []


# ---------------------------------------------------------------------------
# 6. Concurrency — multi-thread appends must not lose or interleave entries
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_multi_thread_append_no_loss(self, audit_dir: Path):
        N_THREADS = 8
        N_PER_THREAD = 25

        def worker(tid: int):
            for i in range(N_PER_THREAD):
                audit.append_decision(
                    "tier_shift",
                    article_id=f"thread-{tid}-art-{i}",
                    base_data_dir=audit_dir,
                )

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        entries = list(audit.read_decision_log(audit_dir))
        assert len(entries) == N_THREADS * N_PER_THREAD, (
            f"expected {N_THREADS * N_PER_THREAD} entries, got {len(entries)}"
        )
        # Every entry parses cleanly (no interleaved partial lines)
        ids = {e["article_id"] for e in entries}
        assert len(ids) == N_THREADS * N_PER_THREAD


# ---------------------------------------------------------------------------
# 7. JSONL discipline — file is grep-able / jq-able line-by-line
# ---------------------------------------------------------------------------


class TestJsonlDiscipline:
    def test_each_entry_is_one_line(self, audit_dir: Path):
        for i in range(5):
            audit.append_decision(
                "do_nothing_recommended",
                article_id=f"art-jsonl-{i}",
                # multi-line payload to test that JSON encoder doesn't insert
                # newlines mid-record
                extra={"deep": {"nested": {"structure": [1, 2, 3, 4, 5]}}},
                base_data_dir=audit_dir,
            )
        log_path = audit_dir / "audit" / audit.DECISION_LOG
        raw = log_path.read_text(encoding="utf-8")
        lines = [l for l in raw.split("\n") if l.strip()]
        assert len(lines) == 5
        for line in lines:
            # Each line is a complete JSON object
            obj = json.loads(line)
            assert obj["decision_type"] == "do_nothing_recommended"


# ---------------------------------------------------------------------------
# 8. make_toulmin helper — required + optional field handling
# ---------------------------------------------------------------------------


class TestMakeToulmin:
    def test_required_fields_only(self):
        t = audit.make_toulmin(
            claim="X",
            grounds=["g1", "g2"],
            warrant="W",
        )
        assert t == {"claim": "X", "grounds": ["g1", "g2"], "warrant": "W"}
        assert "qualifier" not in t
        assert "rebuttal" not in t

    def test_optional_fields_included_when_provided(self):
        t = audit.make_toulmin(
            claim="X",
            grounds=["g"],
            warrant="W",
            qualifier="Q",
            rebuttal="R",
        )
        assert t["qualifier"] == "Q"
        assert t["rebuttal"] == "R"
