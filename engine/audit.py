"""Phase 24 — decision/audit provenance writers.

Append-only JSONL logs for the four classes of consequential decision
the engine makes. Every entry is a self-contained JSON object on its
own line; readers can ``jq`` over the file or ingest line-by-line into
SQLite/DuckDB without parsing the whole stream.

Files (created lazily on first append):

* ``data/audit/decision_log.jsonl`` — pipeline decisions (materiality
  downgrade, coherence warning applied, hallucination audit fire,
  do-nothing recommendation, tier shift)
* ``data/audit/ontology_edits.jsonl`` — TTL file edits with diff +
  approver + Toulmin justification
* ``data/audit/promotion_log.jsonl`` — self-evolving ontology
  promotions/rejections/defers (supersedes the legacy
  ``data/ontology/discovery_audit.jsonl`` going forward)
* ``data/audit/preflight_log.jsonl`` — CFO-credibility preflight gate
  pass/fail per article (W3)

Concurrency model
-----------------
A module-level ``threading.Lock`` serialises writes within a process.
Across processes the writers rely on POSIX append-mode being atomic for
writes ≤ ``PIPE_BUF`` (4096 bytes on Linux, 512 on Windows) — every JSON
line we emit is well under that limit. For larger payloads or hostile
NFS, swap in ``portalocker`` later; the public API does not change.

Schema discipline
-----------------
Every entry carries:

* ``ts`` — ISO-8601 UTC timestamp
* ``decision_type`` (or ``edit_type`` / ``decision``) — enum string
* the entity being decided about (article_id, ontology entity, ...)
* ``automated`` — bool flag distinguishing engine vs human action
* ``toulmin`` — optional ``{claim, grounds, warrant, qualifier,
  rebuttal}`` block (required for human approvals, optional for
  automated decisions)

Reading
-------
Use :func:`read_decision_log` / :func:`read_ontology_edits` /
:func:`read_promotion_log` / :func:`read_preflight_log` for typed
iteration. They tolerate corrupt lines (skip + warn) so a single bad
entry doesn't poison the stream.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypedDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths + concurrency
# ---------------------------------------------------------------------------

_AUDIT_DIR_NAME = "audit"

DECISION_LOG = "decision_log.jsonl"
ONTOLOGY_EDITS = "ontology_edits.jsonl"
PROMOTION_LOG = "promotion_log.jsonl"
PREFLIGHT_LOG = "preflight_log.jsonl"
OVERNIGHT_RUNS = "overnight_runs.jsonl"  # Phase 25 W7
ADVISOR_QUEUE = "advisor_queue.jsonl"          # L6 advisor events
ADVISOR_RESOLUTIONS = "advisor_resolutions.jsonl"  # L6 approve/reject log

# In-process serialisation. Cross-process safety relies on append-mode atomicity.
_WRITE_LOCK = threading.Lock()


def _resolve_audit_dir(base_data_dir: Path | None = None) -> Path:
    """Return the audit directory path, creating it if needed.

    Defaults to ``<repo_root>/data/audit`` based on this file's location
    so the engine doesn't need to know about ``engine.config`` (which is
    test-fragile). Callers may pass ``base_data_dir`` to override —
    useful in tests that point at a tmp_path.
    """
    if base_data_dir is None:
        # engine/audit.py → engine/.. → repo root → data/audit
        repo_root = Path(__file__).resolve().parent.parent
        base_data_dir = repo_root / "data"
    audit_dir = base_data_dir / _AUDIT_DIR_NAME
    audit_dir.mkdir(parents=True, exist_ok=True)
    return audit_dir


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

DecisionType = Literal[
    "materiality_downgrade",
    "coherence_warning_applied",
    "low_confidence_classification",
    "hallucination_audit_fired",
    "do_nothing_recommended",
    "tier_shift",
    "rejected_at_relevance_gate",
    "rejected_at_score_gate",
    "salvage_path_activated",
    # Autoresearcher Phase B
    "autoresearcher_experiment_kept",
    "autoresearcher_experiment_discarded",
    "autoresearcher_knob_promoted",
]

PromotionDecision = Literal["promote", "reject", "defer"]

PreflightGate = Literal[
    "financial_impact_quantified",
    "framework_mapped",
    "no_stale_data",
    "polarity_coherent",
    "numeric_consistent",
    "stakeholder_polarity_matched",
]


class ToulminDict(TypedDict, total=False):
    claim: str
    grounds: list[str]
    warrant: str
    qualifier: str
    rebuttal: str


# ---------------------------------------------------------------------------
# L2 — Universal 4-tag governance schema
# ---------------------------------------------------------------------------
#
# Every audit entry MAY carry a `tags` dict with these four keys. Downstream
# consumers (L3 citation-cap enforcer, L4 Toulmin audit-the-audit gate, L5
# SOP phase-gate, L6 advisor, L7 CompanyAgent) slice the audit stream by
# these axes. Enforcing the schema once here is cheaper than 5 separate
# validators downstream.
#
# Enforcement mode is **advisory by default** to protect Phase 26's 1411
# existing tests. Set `SNOWKAP_AUDIT_REQUIRE_TAGS=1` in the environment to
# flip to strict (raises ValueError on missing/malformed tags at every
# append_* call). The current behaviour on advisory mode is:
#   - tags=None  →  not validated, not stamped
#   - tags={...} →  validated; raises ValueError on malformed input even
#                   in advisory mode (callers passing tags MUST get them right)
#
# The 4-axis taxonomy is Snowkap-specific (not a copy of Base Version's
# B2B vocabulary):
#   - scope         WHAT the entry covers
#   - signal_type   the KIND of evidence producing the entry
#   - attribution   WHO/WHAT produced it (module slug or manual:<email>)
#   - uncertainty   confidence band on the underlying claim

TAG_SCOPES = frozenset({"global", "tenant", "article", "industry"})
TAG_SIGNAL_TYPES = frozenset({
    "analyst_judgment",
    "model_extraction",
    "cascade_computation",
    "regulatory_change",
    "peer_event",
})
TAG_UNCERTAINTIES = frozenset({"low", "moderate", "high", "unverified"})
TAG_REQUIRED_KEYS = frozenset({"scope", "signal_type", "attribution", "uncertainty"})


def _validate_tags(tags: dict[str, Any]) -> dict[str, Any]:
    """Validate a 4-tag governance dict; return it unchanged on success.

    Raises:
        ValueError: when keys are missing, extra, or values fall outside
            the enum sets. Attribution must be either a non-empty module
            slug (str, no whitespace, no colon-prefix other than 'manual:')
            or 'manual:<non-empty>'.
    """
    if not isinstance(tags, dict):
        raise ValueError(f"tags must be dict, got {type(tags).__name__}")

    keys = set(tags.keys())
    missing = TAG_REQUIRED_KEYS - keys
    extra = keys - TAG_REQUIRED_KEYS
    if missing:
        raise ValueError(f"tags missing required keys: {sorted(missing)}")
    if extra:
        raise ValueError(f"tags has unexpected keys: {sorted(extra)}")

    scope = tags["scope"]
    if scope not in TAG_SCOPES:
        raise ValueError(f"tags.scope={scope!r} not in {sorted(TAG_SCOPES)}")

    signal_type = tags["signal_type"]
    if signal_type not in TAG_SIGNAL_TYPES:
        raise ValueError(
            f"tags.signal_type={signal_type!r} not in {sorted(TAG_SIGNAL_TYPES)}"
        )

    uncertainty = tags["uncertainty"]
    if uncertainty not in TAG_UNCERTAINTIES:
        raise ValueError(
            f"tags.uncertainty={uncertainty!r} not in {sorted(TAG_UNCERTAINTIES)}"
        )

    attribution = tags["attribution"]
    if not isinstance(attribution, str) or not attribution.strip():
        raise ValueError("tags.attribution must be non-empty str")
    if attribution.startswith("manual:"):
        if len(attribution) <= len("manual:") or not attribution[len("manual:"):].strip():
            raise ValueError("tags.attribution 'manual:' prefix requires non-empty value")
    elif ":" in attribution:
        # Reserved prefix collision (other than the allowed 'manual:')
        raise ValueError(
            f"tags.attribution={attribution!r}: only 'manual:<value>' colon-prefix is allowed"
        )
    elif any(c.isspace() for c in attribution):
        raise ValueError(f"tags.attribution={attribution!r}: module slugs must not contain whitespace")

    return tags


def _strict_tags_required() -> bool:
    """Read the env opt-in flag for strict tag enforcement."""
    import os
    return os.environ.get("SNOWKAP_AUDIT_REQUIRE_TAGS", "").strip() == "1"


def module_tag(
    *,
    attribution: str,
    uncertainty: str = "moderate",
    scope: str = "article",
    signal_type: str = "model_extraction",
) -> dict[str, Any]:
    """Build a validated L2 tags dict for engine-side append_* calls.

    The 5 in-tree callers use this to stay DRY and strict-mode-ready:
      output_verifier, insight_generator, scheduler, cfo_preflight,
      discovery/promoter

    Defaults chosen for the most common case: a per-article extraction
    with moderate confidence. Override per-call when the signal type or
    confidence differs.
    """
    tags = {
        "scope": scope,
        "signal_type": signal_type,
        "attribution": attribution,
        "uncertainty": uncertainty,
    }
    _validate_tags(tags)
    return tags


# ---------------------------------------------------------------------------
# L3 — Citation cap + verbatim sign-off
# ---------------------------------------------------------------------------
#
# Hard data-layer rules (NOT advisory): every persisted Toulmin block
# must cite ≤ MAX_TOULMIN_GROUNDS distinct grounds, each non-empty.
# And `tags.uncertainty="unverified"` is forbidden at the journal layer —
# unverified is in-flight only; persisting a claim means taking a
# position (low | moderate | high) on its confidence.
#
# Why 5: GPT-4.1's effective working memory holds ~5 citations per claim
# inside a single prompt. Beyond 5, grounds get summarised and lose
# verbatim-citation fidelity, which is the whole point of grounds[].

MAX_TOULMIN_GROUNDS = 5


def enforce_citation_cap(toulmin: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate a Toulmin block against the L3 grounds cap.

    Returns the input unchanged on pass. Raises ValueError on violation.
    Tolerates None / empty dict (decisions without Toulmin are fine).
    """
    if not toulmin:
        return toulmin
    grounds = toulmin.get("grounds") or []
    if not isinstance(grounds, list):
        raise ValueError(f"toulmin.grounds must be list, got {type(grounds).__name__}")
    if len(grounds) > MAX_TOULMIN_GROUNDS:
        raise ValueError(
            f"toulmin.grounds violates citation cap: {len(grounds)} > {MAX_TOULMIN_GROUNDS}"
        )
    for i, g in enumerate(grounds):
        if not isinstance(g, str) or not g.strip():
            raise ValueError(f"toulmin.grounds[{i}] is empty or non-string")
    return toulmin


