"""Phase 10 — Drip campaign runner.

Entry point for the cron job that fires due campaigns:

    python -m engine.output.campaign_runner run-due [--once] [--dry-run]
                                                    [--campaign-id <id>]

Cron:
    */5 * * * * cd /app && python -m engine.output.campaign_runner run-due

What it does for each due campaign:

  1. Resolve the target article:
       * `article_selection='specific'` → use campaign.article_id
       * `article_selection='latest_home'` → scan outputs, pick most recent
         HOME-tier article for the target company (skip if none exist)
  2. **Freshness pre-check:** if the insight JSON's schema_version is stale
     (not "2.0-primitives-l2"), call `enrich_on_demand()` first so the email
     carries the current primitive cascade + framework citations.
  3. **Accuracy pre-check:** verify the insight has materiality, a net_impact
     summary, and at least one framework with a section code. Skip the send
     (log `skipped_stale`) if any assertion fails — DON'T ship a half-baked
     brief to a prospect's inbox.
  4. For each recipient: dedup check → `share_article_by_email()` → log row.
  5. Update campaign: bump `last_sent_at`, compute next `next_send_at`.

Why the runner is defensive:
  * A scheduled email is the moment of truth for Snowkap's credibility. If a
    prospect gets a brief with "Bottom line: (none)" or an empty framework
    section, we've damaged the brand. Runtime gates catch those cases and
    skip rather than send.
  * Cron can fire twice in quick succession (retries, daylight saving,
    operator `send-now` overlapping a scheduled slot). The `find_recent_send`
    dedup probe prevents double-delivery.
  * Resend API failures on one recipient must not abort the whole batch —
    each recipient is a separate try/except.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.config import get_data_path
from engine.models import campaign_store
from engine.models.campaign_store import Campaign, Recipient
from engine.output.cadence import compute_next_send, dedup_window_start
from engine.output.newsletter_renderer import build_articles_from_outputs
from engine.output.share_service import ShareResult, share_article_by_email

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = "2.0-primitives-l2"


# ---------------------------------------------------------------------------
# Article resolution
# ---------------------------------------------------------------------------


def _resolve_article(campaign: Campaign, outputs_root: Path) -> str | None:
    """Return the article_id to send, or None if nothing suitable is found.

    For `specific` campaigns we trust the stored id. For `latest_home` we
    scan the company's outputs and pick the most recent HOME-tier article
    that has a non-empty insight (SECONDARY tier articles never got Stage 10
    deep-insight generation and would render empty sections)."""
    if campaign.article_selection == "specific":
        return campaign.article_id

    # latest_home: use build_articles_from_outputs (already HOME-only — it
    # skips articles without an `insight` payload, which is the same gate).
    articles = build_articles_from_outputs(
        slugs=[campaign.target_company],
        outputs_root=outputs_root,
        max_count=20,
    )
    if not articles:
        return None
    # Most recent is already first
    return articles[0].article_id


# ---------------------------------------------------------------------------
# Freshness + accuracy pre-checks
# ---------------------------------------------------------------------------


def _load_insight_json(article_id: str, company_slug: str, outputs_root: Path) -> dict[str, Any] | None:
    """Return the insight JSON payload, or None if file missing/corrupt."""
    insights_dir = outputs_root / company_slug / "insights"
    if not insights_dir.exists():
        return None
    # Filename convention is `<date>_<article_id>.json`
    for path in sorted(insights_dir.glob(f"*{article_id}*"), reverse=True):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("load_insight_json failed for %s: %s", path, exc)
    return None


def _ensure_fresh_schema(
    article_id: str, company_slug: str, outputs_root: Path, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """If the insight is on the current schema version, return it as-is.
    Otherwise run on-demand enrichment and reload.

    Returns None on enrichment failure — caller should skip the send."""
    stored_version = (payload.get("meta") or {}).get("schema_version", "")
    if stored_version == CURRENT_SCHEMA_VERSION:
        return payload

    logger.info(
        "campaign_runner: enriching %s/%s (schema %r → %r)",
        company_slug, article_id, stored_version, CURRENT_SCHEMA_VERSION,
    )
    try:
        from engine.analysis.on_demand import enrich_on_demand
        enriched = enrich_on_demand(article_id, company_slug, force=True)
        if enriched is None:
            return None
        # Re-read to get the persisted file (enrich_on_demand writes to disk)
        return _load_insight_json(article_id, company_slug, outputs_root) or enriched
    except Exception as exc:
        logger.exception("enrich_on_demand failed for %s: %s", article_id, exc)
        return None


def _accuracy_check(payload: dict[str, Any]) -> tuple[bool, str]:
    """Return (ok, reason_if_not_ok). The runner skips sends that fail this —
    a half-baked brief in a prospect's inbox damages credibility more than
    no brief at all."""
    insight = payload.get("insight") or {}
    if not insight:
        return False, "insight missing (SECONDARY/REJECTED article)"

    decision = insight.get("decision_summary") or {}
    materiality = (decision.get("materiality") or "").upper()
    if materiality not in ("HIGH", "MODERATE", "LOW", "CRITICAL"):
        return False, f"materiality invalid: {materiality!r}"

    # Need either a bottom-line sentence (key_risk) or a net_impact_summary
    if not (decision.get("key_risk") or insight.get("net_impact_summary")):
        return False, "no key_risk or net_impact_summary"

    # At least one framework with a section code
    pipeline = payload.get("pipeline") or {}
    frameworks = pipeline.get("frameworks") or []
    has_section = any(
        (fw.get("section") or fw.get("triggered_sections")) for fw in frameworks
        if isinstance(fw, dict)
    )
    if not has_section:
        return False, "no framework with section code populated"

    return True, ""


# ---------------------------------------------------------------------------
# Per-campaign execution
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _greeting_name(recipient: Recipient) -> str | None:
    """Resolve the greeting name: explicit override → name_from_email → None."""
    if recipient.name_override:
        return recipient.name_override.strip() or None
    try:
        from engine.output.email_sender import name_from_email
        return name_from_email(recipient.email)
    except Exception:
        return None


def _process_campaign(
    campaign: Campaign,
    *,
    outputs_root: Path,
    dry_run: bool,
    now_iso: str,
    force_send: bool = False,
) -> dict[str, Any]:
    """Run one campaign. Returns a summary dict with counts per status.

    `force_send=True` (used by `send-now`) ignores next_send_at and fires
    immediately without advancing the schedule.
    """
    summary = {
        "campaign_id": campaign.id,
        "sent": 0,
        "preview": 0,
        "failed": 0,
        "skipped_stale": 0,
        "skipped_dedup": 0,
        "reason": "",
    }

    # 1. Resolve article
    article_id = _resolve_article(campaign, outputs_root)
    if not article_id:
        summary["reason"] = "no HOME article available"
        recipients = campaign_store.list_recipients(campaign.id)
        for r in recipients:
            campaign_store.append_send_log(
                campaign_id=campaign.id, recipient_email=r.email,
                status="skipped_stale",
                error="no HOME article available for campaign",
                sent_at=now_iso,
            )
            summary["skipped_stale"] += 1
        # Important: DO NOT advance next_send_at — retry next cron tick
        return summary

    # 2. Load + freshness check
    payload = _load_insight_json(article_id, campaign.target_company, outputs_root)
    if payload is None:
        summary["reason"] = f"insight JSON not found for {article_id}"
        _log_stale_for_all_recipients(campaign.id, article_id, summary, summary["reason"], now_iso)
        return summary

    payload = _ensure_fresh_schema(article_id, campaign.target_company, outputs_root, payload)
    if payload is None:
        summary["reason"] = "freshness enrichment failed"
        _log_stale_for_all_recipients(campaign.id, article_id, summary, summary["reason"], now_iso)
        return summary

    # 3. Accuracy check
    ok, reason = _accuracy_check(payload)
    if not ok:
        summary["reason"] = f"accuracy check failed: {reason}"
        _log_stale_for_all_recipients(campaign.id, article_id, summary, summary["reason"], now_iso)
        return summary

    # 4. Per-recipient send loop
    recipients = campaign_store.list_recipients(campaign.id)
    if not recipients:
        summary["reason"] = "no recipients"
        return summary

    dedup_cutoff = dedup_window_start(campaign.cadence, now_iso=now_iso)  # type: ignore[arg-type]

    for recipient in recipients:
        # Dedup
        prior = campaign_store.find_recent_send(
            campaign.id, recipient.email, article_id, since_iso=dedup_cutoff,
        )
        if prior is not None:
            campaign_store.append_send_log(
                campaign_id=campaign.id, recipient_email=recipient.email,
                article_id=article_id, status="skipped_dedup",
                error=f"already sent at {prior.sent_at}",
                sent_at=now_iso,
            )
            summary["skipped_dedup"] += 1
            continue

        # Send (or dry-run)
        try:
            result: ShareResult = share_article_by_email(
                article_id=article_id,
                company_slug=campaign.target_company,
                recipient_email=recipient.email,
                outputs_root=outputs_root,
                sender_note=campaign.sender_note,
                cta_url=(campaign.cta_url or "https://snowkap.com/contact-us/"),
                cta_label=(campaign.cta_label or "Book a demo with Snowkap"),
                dry_run=dry_run,
            )
        except Exception as exc:
            logger.exception("share_article_by_email raised for %s: %s", recipient.email, exc)
            campaign_store.append_send_log(
                campaign_id=campaign.id, recipient_email=recipient.email,
                article_id=article_id, status="failed",
                error=str(exc), sent_at=now_iso,
            )
            summary["failed"] += 1
            continue

        sent_at = now_iso
        # Override recipient-greeting-name if the campaign has one. share_service
        # uses name_from_email by default; for overrides we'd need a deeper
        # plumbing change — capture that as a V2 improvement.
        _ = _greeting_name(recipient)  # future: pass through to render

        campaign_store.append_send_log(
            campaign_id=campaign.id, recipient_email=recipient.email,
            article_id=article_id, subject=result.subject,
            html_length=result.html_length,
            status=result.status,
            provider_id=result.provider_id or None,
            error=result.error or None,
            sent_at=sent_at,
        )
        campaign_store.touch_recipient_last_sent(campaign.id, recipient.email, sent_at)

        if result.status == "sent":
            summary["sent"] += 1
        elif result.status == "preview":
            summary["preview"] += 1
        else:
            summary["failed"] += 1

    # 5. Advance schedule — only when we actually touched recipients AND
    #    either sent or dry-ran successfully (not if we skipped them all).
    #    force_send (send-now) does NOT advance next_send_at.
    delivered = summary["sent"] + summary["preview"]
    if delivered > 0 and not force_send:
        next_ts = compute_next_send(
            campaign.cadence,  # type: ignore[arg-type]
            day_of_week=campaign.day_of_week,
            day_of_month=campaign.day_of_month,
            send_time_utc=campaign.send_time_utc,
            from_time=now_iso,
        ) if campaign.cadence != "once" else None
        campaign_store.mark_sent(campaign.id, last_sent_at=now_iso, next_send_at=next_ts)

    return summary


def _log_stale_for_all_recipients(
    campaign_id: str, article_id: str | None, summary: dict[str, Any],
    reason: str, sent_at: str,
) -> None:
    recipients = campaign_store.list_recipients(campaign_id)
    for r in recipients:
        campaign_store.append_send_log(
            campaign_id=campaign_id, recipient_email=r.email,
            article_id=article_id, status="skipped_stale",
            error=reason, sent_at=sent_at,
        )
        summary["skipped_stale"] += 1


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def run_due_campaigns(
    *,
    now: str | None = None,
    dry_run: bool = False,
    campaign_id: str | None = None,
    outputs_root: Path | None = None,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Run every due campaign (or a single one if `campaign_id` is set).

    Returns a list of per-campaign summary dicts.
    """
    if outputs_root is None:
        outputs_root = get_data_path("outputs")
    now_iso = now or _now_iso()

    if campaign_id:
        campaign = campaign_store.get_campaign(campaign_id)
        if campaign is None:
            logger.warning("run_due_campaigns: campaign_id=%s not found", campaign_id)
            return []
        # For send-now, we don't enforce the active+due filter — admin can
        # fire a paused campaign manually.
        if not force and campaign.status != "active":
            logger.info("campaign %s is not active (status=%s) — skipping", campaign.id, campaign.status)
            return []
        campaigns = [campaign]
    else:
        campaigns = campaign_store.list_due_campaigns(now=now_iso)

    logger.info("campaign_runner: %d campaigns to process", len(campaigns))
    summaries: list[dict[str, Any]] = []
    for c in campaigns:
        try:
            summary = _process_campaign(
                c, outputs_root=outputs_root, dry_run=dry_run,
                now_iso=now_iso, force_send=force,
            )
            summaries.append(summary)
        except Exception as exc:
            logger.exception("campaign %s raised: %s", c.id, exc)
            summaries.append({"campaign_id": c.id, "error": str(exc)})
    return summaries


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m engine.output.campaign_runner",
        description="Run due drip campaigns.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run-due", help="Fire every active campaign whose next_send_at has passed.")
    p_run.add_argument("--once", action="store_true", help="(no-op placeholder for parity with cron)")
    p_run.add_argument("--dry-run", action="store_true", help="Render but don't actually send via Resend.")
    p_run.add_argument("--campaign-id", default=None, help="Run only the given campaign id.")
    p_run.add_argument("--now", default=None, help="Override 'now' for testing (ISO-8601 UTC).")
    p_run.add_argument("--force", action="store_true",
                       help="With --campaign-id: send even if paused / even if next_send_at is in future. "
                            "Does NOT advance the schedule (same as send-now API).")

    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.cmd == "run-due":
        summaries = run_due_campaigns(
            now=args.now, dry_run=args.dry_run,
            campaign_id=args.campaign_id, force=args.force,
        )
        for s in summaries:
            print(json.dumps(s, default=str))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
