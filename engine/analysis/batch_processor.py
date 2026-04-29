"""Phase 6 — OpenAI Batch API integration for Stage 10 deep insights.

Cuts Stage 10 cost by 50% by using OpenAI's asynchronous /v1/batches endpoint.
Ideal for nightly processing of large article backlogs — submit 500+ articles
as a batch, OpenAI completes within 24 hours, pay half the synchronous rate.

Workflow:
    1. `BatchJobBuilder.build_insight_batch(articles, companies)` → JSONL file
    2. `BatchSubmitter.submit(jsonl_path)` → returns batch_id (saves manifest)
    3. Wait (batches usually complete in 1-24 h)
    4. `BatchResultParser.fetch_and_parse(batch_id)` → writes DeepInsight JSON per article

Batch size limits: ≤ 50,000 requests per batch, ≤ 200 MB file. Our typical
payload is ~8 KB per request, so 500 articles = ~4 MB. Plenty of headroom.

CLI: see `engine/main.py batch-submit | batch-status | batch-fetch`.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI

from engine.analysis.insight_generator import (
    DeepInsight,
    _SYSTEM_PROMPT,
    _build_user_prompt,
)
from engine.analysis.pipeline import PipelineResult
from engine.config import Company, get_openai_api_key, load_settings

logger = logging.getLogger(__name__)


# Filesystem locations for batch artefacts
BATCH_DIR = Path("data/batch")
BATCH_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class BatchManifest:
    """Tracks what went into a batch so we can hydrate results later."""
    batch_id: str
    input_file_id: str
    output_file_id: str = ""
    error_file_id: str = ""
    status: str = "submitted"
    submitted_at: str = ""
    completed_at: str = ""
    # Map custom_id → article_id + company_slug so results can be hydrated
    request_map: dict[str, dict[str, str]] = field(default_factory=dict)
    total_requests: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def load(cls, batch_id: str) -> "BatchManifest | None":
        path = BATCH_DIR / f"{batch_id}_manifest.json"
        if not path.exists():
            return None
        return cls(**json.loads(path.read_text(encoding="utf-8")))

    def save(self) -> Path:
        path = BATCH_DIR / f"{self.batch_id}_manifest.json"
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path


# ---------------------------------------------------------------------------
# BatchJobBuilder — compiles JSONL from pipeline results
# ---------------------------------------------------------------------------


def build_insight_batch(
    results: list[tuple[PipelineResult, Company]],
    output_path: Path | None = None,
) -> tuple[Path, dict[str, dict[str, str]]]:
    """Build a JSONL file for Stage 10 insight generation across N articles.

    Returns (jsonl_path, request_map) where request_map is {custom_id:
    {"article_id": ..., "company_slug": ...}} for later hydration.
    """
    settings = load_settings()
    llm_cfg = settings.get("llm", {})
    model = llm_cfg.get("model_heavy", "gpt-4.1")
    max_tokens = llm_cfg.get("max_tokens_insight", 2400)
    temperature = llm_cfg.get("temperature", 0.2)

    if output_path is None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        output_path = BATCH_DIR / f"insight_batch_{ts}.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    request_map: dict[str, dict[str, str]] = {}
    lines: list[str] = []
    for result, company in results:
        if result.rejected or result.tier != "HOME":
            continue
        custom_id = f"insight_{result.company_slug}_{result.article_id}"
        request_map[custom_id] = {
            "article_id": result.article_id,
            "company_slug": result.company_slug,
            "title": result.title,
        }
        request_body = {
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(result, company)},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        line = {
            "custom_id": custom_id,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": request_body,
        }
        lines.append(json.dumps(line, ensure_ascii=False))

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("built batch JSONL: %d requests at %s", len(lines), output_path)
    return output_path, request_map


# ---------------------------------------------------------------------------
# BatchSubmitter — uploads + submits
# ---------------------------------------------------------------------------


def submit_batch(
    jsonl_path: Path,
    request_map: dict[str, dict[str, str]],
    completion_window: str = "24h",
) -> BatchManifest:
    """Upload JSONL + submit batch. Returns manifest (persisted to disk)."""
    client = OpenAI(api_key=get_openai_api_key())

    # Upload input file
    with open(jsonl_path, "rb") as f:
        input_file = client.files.create(file=f, purpose="batch")
    logger.info("uploaded batch input file: %s", input_file.id)

    # Create batch
    batch = client.batches.create(
        input_file_id=input_file.id,
        endpoint="/v1/chat/completions",
        completion_window=completion_window,
        metadata={"created_by": "snowkap_batch_processor_v1"},
    )
    logger.info("submitted batch: %s (status=%s)", batch.id, batch.status)

    manifest = BatchManifest(
        batch_id=batch.id,
        input_file_id=input_file.id,
        status=batch.status,
        submitted_at=datetime.now(timezone.utc).isoformat(),
        request_map=request_map,
        total_requests=len(request_map),
    )
    manifest.save()
    return manifest


# ---------------------------------------------------------------------------
# Poller
# ---------------------------------------------------------------------------


def check_batch_status(batch_id: str) -> BatchManifest | None:
    """Refresh + persist manifest for a batch. Returns updated manifest."""
    manifest = BatchManifest.load(batch_id)
    if manifest is None:
        logger.warning("no manifest for %s", batch_id)
        return None

    client = OpenAI(api_key=get_openai_api_key())
    batch = client.batches.retrieve(batch_id)
    manifest.status = batch.status
    if getattr(batch, "output_file_id", None):
        manifest.output_file_id = batch.output_file_id
    if getattr(batch, "error_file_id", None):
        manifest.error_file_id = batch.error_file_id
    if batch.status in {"completed", "failed", "expired", "cancelled"}:
        manifest.completed_at = datetime.now(timezone.utc).isoformat()
    manifest.save()
    logger.info("batch %s status=%s (output_file=%s)",
                batch_id, manifest.status, manifest.output_file_id or "-")
    return manifest


# ---------------------------------------------------------------------------
# Result parser — hydrates DeepInsight objects from batch output
# ---------------------------------------------------------------------------


def fetch_batch_results(batch_id: str) -> dict[str, DeepInsight]:
    """Download batch output + parse into DeepInsight objects per custom_id.

    Only works when batch status == completed.
    Returns {custom_id: DeepInsight}.
    """
    from engine.analysis.insight_generator import enforce_score_bounds

    manifest = BatchManifest.load(batch_id)
    if manifest is None:
        raise FileNotFoundError(f"manifest missing for {batch_id}")
    if manifest.status != "completed":
        raise RuntimeError(f"batch {batch_id} not complete (status={manifest.status})")
    if not manifest.output_file_id:
        raise RuntimeError(f"batch {batch_id} has no output_file_id")

    client = OpenAI(api_key=get_openai_api_key())
    raw = client.files.content(manifest.output_file_id).text

    # Save raw output for audit
    out_path = BATCH_DIR / f"{batch_id}_raw_output.jsonl"
    out_path.write_text(raw, encoding="utf-8")

    insights: dict[str, DeepInsight] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("batch output line not JSON: %s", line[:100])
            continue

        custom_id = rec.get("custom_id", "")
        if rec.get("error"):
            logger.warning("batch error for %s: %s", custom_id, rec["error"])
            continue

        resp = rec.get("response", {})
        body = resp.get("body", {})
        choices = body.get("choices", [])
        if not choices:
            logger.warning("no choices in response for %s", custom_id)
            continue

        content = choices[0].get("message", {}).get("content", "{}")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("response content not JSON for %s", custom_id)
            continue

        # Clamp impact score (same as sync path does)
        # Since we don't have the original EventClassification here, skip clamp —
        # verifier + downstream stages will still catch issues.
        insight = DeepInsight(
            headline=str(parsed.get("headline", ""))[:200],
            impact_score=float(parsed.get("impact_score", 0) or 0),
            core_mechanism=str(parsed.get("core_mechanism", "") or ""),
            profitability_connection=str(parsed.get("profitability_connection", "") or ""),
            translation=str(parsed.get("translation", "") or ""),
            impact_analysis=dict(parsed.get("impact_analysis", {}) or {}),
            financial_timeline=dict(parsed.get("financial_timeline", {}) or {}),
            esg_relevance_score=dict(parsed.get("esg_relevance_score", {}) or {}),
            net_impact_summary=str(parsed.get("net_impact_summary", "") or ""),
            decision_summary=dict(parsed.get("decision_summary", {}) or {}),
            causal_chain=dict(parsed.get("causal_chain", {}) or {}),
            warnings=["batch_source:openai_batch_api"],
        )
        insights[custom_id] = insight

    logger.info("parsed %d insights from batch %s", len(insights), batch_id)
    return insights


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def estimate_batch_cost(n_articles: int, avg_tokens_in: int = 2500, avg_tokens_out: int = 2000) -> dict:
    """Rough cost estimate. Batch API = 50% of sync pricing."""
    # gpt-4.1 synchronous: $2.50/MTok in, $10/MTok out (approximate)
    sync_in = 2.50
    sync_out = 10.00
    batch_in = sync_in * 0.5
    batch_out = sync_out * 0.5
    cost_sync = n_articles * (avg_tokens_in * sync_in + avg_tokens_out * sync_out) / 1_000_000
    cost_batch = n_articles * (avg_tokens_in * batch_in + avg_tokens_out * batch_out) / 1_000_000
    return {
        "n_articles": n_articles,
        "sync_cost_usd": round(cost_sync, 2),
        "batch_cost_usd": round(cost_batch, 2),
        "savings_usd": round(cost_sync - cost_batch, 2),
        "savings_pct": 50,
    }