def _enforce_verbatim_signoff(tags: dict[str, Any] | None) -> None:
    """Refuse to persist entries with `tags.uncertainty='unverified'`.

    The journal records what we believe with some level of confidence.
    Unverified candidates belong in the L6 advisor queue or a discovery
    buffer, never in the main audit log.
    """
    if tags is None:
        return
    if tags.get("uncertainty") == "unverified":
        raise ValueError(
            "tags.uncertainty='unverified' is forbidden at the journal layer "
            "(take a position: low/moderate/high) — route unverified candidates "
            "to the advisor queue instead"
        )


def _apply_tags(entry: dict[str, Any], tags: dict[str, Any] | None) -> None:
    """Validate + stamp tags onto an audit entry.

    Single source of truth for the L2 contract across all 4 append_*
    functions. The rules:
      - In strict mode (env opt-in), tags is REQUIRED → raise on None.
      - When tags is provided, ALWAYS validate (advisory mode only
        relaxes the requirement, never the correctness).
      - L3 verbatim sign-off enforced when tags present.
      - On success, mutates `entry` in place by adding the `tags` key.
    """
    if tags is None:
        if _strict_tags_required():
            raise ValueError("tags required (SNOWKAP_AUDIT_REQUIRE_TAGS=1)")
        return
    _validate_tags(tags)
    _enforce_verbatim_signoff(tags)
    entry["tags"] = tags


