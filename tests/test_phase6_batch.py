"""Phase 6 tests: batch builder, cache, cost estimator — no pytest fixtures."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from engine.analysis import insight_cache as _cache_mod
from engine.analysis import batch_processor as _batch_mod
from engine.analysis.batch_processor import (
    BatchManifest,
    build_insight_batch,
    estimate_batch_cost,
    fetch_batch_results,
    submit_batch,
)
from engine.analysis.insight_cache import (
    CachedSkeleton,
    _make_key,
    cache_stats,
    clear_cache,
    get_skeleton,
    put_skeleton,
)


# ---------------------------------------------------------------------------
# Temp-dir context for cache + batch-dir overrides
# ---------------------------------------------------------------------------


class _TempCacheEnv:
    """Context manager that redirects cache + batch paths to a temp dir."""

    def __enter__(self):
        self._td = tempfile.TemporaryDirectory()
        self.path = Path(self._td.name)
        self._saved_cache = _cache_mod.CACHE_PATH
        self._saved_batch = _batch_mod.BATCH_DIR
        _cache_mod.CACHE_PATH = self.path / "insight_skeletons.json"
        _batch_mod.BATCH_DIR = self.path
        return self

    def __exit__(self, *args):
        _cache_mod.CACHE_PATH = self._saved_cache
        _batch_mod.BATCH_DIR = self._saved_batch
        self._td.cleanup()


# ---------------------------------------------------------------------------
# Cost estimator
# ---------------------------------------------------------------------------


def test_batch_cost_is_half_of_sync():
    cost = estimate_batch_cost(100, avg_tokens_in=2500, avg_tokens_out=2000)
    assert cost["n_articles"] == 100
    assert cost["sync_cost_usd"] > 0
    assert cost["batch_cost_usd"] == cost["sync_cost_usd"] / 2
    assert cost["savings_pct"] == 50


def test_batch_cost_scales_linearly():
    c100 = estimate_batch_cost(100)
    c500 = estimate_batch_cost(500)
    assert abs(c500["batch_cost_usd"] / c100["batch_cost_usd"] - 5.0) < 0.01


# ---------------------------------------------------------------------------
# Insight cache
# ---------------------------------------------------------------------------


def test_cache_key_deterministic():
    k1 = _make_key("climate_disclosure", "event_regulatory_policy", "Power/Energy", "Large Cap")
    k2 = _make_key("climate_disclosure", "event_regulatory_policy", "Power/Energy", "Large Cap")
    assert k1 == k2


def test_cache_key_case_insensitive():
    k1 = _make_key("climate_disclosure", "event_regulatory_policy", "Power/Energy", "Large Cap")
    k2 = _make_key("CLIMATE_DISCLOSURE", "Event_Regulatory_Policy", "power/energy", "LARGE CAP")
    assert k1 == k2


def test_cache_put_get_roundtrip():
    with _TempCacheEnv():
        put_skeleton(
            theme="forced_labour",
            event_type="event_social_violation",
            industry="Renewable Energy",
            cap_tier="Mid Cap",
            typical_frameworks=["BRSR:P5:Q4", "GRI:408"],
            typical_sdg_codes=["8.7", "16.6"],
        )
        found = get_skeleton("forced_labour", "event_social_violation", "Renewable Energy", "Mid Cap")
        assert found is not None
        assert found.typical_frameworks == ["BRSR:P5:Q4", "GRI:408"]
        assert found.typical_sdg_codes == ["8.7", "16.6"]
        assert found.hit_count == 1


def test_cache_miss_returns_none():
    with _TempCacheEnv():
        assert get_skeleton("unknown", "event_x", "Finance", "Small Cap") is None


def test_cache_stale_entry_not_returned():
    with _TempCacheEnv() as env:
        put_skeleton("x", "y", "z", "q", typical_frameworks=["A"])
        raw = json.loads(env.path.joinpath("insight_skeletons.json").read_text(encoding="utf-8"))
        (key,) = raw.keys()
        raw[key]["cached_at"] = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        env.path.joinpath("insight_skeletons.json").write_text(json.dumps(raw), encoding="utf-8")
        assert get_skeleton("x", "y", "z", "q") is None


def test_cache_stats():
    with _TempCacheEnv():
        put_skeleton("a", "b", "c", "d")
        put_skeleton("e", "f", "g", "h")
        stats = cache_stats()
        assert stats["total_entries"] == 2
        assert stats["fresh_entries"] == 2


def test_cache_clear():
    with _TempCacheEnv():
        put_skeleton("a", "b", "c", "d")
        put_skeleton("e", "f", "g", "h")
        n = clear_cache()
        assert n == 2
        assert cache_stats()["total_entries"] == 0


# ---------------------------------------------------------------------------
# Batch builder — produces valid OpenAI Batch API JSONL
# ---------------------------------------------------------------------------


def _make_pipeline_result(idx: int, tier: str = "HOME"):
    result = MagicMock()
    result.rejected = (tier == "REJECTED")
    result.tier = tier
    result.company_slug = "adani-power"
    result.article_id = f"test{idx}"
    result.title = f"Test article {idx}"
    # Provide minimal fields _build_user_prompt may need (we mock it anyway)
    result.nlp = MagicMock()
    result.themes = MagicMock(primary_theme="regulatory_policy")
    result.event = MagicMock(event_id="event_regulatory_policy")
    result.relevance = MagicMock(adjusted_total=7.0, tier="HOME")
    result.causal_chains = []
    result.frameworks = []
    result.risk = None
    result.stakeholders = []
    result.sdgs = []
    return result


def test_batch_builder_produces_valid_jsonl():
    """Each line is valid JSON with required Batch API fields; rejected filtered out."""
    with _TempCacheEnv() as env:
        company = MagicMock()
        company.name = "Adani Power"
        company.industry = "Power/Energy"
        company.market_cap = "Large Cap"
        company.primitive_calibration = {"revenue_cr": 56000, "opex_cr": 38000, "fy_year": "FY25"}
        company.revenue_cr = 56000

        results = [(_make_pipeline_result(i), company) for i in range(3)]
        rejected = _make_pipeline_result(99, tier="REJECTED")
        results.append((rejected, company))

        out_path = env.path / "batch.jsonl"
        with patch("engine.analysis.batch_processor._build_user_prompt", return_value="fake user prompt"):
            path, req_map = build_insight_batch(results, output_path=out_path)

        assert path.exists()
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3  # rejected filtered
        assert len(req_map) == 3

        for line in lines:
            rec = json.loads(line)
            assert rec["method"] == "POST"
            assert rec["url"] == "/v1/chat/completions"
            assert rec["custom_id"].startswith("insight_adani-power_")
            body = rec["body"]
            assert body["model"]
            assert body["response_format"] == {"type": "json_object"}
            assert len(body["messages"]) == 2
            assert body["messages"][0]["role"] == "system"
            assert body["messages"][1]["role"] == "user"


# ---------------------------------------------------------------------------
# Manifest persistence
# ---------------------------------------------------------------------------


def test_manifest_save_load_roundtrip():
    with _TempCacheEnv():
        m = BatchManifest(
            batch_id="batch_test_123",
            input_file_id="file_abc",
            status="submitted",
            submitted_at="2026-04-22T10:00:00+00:00",
            request_map={"c1": {"article_id": "a1", "company_slug": "adani-power"}},
            total_requests=1,
        )
        m.save()
        loaded = BatchManifest.load("batch_test_123")
        assert loaded is not None
        assert loaded.batch_id == "batch_test_123"
        assert loaded.request_map["c1"]["article_id"] == "a1"


def test_manifest_missing_returns_none():
    with _TempCacheEnv():
        assert BatchManifest.load("does_not_exist") is None


# ---------------------------------------------------------------------------
# Batch submit + fetch — mocked OpenAI client
# ---------------------------------------------------------------------------


def test_batch_submit_flow():
    with _TempCacheEnv() as env:
        jsonl = env.path / "in.jsonl"
        jsonl.write_text(
            '{"custom_id": "x", "method": "POST", "url": "/v1/chat/completions", "body": {}}\n',
            encoding="utf-8",
        )

        with patch("engine.analysis.batch_processor.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_file = MagicMock(id="file_123")
            mock_batch = MagicMock(id="batch_456", status="validating")
            mock_client.files.create.return_value = mock_file
            mock_client.batches.create.return_value = mock_batch
            mock_cls.return_value = mock_client

            manifest = submit_batch(jsonl, {"x": {"article_id": "a", "company_slug": "s"}})

        assert manifest.batch_id == "batch_456"
        assert manifest.input_file_id == "file_123"
        assert (env.path / "batch_456_manifest.json").exists()


def test_batch_fetch_parses_results():
    with _TempCacheEnv():
        m = BatchManifest(
            batch_id="b1",
            input_file_id="fin",
            output_file_id="fout",
            status="completed",
            request_map={"insight_adani-power_xyz": {"article_id": "xyz", "company_slug": "adani-power"}},
            total_requests=1,
        )
        m.save()

        fake_output = json.dumps({
            "custom_id": "insight_adani-power_xyz",
            "response": {
                "status_code": 200,
                "body": {
                    "choices": [{
                        "message": {
                            "content": json.dumps({
                                "headline": "Test headline",
                                "impact_score": 7.0,
                                "core_mechanism": "mechanism",
                                "decision_summary": {"materiality": "HIGH"},
                            })
                        }
                    }]
                }
            }
        }) + "\n"

        with patch("engine.analysis.batch_processor.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_file_resp = MagicMock(text=fake_output)
            mock_client.files.content.return_value = mock_file_resp
            mock_cls.return_value = mock_client

            insights = fetch_batch_results("b1")

        assert len(insights) == 1
        ins = insights["insight_adani-power_xyz"]
        assert ins.headline == "Test headline"
        assert ins.impact_score == 7.0
        assert "batch_source:openai_batch_api" in ins.warnings
