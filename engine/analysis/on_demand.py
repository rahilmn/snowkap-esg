"""On-demand enrichment — Phase 14 hybrid intelligence.

Pre-processing at ingestion handles stages 1-9 (cheap, ontology-driven).
This module runs stages 10-12 on demand when a user clicks an article:
  - Stage 10: Deep insight generation (gpt-4.1)
  - Stage 11: Perspective transformation (ontology, no LLM)
  - Stage 12: REREACT recommendations (gpt-4.1-mini x3)
  + Phase I LLM intelligence layers (competitive brief, causal narrative,
    executive Q&A, sentiment trajectory)

Results are cached to disk so subsequent opens are instant.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from engine.config import Company, get_company, get_data_path
from engine.index.sqlite_index import resolve_slug

logger = logging.getLogger(__name__)


# Phase 39 — bumped 3.2-template-hardened -> 3.3-editorial-lede so
# every company's persisted insights auto-re-enrich on next view and
# pick up the new analysis.lede block (2-3 sentence editorial story
# opener that sits above WHAT CHANGED in the email + /now article
# sheet + chat seed). Files at 3.0/3.1/3.2/2.x remain readable; the
# schema gate just triggers a fresh pipeline run.
CURRENT_SCHEMA_VERSION = "3.3-editorial-lede"


def enrich_on_demand(
    article_id: str, company_slug: str, force: bool = False
) -> dict[str, Any] | None:
    """Run deep enrichment for an article on demand.

    Args:
        force: If True, re-run enrichment even if already cached (for articles
               processed with old prompts that need primitive-enriched re-analysis).

    Returns the full enriched payload dict, or None on failure.
    """
    from engine.analysis.ceo_narrative_generator import generate_ceo_narrative_perspective
    from engine.analysis.esg_analyst_generator import generate_esg_analyst_perspective
    from engine.analysis.insight_generator import generate_deep_insight
    from engine.analysis.perspective_engine import transform_for_perspective
    from engine.analysis.pipeline import PipelineResult
    from engine.analysis.recommendation_engine import generate_recommendations
    from engine.output.writer import write_insight

    started = time.perf_counter()

    # 1. Find the article JSON. Stub-row fallback: when nothing is on
    #    disk under outputs/, this is a first-time enrichment driven by
    #    a swipe-up on a non-critical / rejected article. We still
    #    proceed by reading the raw input via `_rerun_full_pipeline` in
    #    step 4 below.
    insights_dir = get_data_path("outputs", company_slug, "insights")
    candidates = list(insights_dir.glob(f"*{article_id}*"))
    json_path = candidates[0] if candidates else None
    first_time_enrich = json_path is None

    # 2. Load existing payload (or empty when first-time enrich).
    if first_time_enrich:
        logger.info(
            "enrich_on_demand: first-time enrich for %s/%s (no insight on disk)",
            company_slug, article_id,
        )
        payload = {}
        pipeline_data = {}
        existing_insight = {}
    else:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        pipeline_data = payload.get("pipeline") or {}
        existing_insight = payload.get("insight") or {}

    # 3. Check if already enriched with CURRENT engine version
    # Module-level constant — see top of file. Importable so the legacy_adapter
    # cached-check stays aligned.
    stored_version = (payload.get("meta") or {}).get("schema_version", "")
    is_current = stored_version == CURRENT_SCHEMA_VERSION

    # Phase 36 — auto-reenrich trigger. When the retry cron successfully
    # backfills body for an article whose insight was previously generated
    # with headline_only=True, it stamps `meta.body_grounded_pending: True`
    # on the insight JSON. Detecting this flag forces a re-enrichment so
    # the next view picks up the new body-grounded analysis without
    # waiting for a manual force=True call.
    body_grounded_pending = bool((payload.get("meta") or {}).get("body_grounded_pending"))

    if (
        not force
        and not body_grounded_pending
        and is_current
        and existing_insight.get("headline")
        and existing_insight.get("core_mechanism")
    ):
        logger.info("enrich_on_demand: %s already enriched (v%s), returning cached", article_id, stored_version)
        return payload

    if body_grounded_pending:
        logger.info(
            "enrich_on_demand: %s flagged body_grounded_pending — "
            "forcing re-enrich against new body", article_id,
        )

    # Phase 30 — per-tenant LLM daily cap. We only check BEFORE firing
    # new Stages 10-12 (cached returns above are free). Fails open on
    # any tracking error so a DB hiccup doesn't block paying tenants.
    try:
        from engine.llm.budget import assert_under_cap, TenantBudgetExceeded
        assert_under_cap(company_slug)
    except TenantBudgetExceeded as exc:
        logger.warning(
            "enrich_on_demand: daily cap reached for %s — spent $%.2f of $%.2f cap",
            company_slug, exc.spent, exc.cap,
        )
        # Stamp the cap status on the existing payload so the UI can
        # show a friendly "Daily limit reached, try again tomorrow"
        # banner instead of an infinite spinner.
        from datetime import datetime, timezone
        payload.setdefault("meta", {})["budget_cap_reached"] = {
            "spent_usd": exc.spent,
            "cap_usd": exc.cap,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        return payload

    # 4. Get company and build PipelineResult
    company = get_company(company_slug)

    # 4a. Always re-run full pipeline (stages 1-9) from raw input article
    #     to pick up latest event keywords, ontology edges, and primitive mappings.
    result = _rerun_full_pipeline(article_id, company_slug, company)

    # 4b. Fall back to reconstructing from stored pipeline data if input not found
    if result is None:
        result = _reconstruct_pipeline_result(pipeline_data)

    if result.rejected:
        # Phase 22.1 — when re-analysis rejects (e.g. cross-entity gate
        # fires), persist the new rejection to disk so the dashboard
        # surfaces correctly. Pre-fix the OLD analysis stayed cached
        # forever even though the article's tier should now be REJECTED.
        # write_insight() also calls upsert_article() to refresh the
        # SQLite index so the article drops out of company-feed queries.
        logger.info(
            "enrich_on_demand: %s REJECTED on re-analysis (%s) — persisting",
            article_id, result.rejection_reason,
        )
        try:
            write_insight(result, None, {}, None)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "enrich_on_demand: failed to persist rejection for %s: %s",
                article_id, exc,
            )
        return payload

    # 4c. Check content quality — flag paywall/thin articles
    # Check both raw content and NLP extraction quality
    raw_content_len = len(result.title or "")
    if hasattr(result, "nlp") and result.nlp:
        raw_content_len += len(result.nlp.narrative_core_claim or "")
        raw_content_len += len(result.nlp.narrative_implied_causation or "")
    if raw_content_len < 100:
        logger.warning(
            "enrich_on_demand: thin content (%d chars) for %s — likely paywalled",
            content_len, article_id,
        )
        result._thin_content = True  # type: ignore[attr-defined]
    else:
        result._thin_content = False  # type: ignore[attr-defined]

    # 5. Stage 10: Deep insight generation
    logger.info("enrich_on_demand: generating deep insight for %s", article_id)
    insight = generate_deep_insight(result, company)
    if not insight:
        logger.warning("enrich_on_demand: insight generation failed for %s", article_id)
        return payload

    # 6. Stage 11: Perspective transformation
    # Phase 17 fix — mirror the ingest path (engine/main.py::_run_article).
    # Pre-fix the on-demand path used the legacy `transform_for_perspective`
    # for ALL THREE lenses, producing thin ESG Analyst + CEO panels missing
    # stakeholder_map / kpi_table / audit_trail / three_year_trajectory.
    # The ingest path correctly uses the Phase 4 dedicated generators for
    # ESG Analyst + CEO and only uses the legacy transform for CFO.
    perspectives: dict[str, Any] = {}
    try:
        perspectives["esg-analyst"] = generate_esg_analyst_perspective(insight, result, company)
    except Exception as exc:  # noqa: BLE001
        logger.warning("esg_analyst generator failed (%s); falling back to legacy transform", exc)
        perspectives["esg-analyst"] = transform_for_perspective(insight, result, "esg-analyst")
    try:
        perspectives["ceo"] = generate_ceo_narrative_perspective(insight, result, company)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ceo_narrative generator failed (%s); falling back to legacy transform", exc)
        perspectives["ceo"] = transform_for_perspective(insight, result, "ceo")
    perspectives["cfo"] = transform_for_perspective(insight, result, "cfo")

    # 7. Stage 12: Recommendations
    recs = generate_recommendations(insight, result, company)

    # 7.5 Phase 24 W3 — CFO-credibility preflight gating.
    # Six independent gates run on the parsed insight + perspectives.
    # If ANY gate fails, the article is hidden from the CFO surface
    # (still visible on ESG Analyst surface). Each gate is logged to
    # data/audit/preflight_log.jsonl.
    preflight_status = "PASS"
    try:
        from engine.analysis.cfo_preflight import run_preflight
        framework_codes = []
        try:
            framework_codes = [
                getattr(fm, "framework_id", "") or getattr(fm, "id", "")
                for fm in (result.frameworks or [])[:5]
            ]
            framework_codes = [c for c in framework_codes if c]
        except Exception:
            framework_codes = []
        report = run_preflight(
            insight.to_dict() if insight else None,
            perspectives={k: v.to_dict() for k, v in perspectives.items()},
            framework_codes=framework_codes,
            published_at=getattr(result, "published_at", None),
            event_id=getattr(result.event, "event_id", "") or "" if result.event else None,
            event_polarity=getattr(insight, "event_polarity", "neutral") if insight else "neutral",
            verifier_warnings=getattr(insight, "warnings", None) if insight else None,
            article_id=str(article_id),
            company_slug=company_slug,
        )
        preflight_status = "PASS" if report.passed else "FAIL"
        # Stamp the report onto the insight via the dataclass field added
        # in W3 — flows through DeepInsight.to_dict() → write_insight() →
        # JSON. The SQLite extractor reads it back via _extract_fields().
        if insight is not None:
            insight.cfo_preflight = report.to_dict()
    except Exception as exc:  # noqa: BLE001 — preflight is additive
        logger.warning("cfo_preflight failed (non-fatal): %s", exc)

    # 8. Phase I: LLM intelligence layers
    intelligence = _run_intelligence_layers(result, insight, company)

    # 9. Write enriched payload back to disk
    written = write_insight(result, insight, perspectives, recs)

    # 9.5 Stage 12.5: Self-evolving ontology — collect discoveries (~5ms)
    try:
        from engine.ontology.discovery.collector import collect_discoveries
        discoveries = collect_discoveries(result, insight, company_slug)
        if discoveries > 0:
            logger.info("enrich_on_demand: %d discovery candidates collected", discoveries)
    except Exception as exc:
        logger.debug("discovery collection skipped: %s", exc)

    # 10. Reload and merge intelligence layers into the payload. For a
    # first-time enrich (stub article that wasn't on disk before),
    # `write_insight` upstream just created the file — locate it now.
    if json_path is None:
        insights_dir = get_data_path("outputs", company_slug, "insights")
        candidates = list(insights_dir.glob(f"*{article_id}*"))
        json_path = candidates[0] if candidates else None
    if json_path is None:
        logger.warning(
            "enrich_on_demand: write_insight produced no file for %s/%s — "
            "skipping intelligence-layer merge",
            company_slug, article_id,
        )
    else:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        # Phase 36 — clear the body_grounded_pending flag now that the
        # re-enrich has run. The next view will hit the fast cached-return
        # path instead of re-firing the pipeline.
        needs_write = False
        if (payload.get("meta") or {}).get("body_grounded_pending"):
            payload.setdefault("meta", {})["body_grounded_pending"] = False
            needs_write = True
        if intelligence:
            payload["intelligence"] = intelligence
            needs_write = True
        if needs_write:
            json_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

    elapsed = round(time.perf_counter() - started, 2)
    logger.info(
        "enrich_on_demand: %s enriched in %.1fs (insight=%s, recs=%d)",
        article_id,
        elapsed,
        bool(insight),
        len(recs.recommendations) if recs and not recs.do_nothing else 0,
    )
    return payload


def _rerun_full_pipeline(
    article_id: str, company_slug: str, company: Company
) -> Any | None:
    """Re-run stages 1-9 from the raw input article for fresh analysis.

    Looks up the article in data/inputs/news/{company_slug}/ and runs
    process_article() to get updated event classification, relevance,
    causal chains, etc. with latest ontology keywords.

    Returns PipelineResult or None if input article not found.
    """
    from engine.analysis.pipeline import process_article

    # Try the requested slug first; fall back to canonical via the
    # slug_aliases table; finally walk every company dir as a last
    # resort. This handles the alias/canonical mismatch that occurs
    # when the caller's JWT carries an alias (e.g. "nestle") but the
    # raw input files live under the canonical ("nestl-india-limited").
    inputs_dir = get_data_path("inputs", "news", company_slug)
    if not inputs_dir.exists():
        try:
            from engine.index.sqlite_index import resolve_slug
            canonical = resolve_slug(company_slug)
            if canonical and canonical != company_slug:
                alt_dir = get_data_path("inputs", "news", canonical)
                if alt_dir.exists():
                    logger.info(
                        "_rerun_full_pipeline: alias %s -> canonical %s for inputs",
                        company_slug, canonical,
                    )
                    inputs_dir = alt_dir
                    company_slug = canonical
        except Exception:  # noqa: BLE001
            pass
    if not inputs_dir.exists():
        # Last-resort: glob across every company dir for the article id.
        base = get_data_path("inputs", "news")
        if base.exists():
            for candidate_dir in base.iterdir():
                if not candidate_dir.is_dir():
                    continue
                if list(candidate_dir.glob(f"*{article_id}*")):
                    logger.info(
                        "_rerun_full_pipeline: located %s under %s (caller asked for %s)",
                        article_id, candidate_dir.name, company_slug,
                    )
                    inputs_dir = candidate_dir
                    company_slug = candidate_dir.name
                    break
    if not inputs_dir.exists():
        logger.warning("_rerun_full_pipeline: no inputs dir for %s", company_slug)
        return None

    # Find the raw input article by ID
    candidates = list(inputs_dir.glob(f"*{article_id}*"))
    if not candidates:
        logger.warning("_rerun_full_pipeline: no input file for %s/%s", company_slug, article_id)
        return None

    import json as _json
    raw_path = candidates[0]
    raw = _json.loads(raw_path.read_text(encoding="utf-8"))

    # Phase 35.5 — Backfill body via googlenewsdecoder + trafilatura when
    # the stored input is headline-only (content < 300 chars or content ==
    # title-duplicate). This is the lazy-backfill path: first on-demand
    # view of any stale article promotes it to body-grounded analysis.
    # Subsequent calls hit the cached body via the 7-day SQLite cache in
    # full_text_extractor.
    existing = (raw.get("content") or "").strip()
    title = (raw.get("title") or "").strip()
    needs_backfill = (
        len(existing) < 300
        or (existing == title)
        or (len(existing) <= len(title) + 50)
    )
    if needs_backfill and raw.get("url"):
        try:
            from engine.ingestion.full_text_extractor import extract_full_text
            result_ft = extract_full_text(raw["url"], timeout=12.0)
            if result_ft and result_ft.body and len(result_ft.body) >= 300:
                raw["content"] = result_ft.body
                raw["summary"] = result_ft.body[:500]
                meta = raw.get("metadata") or {}
                meta["full_text_source"] = "publisher_scrape_lazy"
                meta["full_text_char_count"] = result_ft.char_count
                meta["publisher_url"] = result_ft.publisher_url
                raw["metadata"] = meta
                # Persist back to disk so the next on-demand call skips
                # the network step entirely (cache still saves us, but
                # the file-level mutation also surfaces to backfill audits).
                try:
                    raw_path.write_text(_json.dumps(raw, indent=2), encoding="utf-8")
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "_rerun_full_pipeline: could not persist backfilled "
                        "body for %s: %s", article_id, exc,
                    )
                logger.info(
                    "_rerun_full_pipeline: backfilled body for %s "
                    "(%d → %d chars)",
                    article_id, len(existing), result_ft.char_count,
                )
        except Exception as exc:  # noqa: BLE001 — backfill is additive
            logger.debug(
                "_rerun_full_pipeline: body backfill failed for %s (%s)",
                article_id, type(exc).__name__,
            )

    article = {
        "id": raw.get("id", article_id),
        "title": raw.get("title", ""),
        "content": raw.get("content", ""),
        "summary": raw.get("summary", ""),
        "source": raw.get("source", ""),
        "url": raw.get("url", ""),
        "published_at": raw.get("published_at", ""),
        "metadata": raw.get("metadata", {}),
    }

    logger.info("_rerun_full_pipeline: re-running stages 1-9 for %s", article_id)
    result = process_article(article, company)
    logger.info(
        "_rerun_full_pipeline: %s → tier=%s, event=%s, score=%.1f",
        article_id,
        result.tier,
        result.event.label if result.event else "none",
        result.relevance.adjusted_total if result.relevance else 0,
    )
    return result


def _run_intelligence_layers(
    result: Any, insight: Any, company: Company
) -> dict[str, Any]:
    """Phase I: Run additional LLM intelligence layers on demand.

    Returns a dict with keys: competitive_brief, causal_narrative,
    anticipated_qa, sentiment_trajectory.
    """
    import os

    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return {}

    client = OpenAI(api_key=api_key)
    model = "gpt-4.1-mini"
    intelligence: dict[str, Any] = {}

    headline = insight.headline if hasattr(insight, "headline") else ""
    company_name = company.name
    industry = company.industry

    # I2: Competitive intelligence brief
    try:
        from engine.ontology.intelligence import query_competitors

        competitors = query_competitors(company.slug)
        if competitors:
            peer_names = ", ".join(competitors[:3])
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": f"You are a competitive intelligence analyst for {company_name} in the {industry} sector. Be specific with company names and ₹ figures.",
                    },
                    {
                        "role": "user",
                        "content": f'Given this ESG event: "{headline}", how would competitors {peer_names} be affected or respond? What has the industry done in similar situations? Max 200 words.',
                    },
                ],
                max_tokens=400,
                temperature=0.3,
            )
            from engine.models.llm_calls import log_openai_usage
            log_openai_usage(resp, model=model, article_id=getattr(result, "article_id", None), stage="on_demand")
            intelligence["competitive_brief"] = resp.choices[0].message.content or ""
    except Exception as exc:
        logger.warning("competitive_brief failed: %s", exc)

    # I4: Causal narrative
    try:
        chains = result.causal_chains if hasattr(result, "causal_chains") else []
        if chains:
            chains_text = "\n".join(
                f"- {c.relationship_type} (hops={c.hops}): {c.explanation}"
                if hasattr(c, "relationship_type")
                else f"- {c.get('relationship_type', '')} (hops={c.get('hops', 0)}): {c.get('explanation', '')}"
                for c in chains[:5]
            )
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an ESG risk transmission analyst. Write a 3-4 sentence narrative explaining HOW the ESG event transmits through the company's operations, supply chain, or regulatory environment to create financial risk. Be specific and quantitative where possible.",
                    },
                    {
                        "role": "user",
                        "content": f"Event: {headline}\nCompany: {company_name} ({industry})\nCausal chains:\n{chains_text}",
                    },
                ],
                max_tokens=300,
                temperature=0.3,
            )
            from engine.models.llm_calls import log_openai_usage
            log_openai_usage(resp, model=model, article_id=getattr(result, "article_id", None), stage="on_demand")
            intelligence["causal_narrative"] = resp.choices[0].message.content or ""
    except Exception as exc:
        logger.warning("causal_narrative failed: %s", exc)

    # I5: Executive Q&A pre-generation
    try:
        insight_json = json.dumps(insight.to_dict() if hasattr(insight, "to_dict") else {}, default=str)[:3000]
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a C-suite ESG advisor. Generate the 5 questions a CFO, CEO, or Board member would most likely ask about this intelligence brief, with concise answers (2-3 sentences each). Return JSON: {\"qa\": [{\"question\": \"...\", \"answer\": \"...\"}]}",
                },
                {
                    "role": "user",
                    "content": f"Company: {company_name} ({industry})\nInsight:\n{insight_json}",
                },
            ],
            max_tokens=600,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        from engine.models.llm_calls import log_openai_usage
        log_openai_usage(resp, model=model, article_id=getattr(result, "article_id", None), stage="on_demand")
        qa_data = json.loads(resp.choices[0].message.content or "{}")
        intelligence["anticipated_qa"] = qa_data.get("qa", [])
    except Exception as exc:
        logger.warning("anticipated_qa failed: %s", exc)

    # I6: Sentiment trajectory  (FALLBACK PATH — see Phase 28 note below)
    #
    # Phase 28 audit note: this inline OpenAI sentiment-trajectory call
    # looks like a duplicate of engine.analysis.forecaster.forecast_sentiment_trajectory
    # (the Phase-27 canonical implementation). It is NOT a duplicate —
    # this block runs as a fallback for legacy articles missing the
    # Stage-9 cascade context that the modern forecaster needs. Both
    # implementations are intentional and live in different contexts:
    #   * forecaster.py — Phase 27 canonical, called by insight_generator
    #     for HOME-tier articles with a fresh cascade.
    #   * THIS block    — legacy on-demand fallback, fires when the
    #     article predates Phase 17c and so has no cascade. Returns a
    #     simpler {direction, summary, emerging_themes} shape — caller
    #     handles both shapes via the `intelligence["sentiment_trajectory"]`
    #     accessor.
    #
    # Phase 22.2 — route through `resolve_slug` so a session bound to
    # an alias slug ("puma") still pulls the canonical's history
    # ("puma-se"). Pre-fix this raw SQL bypassed the alias rewrite,
    # so on-demand enrichment for an alias-tenant article saw zero
    # prior context even when the pipeline had indexed plenty.
    try:
        # Phase 24 — route through engine.db so this lookup respects the
        # active backend (SQLite or Postgres). The legacy db_path.exists()
        # gate doesn't make sense on Postgres; we just attempt the query
        # and treat empty results as "no prior context" the same way.
        from engine.db import connect as _db_connect, is_sqlite

        skip = False
        if is_sqlite():
            db_path = get_data_path("snowkap.db")
            if not db_path.exists():
                skip = True

        if not skip:
            with _db_connect() as conn:
                rows = conn.execute(
                    "SELECT title, json_path FROM article_index WHERE company_slug = ? ORDER BY published_at DESC LIMIT 6",
                    (resolve_slug(company.slug),),
                ).fetchall()

            if len(rows) >= 2:
                prev_summaries = []
                for r in rows[1:6]:  # skip current article
                    try:
                        p = json.loads(Path(r["json_path"]).read_text(encoding="utf-8"))
                        nlp = p.get("pipeline", {}).get("nlp", {})
                        prev_summaries.append(
                            f"- {r['title'][:80]} (sentiment={nlp.get('sentiment', 0)})"
                        )
                    except Exception:
                        continue

                if prev_summaries:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[
                            {
                                "role": "system",
                                "content": 'You are a sentiment trend analyst. Return JSON: {"direction": "improving|declining|stable", "summary": "<1 sentence>", "emerging_themes": ["theme1", "theme2"]}',
                            },
                            {
                                "role": "user",
                                "content": f"Company: {company_name}\nRecent articles:\n"
                                + "\n".join(prev_summaries)
                                + f"\n\nNew article: {headline} (sentiment={getattr(result.nlp, 'sentiment', 0) if hasattr(result, 'nlp') else 0})",
                            },
                        ],
                        max_tokens=200,
                        temperature=0.2,
                        response_format={"type": "json_object"},
                    )
                    from engine.models.llm_calls import log_openai_usage
                    log_openai_usage(resp, model=model, article_id=getattr(result, "article_id", None), stage="on_demand")
                    trajectory = json.loads(resp.choices[0].message.content or "{}")
                    intelligence["sentiment_trajectory"] = trajectory
    except Exception as exc:
        logger.warning("sentiment_trajectory failed: %s", exc)

    return intelligence


def _reconstruct_pipeline_result(pipeline_data: dict[str, Any]) -> Any:
    """Reconstruct a PipelineResult-like object from stored JSON.

    This is needed because the on-demand enrichment gets raw JSON from disk,
    but insight_generator and recommendation_engine expect PipelineResult.
    """
    from engine.analysis.pipeline import PipelineResult, NLPExtraction, ESGThemeTags
    from engine.nlp.event_classifier import EventClassification
    from engine.analysis.relevance_scorer import RelevanceScore

    nlp_data = pipeline_data.get("nlp") or {}
    themes_data = pipeline_data.get("themes") or {}
    event_data = pipeline_data.get("event") or {}
    rel_data = pipeline_data.get("relevance") or {}
    risk_data = pipeline_data.get("risk")

    # Reconstruct NLPExtraction
    nlp = NLPExtraction(
        sentiment=nlp_data.get("sentiment", 0),
        sentiment_confidence=nlp_data.get("sentiment_confidence", 0.5),
        tone=nlp_data.get("tone", ["neutral"]) if isinstance(nlp_data.get("tone"), list) else [nlp_data.get("tone", "neutral")],
        narrative_core_claim=nlp_data.get("narrative_core_claim") or nlp_data.get("core_claim", ""),
        narrative_implied_causation=nlp_data.get("narrative_implied_causation") or nlp_data.get("implied_causation", ""),
        narrative_stakeholder_framing=str(nlp_data.get("narrative_stakeholder_framing") or nlp_data.get("stakeholder_framing", "")),
        entities=nlp_data.get("entities", []),
        entity_types=nlp_data.get("entity_types", {}),
        financial_signal=nlp_data.get("financial_signal", {}),
        regulatory_references=nlp_data.get("regulatory_references", []),
        esg_pillar=nlp_data.get("esg_pillar", "") or themes_data.get("primary_pillar", ""),
        esg_topics=nlp_data.get("esg_topics", []),
        content_type=nlp_data.get("content_type", "news"),
        urgency=nlp_data.get("urgency", "medium"),
        time_horizon=nlp_data.get("time_horizon", "medium-term"),
        source_credibility_tier=nlp_data.get("source_credibility_tier", 3),
        climate_events=nlp_data.get("climate_events", []),
        raw_llm_response=nlp_data.get("raw_llm_response", {}),
    )

    themes = ESGThemeTags(
        primary_theme=themes_data.get("primary_theme", ""),
        primary_pillar=themes_data.get("primary_pillar", ""),
        primary_sub_metrics=themes_data.get("primary_sub_metrics", []),
        secondary_themes=themes_data.get("secondary_themes", []),
        confidence=themes_data.get("confidence", 0),
        method=themes_data.get("method", "llm"),
    )

    event = EventClassification(
        event_id=event_data.get("event_id", "event_default"),
        label=event_data.get("label") or event_data.get("event_type_label", "Unclassified"),
        score_floor=event_data.get("score_floor", 2),
        score_ceiling=event_data.get("score_ceiling", 6),
        financial_transmission=event_data.get("financial_transmission", ""),
        matched_keywords=event_data.get("matched_keywords", []),
        has_financial_quantum=event_data.get("has_financial_quantum", False),
        financial_amount_cr=event_data.get("financial_amount_cr"),
    )

    relevance = RelevanceScore(
        total=rel_data.get("total", 0),
        tier=pipeline_data.get("tier", "SECONDARY"),
        esg_correlation=rel_data.get("esg_correlation", 0),
        financial_impact=rel_data.get("financial_impact", 0),
        compliance_risk=rel_data.get("compliance_risk", 0),
        supply_chain_impact=rel_data.get("supply_chain_impact", 0),
        people_impact=rel_data.get("people_impact", 0),
        materiality_weight=rel_data.get("materiality_weight", 0.5),
        adjusted_total=rel_data.get("adjusted_total", 0),
        rejection_reason="",
        ontology_queries=0,
    )

    # Reconstruct causal chains as simple objects with attribute access
    from dataclasses import dataclass, field as dc_field

    @dataclass
    class _Chain:
        relationship_type: str = ""
        hops: int = 0
        impact_score: float = 0.0
        explanation: str = ""
        nodes: list[str] = dc_field(default_factory=list)
        edges: list[str] = dc_field(default_factory=list)

    causal_chains = [
        _Chain(
            relationship_type=c.get("relationship_type", ""),
            hops=c.get("hops", 0),
            impact_score=c.get("impact_score", 0),
            explanation=c.get("explanation", ""),
            nodes=c.get("nodes") or c.get("path", []),
            edges=c.get("edges", []),
        )
        for c in (pipeline_data.get("causal_chains") or [])
    ]

    # Reconstruct frameworks
    @dataclass
    class _FW:
        framework_id: str = ""
        framework_label: str = ""
        relevance: float = 0.0
        is_mandatory: bool = False
        profitability_link: str = ""
        triggered_sections: list[str] = dc_field(default_factory=list)
        triggered_by_themes: list[str] = dc_field(default_factory=list)

        def to_dict(self) -> dict:
            return {
                "framework_id": self.framework_id,
                "framework_label": self.framework_label,
                "relevance": self.relevance,
                "is_mandatory": self.is_mandatory,
                "profitability_link": self.profitability_link,
                "triggered_sections": self.triggered_sections,
                "triggered_by_themes": self.triggered_by_themes,
            }

    frameworks = [
        _FW(
            framework_id=f.get("framework_id", ""),
            framework_label=f.get("framework_label", ""),
            relevance=f.get("relevance", 0),
            is_mandatory=f.get("is_mandatory", False),
            profitability_link=f.get("profitability_link", ""),
            triggered_sections=f.get("triggered_sections", []),
        )
        for f in (pipeline_data.get("frameworks") or [])
    ]

    # Reconstruct risk
    risk = None
    if risk_data:
        from engine.analysis.risk_assessor import RiskAssessment, RiskScore

        def _rebuild_risks(risk_list: list[dict]) -> list[RiskScore]:
            return [
                RiskScore(
                    category=r.get("category", ""),
                    probability=r.get("probability", 0),
                    exposure=r.get("exposure", 0),
                    raw_score=r.get("raw_score", 0),
                    industry_weight=r.get("industry_weight", 1.0),
                    adjusted_score=r.get("adjusted_score", 0),
                    level=r.get("level", "LOW"),
                    lead_indicators=r.get("lead_indicators", []),
                    lag_indicators=r.get("lag_indicators", []),
                )
                for r in risk_list
            ]

        risk = RiskAssessment(
            esg_risks=_rebuild_risks(risk_data.get("esg_risks") or []),
            temples_risks=_rebuild_risks(risk_data.get("temples_risks") or []),
            top_risks=_rebuild_risks(risk_data.get("top_risks") or []),
            aggregate_score=risk_data.get("aggregate_score", 0),
            ontology_queries=risk_data.get("ontology_queries", 0),
        )

    return PipelineResult(
        article_id=pipeline_data.get("article_id", ""),
        title=pipeline_data.get("title", ""),
        url=pipeline_data.get("url", ""),
        source=pipeline_data.get("source", ""),
        published_at=pipeline_data.get("published_at", ""),
        company_slug=pipeline_data.get("company_slug", ""),
        nlp=nlp,
        themes=themes,
        event=event,
        relevance=relevance,
        causal_chains=causal_chains,
        frameworks=frameworks,
        risk=risk,
        stakeholders=pipeline_data.get("stakeholders", []),
        sdgs=pipeline_data.get("sdgs", []),
        tier=pipeline_data.get("tier", "SECONDARY"),
        rejected=pipeline_data.get("rejected", False),
        rejection_reason="",
        ontology_query_count=pipeline_data.get("ontology_query_count", 0),
        elapsed_seconds=0,
        stages_executed=pipeline_data.get("stages_executed", []),
        # Phase 23B — round-trip the raw article body and hero image so
        # rehydrated PipelineResults match the original. Otherwise the
        # output verifier loses its grounding text and the UI / newsletter
        # loses the hero image on any on-demand re-render.
        article_content=pipeline_data.get("article_content", ""),
        image_url=pipeline_data.get("image_url", ""),
    )