def _apply_toulmin(entry: dict[str, Any], toulmin: dict[str, Any] | None) -> None:
    """Validate + stamp a Toulmin block onto an audit entry (L3 citation cap).

    Mirrors `_apply_tags` — single source of truth for the L3 contract
    so all 4 append_* writers share the same enforcement path.
    """
    if not toulmin:
        return
    enforce_citation_cap(toulmin)
    entry["toulmin"] = toulmin


# ---------------------------------------------------------------------------
# Low-level append
# ---------------------------------------------------------------------------


def _append(path: Path, entry: dict[str, Any]) -> None:
    """Append a single JSON object as one line (UTF-8, newline-terminated).

    Single ``write()`` call to maximise the chance the OS treats the
    append as atomic. Wrapped in the module lock for thread safety.
    """
    line = json.dumps(entry, ensure_ascii=False, sort_keys=False) + "\n"
    with _WRITE_LOCK:
        # mode="a" + buffering=1 (line-buffered) so writes flush per call
        with path.open("a", encoding="utf-8", buffering=1) as f:
            f.write(line)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Public writers
# ---------------------------------------------------------------------------


def append_decision(
    decision_type: DecisionType,
    *,
    article_id: str | None = None,
    company_slug: str | None = None,
    before: Any = None,
    after: Any = None,
    toulmin: ToulminDict | None = None,
    automated: bool = True,
    user_id: str | None = None,
    extra: dict[str, Any] | None = None,
    tags: dict[str, Any] | None = None,
    base_data_dir: Path | None = None,
) -> None:
    """Record a consequential pipeline decision."""
    entry: dict[str, Any] = {
        "ts": _now(),
        "decision_type": decision_type,
        "automated": automated,
    }
    _apply_tags(entry, tags)
    if article_id is not None:
        entry["article_id"] = article_id
    if company_slug is not None:
        entry["company_slug"] = company_slug
    if before is not None:
        entry["before"] = before
    if after is not None:
        entry["after"] = after
    _apply_toulmin(entry, toulmin)
    if user_id is not None:
        entry["user_id"] = user_id
    if extra:
        entry["extra"] = extra
    path = _resolve_audit_dir(base_data_dir) / DECISION_LOG
    _append(path, entry)
    _maybe_emit_high_uncertainty_event(entry, tags, base_data_dir, decision_type)


def append_edit(
    edit_type: str,
    *,
    target_path: str,
    before_hash: str | None = None,
    after_hash: str | None = None,
    diff_summary: str | None = None,
    toulmin: ToulminDict | None = None,
    user_id: str | None = None,
    automated: bool = False,
    extra: dict[str, Any] | None = None,
    tags: dict[str, Any] | None = None,
    base_data_dir: Path | None = None,
) -> None:
    """Record a TTL / config edit with diff + approver + Toulmin justification.

    Used by the W2 ontology-edit hook (per CLAUDE.md propagation plan)
    and by the ``/refine-ontology`` skill in W4.
    """
    entry: dict[str, Any] = {
        "ts": _now(),
        "edit_type": edit_type,
        "target_path": target_path,
        "automated": automated,
    }
    _apply_tags(entry, tags)
    if before_hash is not None:
        entry["before_hash"] = before_hash
    if after_hash is not None:
        entry["after_hash"] = after_hash
    if diff_summary is not None:
        entry["diff_summary"] = diff_summary
    _apply_toulmin(entry, toulmin)
    if user_id is not None:
        entry["user_id"] = user_id
    if extra:
        entry["extra"] = extra
    path = _resolve_audit_dir(base_data_dir) / ONTOLOGY_EDITS
    _append(path, entry)


def append_promotion(
    decision: PromotionDecision,
    *,
    candidate_id: str,
    category: str,
    candidate_payload: dict[str, Any],
    confidence: float | None = None,
    toulmin: ToulminDict | None = None,
    user_id: str | None = None,
    automated: bool = False,
    extra: dict[str, Any] | None = None,
    tags: dict[str, Any] | None = None,
    base_data_dir: Path | None = None,
) -> None:
    """Record a self-evolving ontology promotion / rejection / defer.

    Supersedes ``data/ontology/discovery_audit.jsonl`` for new writes.
    The Phase 19 promoter still appends there for back-compat; W2 will
    refactor the promoter to write here instead.

    ``category`` matches the Phase 19 7-category taxonomy: ``entity``,
    ``theme``, ``event``, ``framework``, ``edge``, ``weight``,
    ``stakeholder``.
    """
    entry: dict[str, Any] = {
        "ts": _now(),
        "decision": decision,
        "candidate_id": candidate_id,
        "category": category,
        "candidate_payload": candidate_payload,
        "automated": automated,
    }
    _apply_tags(entry, tags)
    if confidence is not None:
        entry["confidence"] = confidence
    _apply_toulmin(entry, toulmin)
    if user_id is not None:
        entry["user_id"] = user_id
    if extra:
        entry["extra"] = extra
    path = _resolve_audit_dir(base_data_dir) / PROMOTION_LOG
    _append(path, entry)


def append_preflight(
    gate: PreflightGate,
    *,
    article_id: str,
    company_slug: str,
    perspective: str = "cfo",
    passed: bool,
    reason: str | None = None,
    extra: dict[str, Any] | None = None,
    tags: dict[str, Any] | None = None,
    base_data_dir: Path | None = None,
) -> None:
    """Record a CFO-credibility preflight gate result (W3 use)."""
    entry: dict[str, Any] = {
        "ts": _now(),
        "gate": gate,
        "article_id": article_id,
        "company_slug": company_slug,
        "perspective": perspective,
        "passed": passed,
    }
    _apply_tags(entry, tags)
    if reason is not None:
        entry["reason"] = reason
    if extra:
        entry["extra"] = extra
    path = _resolve_audit_dir(base_data_dir) / PREFLIGHT_LOG
    _append(path, entry)


# ---------------------------------------------------------------------------
# Readers (tolerant of malformed lines)
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                yield json.loads(stripped)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "audit: skipping malformed line %d in %s: %s",
                    lineno, path.name, exc,
                )


def read_decision_log(base_data_dir: Path | None = None) -> Iterator[dict[str, Any]]:
    yield from _read_jsonl(_resolve_audit_dir(base_data_dir) / DECISION_LOG)


def read_ontology_edits(base_data_dir: Path | None = None) -> Iterator[dict[str, Any]]:
    yield from _read_jsonl(_resolve_audit_dir(base_data_dir) / ONTOLOGY_EDITS)


def read_promotion_log(base_data_dir: Path | None = None) -> Iterator[dict[str, Any]]:
    yield from _read_jsonl(_resolve_audit_dir(base_data_dir) / PROMOTION_LOG)


def read_preflight_log(base_data_dir: Path | None = None) -> Iterator[dict[str, Any]]:
    yield from _read_jsonl(_resolve_audit_dir(base_data_dir) / PREFLIGHT_LOG)


# ---------------------------------------------------------------------------
# Phase 25 W7 — overnight batch run writer + reader
# ---------------------------------------------------------------------------


def append_overnight_run(
    *,
    started_at: str,
    completed_at: str,
    tenants_attempted: int,
    tenants_succeeded: int,
    articles_fetched: int,
    articles_selected: int,
    articles_passed_preflight: int,
    total_cost_usd: float | None = None,
    errors: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
    tags: dict[str, Any] | None = None,
    base_data_dir: Path | None = None,
) -> None:
    """Record a single overnight batch run summary.

    Read by ``snowkap.hooks.session_start`` (which surfaces "31 customers
    × 3 articles = 93 ingested by 1:24am" in the morning banner) and by
    the W10 8am digest composer (which reads the latest entry to
    decide which articles to feature).

    Schema:
        {
          "ts": <ISO timestamp>,
          "started_at": <ISO>,
          "completed_at": <ISO>,
          "tenants_attempted": int,
          "tenants_succeeded": int,
          "articles_fetched": int,        # total fetched across all tenants
          "articles_selected": int,       # top-N picked by article_selector
          "articles_passed_preflight": int,
          "total_cost_usd": float | None,  # from llm_calls.spend_for_window()
          "errors": [{tenant_slug, error_class, message}, ...],
          "extra": {worker_count, max_per_tenant, ...}
        }
    """
    entry: dict[str, Any] = {
        "ts": _now(),
        "started_at": started_at,
        "completed_at": completed_at,
        "tenants_attempted": int(tenants_attempted),
        "tenants_succeeded": int(tenants_succeeded),
        "articles_fetched": int(articles_fetched),
        "articles_selected": int(articles_selected),
        "articles_passed_preflight": int(articles_passed_preflight),
    }
    _apply_tags(entry, tags)
    if total_cost_usd is not None:
        entry["total_cost_usd"] = float(total_cost_usd)
    if errors:
        entry["errors"] = list(errors)
    if extra:
        entry["extra"] = extra
    path = _resolve_audit_dir(base_data_dir) / OVERNIGHT_RUNS
    _append(path, entry)


def read_overnight_runs(base_data_dir: Path | None = None) -> Iterator[dict[str, Any]]:
    yield from _read_jsonl(_resolve_audit_dir(base_data_dir) / OVERNIGHT_RUNS)


# ---------------------------------------------------------------------------
# L6 — Advisor queue (reactive observability)
# ---------------------------------------------------------------------------
#
# When an audit entry carries `tags.uncertainty='high'`, fire a
# structured event into `data/audit/advisor_queue.jsonl`. The L7
# CompanyAgent subscribes to this queue and treats events as triggers
# for re-evaluation.
#
# `unverified` candidates are routed via `route_unverified_to_advisor()`
# because L3 forbids them from the main journal entirely.


def _emit_advisor_event(event: dict[str, Any], base_data_dir: Path | None) -> None:
    path = _resolve_audit_dir(base_data_dir) / ADVISOR_QUEUE
    _append(path, event)


def _maybe_emit_high_uncertainty_event(
    entry: dict[str, Any],
    tags: dict[str, Any] | None,
    base_data_dir: Path | None,
    source_decision_type: str,
) -> None:
    """Fire an advisor event when tags.uncertainty=='high'.

    Idempotent w.r.t. journal writes — emits only when both tags exist
    and uncertainty is high. Low/moderate are journal-only.
    """
    if not tags:
        return
    if tags.get("uncertainty") != "high":
        return
    event = {
        "ts": _now(),
        "event_type": "high_uncertainty_decision",
        "source_decision_type": source_decision_type,
        "article_id": entry.get("article_id"),
        "company_slug": entry.get("company_slug"),
        "tags": tags,
        "toulmin": entry.get("toulmin"),
    }
    _emit_advisor_event(event, base_data_dir)


def route_unverified_to_advisor(
    *,
    candidate_id: str,
    category: str,
    rationale: str,
    tags: dict[str, Any],
    base_data_dir: Path | None = None,
) -> None:
    """Route an `unverified` candidate to the advisor queue.

    Use this when L3's verbatim sign-off rule would block a journal
    append (`tags.uncertainty='unverified'`). The candidate becomes
    advisor-reviewable instead of disappearing.

    Raises ValueError if the candidate is NOT unverified (low/moderate/
    high belong in the main journal).
    """
    _validate_tags(tags)  # L2 schema still applies
    if tags.get("uncertainty") != "unverified":
        raise ValueError(
            f"route_unverified_to_advisor requires tags.uncertainty='unverified', "
            f"got {tags.get('uncertainty')!r}; use append_decision instead"
        )
    event = {
        "ts": _now(),
        "event_type": "unverified_candidate",
        "candidate_id": candidate_id,
        "category": category,
        "rationale": rationale,
        "tags": tags,
    }
    _emit_advisor_event(event, base_data_dir)


def read_advisor_queue(base_data_dir: Path | None = None) -> Iterator[dict[str, Any]]:
    yield from _read_jsonl(_resolve_audit_dir(base_data_dir) / ADVISOR_QUEUE)


def _advisor_event_id(ev: dict[str, Any]) -> str:
    """Deterministic ID for an advisor event.

    Built from `ts + event_type + (article_id|candidate_id)` so the same
    physical event always gets the same ID, even after restart or read
    from disk. The L6 writers don't currently emit `event_id` — this
    helper synthesises one on read.
    """
    ts = str(ev.get("ts") or "")
    etype = str(ev.get("event_type") or "")
    target = str(ev.get("article_id") or ev.get("candidate_id") or "")
    raw = f"{ts}|{etype}|{target}"
    import hashlib
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def read_advisor_resolutions(base_data_dir: Path | None = None) -> Iterator[dict[str, Any]]:
    yield from _read_jsonl(_resolve_audit_dir(base_data_dir) / ADVISOR_RESOLUTIONS)


def apply_resolution_action(
    *,
    event: dict[str, Any],
    resolution: str,         # "approve" | "reject"
    actor: str,
    rationale: str,
) -> dict[str, Any] | None:
    """Side-effect path for an advisor resolution beyond appending the log.

    Today this routes `unverified_candidate` approvals into the
    `discovery.promoter.manual_decide` path so the analyst's approve
    actually promotes the candidate. `reject` becomes a `manual_decide`
    reject too (instead of just silently appending). `high_uncertainty_decision`
    events have no candidate to act on, so the resolution is journal-only.

    Returns the promoter's `DecideResult` as a dict, or None when no
    promoter action was triggered. Caller is responsible for handling
    None (which is normal for high-uncertainty resolutions).

    Failures here are NEVER raised — the resolution log entry has
    already been appended by `resolve_advisor_event`, and the
    side-effect failure is reported via the return shape so the HTTP
    caller can surface it without losing the resolution itself.
    """
    if event.get("event_type") != "unverified_candidate":
        return None
    candidate_id = event.get("candidate_id")
    category = event.get("category")
    if not candidate_id or not category:
        return {"ok": False, "message": "advisor event missing candidate_id/category"}

    # Promote and reject both map to the promoter's vocabulary
    # ("approve" → promote, "reject" → reject). The third option in the
    # promoter, "defer", is NOT used here — analysts deferring re-route
    # via the queue UI without resolving.
    promoter_decision = "promote" if resolution == "approve" else "reject"

    # Build the Toulmin block from the resolution rationale. The
    # promoter requires Toulmin for reject (and accepts it for promote).
    toulmin = {
        "claim": f"manual {promoter_decision} of candidate {candidate_id}",
        "grounds": [rationale] if rationale else ["analyst resolution via advisor queue"],
        "warrant": f"resolved by {actor} via /api/advisor/resolve",
    }

    try:
        from engine.ontology.discovery import promoter as _promoter
        # actor is `manual:<email>`; strip prefix for user_id field
        user_id = actor.removeprefix("manual:") if actor.startswith("manual:") else actor
        result = _promoter.manual_decide(
            candidate_id=f"{category}:{candidate_id}",
            decision=promoter_decision,
            toulmin=toulmin,
            user_id=user_id,
        )
        return {
            "ok": getattr(result, "ok", False),
            "message": getattr(result, "message", ""),
            "category": getattr(result, "category", None),
            "slug": getattr(result, "slug", None),
        }
    except Exception as exc:  # noqa: BLE001 — side effect failure must not lose the resolution
        return {"ok": False, "message": f"promoter call failed: {exc}"}


def resolve_advisor_event(
    *,
    event_id: str,
    resolution: str,         # "approve" | "reject"
    actor: str,              # "manual:<email>"
    rationale: str = "",
    base_data_dir: Path | None = None,
) -> dict[str, Any]:
    """Append a resolution entry to `advisor_resolutions.jsonl`.

    Does NOT mutate the underlying advisor_queue.jsonl (append-only
    invariant preserved). The "open queue" view is computed as
    `events − resolved_event_ids` by `open_advisor_events()`.

    Raises ValueError on bad inputs; idempotent in the sense that
    multiple resolutions for the same event_id are all recorded, with
    the most recent one winning for "open queue" computation.
    """
    if resolution not in ("approve", "reject"):
        raise ValueError(f"resolution must be 'approve' or 'reject', got {resolution!r}")
    if not event_id.strip():
        raise ValueError("event_id is required")
    if not actor.strip():
        raise ValueError("actor is required (use manual:<email> for human actors)")
    entry = {
        "ts": _now(),
        "event_id": event_id,
        "resolution": resolution,
        "actor": actor,
        "rationale": rationale,
    }
    path = _resolve_audit_dir(base_data_dir) / ADVISOR_RESOLUTIONS
    _append(path, entry)
    return entry


def open_advisor_events(
    *,
    base_data_dir: Path | None = None,
    tenant: str | None = None,
) -> list[dict[str, Any]]:
    """Return the "open" advisor queue (events minus resolved ones).

    Each returned event has an `event_id` field synthesised on read. When
    `tenant` is provided, filters to entries with `company_slug == tenant`.

    Most-recent resolution wins (so reopening via a second approve/reject
    is supported by appending another resolution entry).
    """
    resolutions = list(read_advisor_resolutions(base_data_dir))
    resolved_ids: set[str] = set()
    for r in resolutions:
        # Latest resolution determines state. For now, ANY resolution
        # (approve OR reject) removes the event from the open queue.
        rid = r.get("event_id")
        if rid:
            resolved_ids.add(rid)

    out: list[dict[str, Any]] = []
    for ev in read_advisor_queue(base_data_dir):
        eid = _advisor_event_id(ev)
        if eid in resolved_ids:
            continue
        if tenant and ev.get("company_slug") != tenant:
            continue
        ev_with_id = dict(ev)
        ev_with_id["event_id"] = eid
        out.append(ev_with_id)
    return out


# ---------------------------------------------------------------------------
# L4 — Toulmin audit-the-audit
# ---------------------------------------------------------------------------
#
# A meta-verifier that scans recent decision-log entries and reports
# violations of the L2 tag schema + Toulmin discipline contract.
# Untagged legacy entries are skipped (back-compat with Phase 26).
#
# This is intentionally a function, not a class — it's a pure read of
# the JSONL log + a list of violations. L4 ships the discipline gate;
# how it's surfaced (CI, dashboard, advisor queue) is up to consumers.


def audit_the_audit(
    *,
    window: int = 100,
    base_data_dir: Path | None = None,
) -> dict[str, Any]:
    """Audit the last `window` decision-log entries for L2/L4 discipline.

    Returns a structured report dict:
        {
            "pass": bool,
            "scanned": int,          # entries actually audited
            "skipped_untagged": int, # entries skipped (no L2 tags)
            "violations": [
                {"rule": <slug>, "entry_index": <int>, "article_id": <str|None>},
                ...
            ]
        }

    Skipped entries are NOT counted as failures — they're pre-L2 legacy.
    """
    path = _resolve_audit_dir(base_data_dir) / DECISION_LOG
    all_entries = list(_read_jsonl(path))
    # Tail of the last `window` entries (or all, if fewer)
    entries = all_entries[-window:] if window > 0 else all_entries

    violations: list[dict[str, Any]] = []
    skipped = 0
    scanned = 0

    for idx, entry in enumerate(entries):
        tags = entry.get("tags")
        if not tags:
            skipped += 1
            continue
        scanned += 1
        article_id = entry.get("article_id")
        signal_type = tags.get("signal_type")
        uncertainty = tags.get("uncertainty")
        attribution = tags.get("attribution") or ""
        automated = entry.get("automated", True)
        toulmin = entry.get("toulmin") or {}

        # Rule 1: analyst_judgment requires Toulmin
        if signal_type == "analyst_judgment" and not toulmin:
            violations.append({
                "rule": "analyst_judgment_requires_toulmin",
                "entry_index": idx,
                "article_id": article_id,
            })

        # Rule 2: Toulmin block (when present) must have non-empty
        # claim, grounds, warrant
        if toulmin:
            if not (toulmin.get("claim") or "").strip():
                violations.append({
                    "rule": "toulmin_missing_claim",
                    "entry_index": idx,
                    "article_id": article_id,
                })
            grounds = toulmin.get("grounds") or []
            if not grounds or not any((g or "").strip() for g in grounds):
                violations.append({
                    "rule": "toulmin_missing_grounds",
                    "entry_index": idx,
                    "article_id": article_id,
                })
            if not (toulmin.get("warrant") or "").strip():
                violations.append({
                    "rule": "toulmin_missing_warrant",
                    "entry_index": idx,
                    "article_id": article_id,
                })

        # Rule 3: attribution form must match automated flag
        is_manual = attribution.startswith("manual:")
        if is_manual and automated:
            violations.append({
                "rule": "attribution_automation_mismatch",
                "entry_index": idx,
                "article_id": article_id,
                "detail": "manual: attribution but automated=True",
            })
        elif not is_manual and not automated:
            violations.append({
                "rule": "attribution_automation_mismatch",
                "entry_index": idx,
                "article_id": article_id,
                "detail": "module slug attribution but automated=False",
            })

        # Rule 4: high/unverified uncertainty requires Toulmin qualifier
        if uncertainty in ("high", "unverified"):
            if not (toulmin.get("qualifier") or "").strip():
                violations.append({
                    "rule": "high_uncertainty_requires_qualifier",
                    "entry_index": idx,
                    "article_id": article_id,
                })

    return {
        "pass": len(violations) == 0,
        "scanned": scanned,
        "skipped_untagged": skipped,
        "violations": violations,
    }


# ---------------------------------------------------------------------------
# Convenience constructors for Toulmin blocks
# ---------------------------------------------------------------------------


def make_toulmin(
    claim: str,
    grounds: list[str],
    warrant: str,
    qualifier: str = "",
    rebuttal: str = "",
) -> ToulminDict:
    """Build a Toulmin dict with required + optional fields filled in.

    The required-rebuttal discipline is enforced at the *consumer* layer
    (insight_generator + W4 /snowkap-advise). This helper just makes
    construction cheap.
    """
    out: ToulminDict = {
        "claim": claim,
        "grounds": grounds,
        "warrant": warrant,
    }
    if qualifier:
        out["qualifier"] = qualifier
    if rebuttal:
        out["rebuttal"] = rebuttal
    return out
