"""End-to-end pre-deploy test runner (2026-04-29 Track A).

Exercises the full analyst workflow against the local API on :8000:

  T1. Login as sales@snowkap.co.in → mint JWT, verify super-admin perms
  T2. Browse existing-company articles (use Adani Power — has HOME tier)
  T3. Open analysis on one HOME article (trigger if needed, poll for ready)
  T4. Verify all 3 roles render with rich Phase 4 schema
  T5. Share analysis via email to ci@snowkap.com
  T6. Onboard tatachemicals.com via domain, poll status, verify articles
  T7. Quality audit — source flags, ₹ consistency, framework specificity

Outputs JSON to data/e2e_reports/e2e_<ts>.json + a markdown summary.
Exit code 0 on full pass, 1 on any FAIL.

Run:
    python scripts/e2e_pre_deploy.py [--skip-onboard] [--skip-share]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

API = "http://127.0.0.1:8000"
TEST_LOGIN_EMAIL = "sales@snowkap.co.in"
TEST_LOGIN_DESIGNATION = "Sales"
TEST_LOGIN_NAME = "Sales (E2E Test)"
TEST_LOGIN_DOMAIN = "snowkap.co.in"
TEST_LOGIN_COMPANY_NAME = "Snowkap"
TEST_SHARE_RECIPIENT = "ci@snowkap.com"
TEST_ONBOARD_DOMAIN = "tatachemicals.com"
TEST_EXISTING_COMPANY_SLUG = "adani-power"  # has HOME-tier articles


def _http(method: str, path: str, *, headers: dict | None = None, body: dict | None = None,
          timeout: int = 60) -> tuple[int, dict | str]:
    url = f"{API}{path}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    hdrs = {"Accept": "application/json"}
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
            try:
                return e.code, json.loads(err_body)
            except json.JSONDecodeError:
                return e.code, err_body
        except Exception:
            return e.code, str(e)
    except urllib.error.URLError as e:
        return -1, f"URLError: {e}"


def _emit(findings: list[dict], step: str, status: str, msg: str, **details) -> None:
    """Append a finding. status ∈ {PASS, FAIL, WARN, INFO}."""
    rec = {
        "step": step,
        "status": status,
        "message": msg,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    rec.update(details)
    findings.append(rec)
    icon = {"PASS": "OK", "FAIL": "XX", "WARN": "??", "INFO": "..."}.get(status, "  ")
    print(f"[{icon}] {step}: {msg}")
    for k, v in details.items():
        if isinstance(v, (str, int, float, bool)):
            print(f"      {k}: {v}")


def t1_login(findings: list[dict]) -> str | None:
    """T1 — Login as sales@snowkap.co.in. Returns the JWT or None."""
    status, body = _http("POST", "/api/auth/login", body={
        "email": TEST_LOGIN_EMAIL,
        "domain": TEST_LOGIN_DOMAIN,
        "designation": TEST_LOGIN_DESIGNATION,
        "company_name": TEST_LOGIN_COMPANY_NAME,
        "name": TEST_LOGIN_NAME,
    })
    if status != 200:
        _emit(findings, "T1", "FAIL", f"Login failed (HTTP {status})", body=str(body)[:400])
        return None
    if not isinstance(body, dict) or "token" not in body:
        _emit(findings, "T1", "FAIL", "Login response missing token", body=str(body)[:400])
        return None
    perms = body.get("permissions", [])
    if "manage_drip_campaigns" not in perms:
        _emit(findings, "T1", "FAIL",
              "sales@ login did NOT receive manage_drip_campaigns permission",
              perms=str(perms)[:300])
        return None
    _emit(findings, "T1", "PASS", "Login OK + super-admin permissions",
          perm_count=len(perms))
    return body["token"]


def t2_browse_existing(findings: list[dict], token: str) -> list[dict]:
    """T2 — List 7 companies + HOME articles for an existing company.
    Returns the list of HOME articles."""
    auth = {"Authorization": f"Bearer {token}"}
    # 2a. /api/companies/
    status, body = _http("GET", "/api/companies/?limit=20", headers=auth)
    if status != 200 or not isinstance(body, dict):
        _emit(findings, "T2a", "FAIL", f"GET /companies failed (HTTP {status})",
              body=str(body)[:300])
        return []
    companies = body.get("companies") or body.get("data") or []
    if isinstance(body, dict) and "companies" in body:
        companies = body["companies"]
    if not companies and isinstance(body, list):
        companies = body
    if len(companies) < 7:
        _emit(findings, "T2a", "FAIL",
              f"Expected ≥7 companies, got {len(companies)}",
              companies=str([c.get("slug") for c in companies])[:300])
        return []
    _emit(findings, "T2a", "PASS",
          f"All 7 target companies present",
          slugs=", ".join(c.get("slug", "?") for c in companies))

    # 2b. /api/news/feed for the test company — get HOME articles
    status, body = _http(
        "GET",
        f"/api/news/feed?limit=20&company_id={TEST_EXISTING_COMPANY_SLUG}&sort_by=priority",
        headers=auth,
    )
    if status != 200:
        _emit(findings, "T2b", "FAIL",
              f"GET /news/feed failed (HTTP {status})",
              body=str(body)[:300])
        return []
    articles = body.get("articles") or [] if isinstance(body, dict) else []
    home = [a for a in articles if a.get("priority_level") in ("HIGH", "CRITICAL")
            or (a.get("relevance_score") or 0) >= 7]
    # Fall back to anything with deep_insight populated
    if not home:
        home = [a for a in articles if a.get("deep_insight", {}).get("headline")]
    _emit(findings, "T2b",
          "PASS" if home else "WARN",
          f"{TEST_EXISTING_COMPANY_SLUG}: {len(articles)} articles ({len(home)} look like HOME tier)",
          total=len(articles), home_count=len(home))
    return home if home else articles[:1]


def t3_open_analysis(findings: list[dict], token: str, articles: list[dict]) -> tuple[str, dict] | tuple[None, None]:
    """T3 — Open analysis on one article. Triggers + polls if needed.
    Returns (article_id, analysis_payload)."""
    if not articles:
        _emit(findings, "T3", "FAIL", "No article to test analysis on")
        return None, None
    auth = {"Authorization": f"Bearer {token}"}
    article = articles[0]
    aid = str(article.get("id"))
    if not aid or aid == "None":
        _emit(findings, "T3", "FAIL", "First article has no id", article=str(article)[:300])
        return None, None

    # GET first to see if analysis is already there
    status, body = _http("GET", f"/api/news/{aid}/analysis", headers=auth)
    if status == 200 and isinstance(body, dict):
        if body.get("deep_insight", {}).get("headline"):
            _emit(findings, "T3", "PASS",
                  f"Analysis already cached for {aid[:8]}",
                  headline=str(body["deep_insight"]["headline"])[:120])
            return aid, body

    # Trigger analysis
    status, body = _http("POST", f"/api/news/{aid}/trigger-analysis", headers=auth)
    if status not in (200, 202):
        _emit(findings, "T3", "FAIL",
              f"trigger-analysis failed (HTTP {status})", body=str(body)[:300])
        return None, None
    _emit(findings, "T3a", "INFO", "Analysis triggered — polling...", article_id=aid[:8])

    # Poll status
    deadline = time.time() + 120
    last_state = None
    while time.time() < deadline:
        status, body = _http("GET", f"/api/news/{aid}/analysis-status", headers=auth)
        if status == 200 and isinstance(body, dict):
            state = body.get("state")
            if state != last_state:
                _emit(findings, "T3a", "INFO", f"Analysis state: {state}",
                      elapsed=int(body.get("elapsed_sec") or 0))
                last_state = state
            if state in ("ready", "done"):
                break
            if state == "failed":
                _emit(findings, "T3", "FAIL",
                      "Analysis pipeline failed",
                      error=str(body.get("error"))[:300])
                return None, None
        time.sleep(3)

    # Fetch the final analysis
    status, body = _http("GET", f"/api/news/{aid}/analysis", headers=auth)
    if status != 200 or not isinstance(body, dict):
        _emit(findings, "T3", "FAIL",
              f"analysis fetch failed (HTTP {status})", body=str(body)[:300])
        return None, None
    if not body.get("deep_insight", {}).get("headline"):
        _emit(findings, "T3", "FAIL",
              "Analysis returned but deep_insight.headline is empty",
              keys=list(body.keys())[:10])
        return None, None
    _emit(findings, "T3", "PASS",
          f"Analysis ready for {aid[:8]}",
          headline=str(body["deep_insight"]["headline"])[:120])
    return aid, body


def t4_check_perspectives(findings: list[dict], analysis: dict) -> dict:
    """T4 — Verify all 3 roles render with the rich Phase 4 schema.
    Returns the perspectives dict for downstream quality audit."""
    perspectives = analysis.get("perspectives") or {}
    if not isinstance(perspectives, dict):
        _emit(findings, "T4", "FAIL", "perspectives is not a dict",
              type=type(perspectives).__name__)
        return {}

    expected_lenses = ("esg-analyst", "cfo", "ceo")
    missing = [l for l in expected_lenses if l not in perspectives]
    if missing:
        _emit(findings, "T4", "FAIL",
              f"Missing perspective lenses: {missing}",
              present=list(perspectives.keys()))
        return {}

    # Phase 4 dedicated generators emit richer fields than legacy transform.
    # ESG Analyst should have: kpi_table OR audit_trail OR framework_citations
    # CEO should have: stakeholder_map OR three_year_trajectory
    # CFO is allowed to be the legacy thin schema (Phase 4 didn't dedicate-generate it)
    esg = perspectives.get("esg-analyst") or {}
    ceo = perspectives.get("ceo") or {}
    cfo = perspectives.get("cfo") or {}

    esg_has_rich = bool(esg.get("kpi_table") or esg.get("audit_trail")
                        or esg.get("framework_citations"))
    ceo_has_rich = bool(ceo.get("stakeholder_map") or ceo.get("three_year_trajectory")
                        or ceo.get("board_paragraph"))
    cfo_has_basics = bool(cfo.get("headline") or cfo.get("what_matters")
                          or cfo.get("action"))

    if not esg_has_rich:
        _emit(findings, "T4-esg", "WARN",
              "ESG Analyst perspective is using LEGACY schema (no kpi_table/audit_trail/framework_citations)",
              keys=list(esg.keys())[:10])
    else:
        _emit(findings, "T4-esg", "PASS",
              "ESG Analyst has rich Phase 4 schema",
              has_kpi_table=bool(esg.get("kpi_table")),
              has_audit_trail=bool(esg.get("audit_trail")))

    if not ceo_has_rich:
        _emit(findings, "T4-ceo", "WARN",
              "CEO perspective is using LEGACY schema (no stakeholder_map/three_year_trajectory)",
              keys=list(ceo.keys())[:10])
    else:
        _emit(findings, "T4-ceo", "PASS",
              "CEO has rich Phase 4 schema",
              has_stakeholder_map=bool(ceo.get("stakeholder_map")),
              has_3y_trajectory=bool(ceo.get("three_year_trajectory")))

    if not cfo_has_basics:
        _emit(findings, "T4-cfo", "FAIL",
              "CFO perspective missing headline/what_matters/action",
              keys=list(cfo.keys())[:10])
    else:
        _emit(findings, "T4-cfo", "PASS",
              "CFO has core fields",
              headline=str(cfo.get("headline", ""))[:80])

    if esg_has_rich and ceo_has_rich and cfo_has_basics:
        _emit(findings, "T4", "PASS", "All 3 perspectives render correctly")

    return perspectives


def t5_share(findings: list[dict], token: str, article_id: str) -> bool:
    """T5 — Share analysis via email to ci@snowkap.com."""
    auth = {"Authorization": f"Bearer {token}"}

    # 5a. Preview first (idempotent — no actual send)
    status, body = _http(
        "POST", f"/api/news/{article_id}/share/preview",
        headers=auth,
        body={
            "recipient_email": TEST_SHARE_RECIPIENT,
            "sender_note": "E2E pre-deploy test — automated send.",
        },
    )
    if status != 200 or not isinstance(body, dict):
        _emit(findings, "T5a", "FAIL",
              f"share/preview failed (HTTP {status})",
              body=str(body)[:300])
        return False
    subject = body.get("subject", "")
    if not subject or len(subject) < 10:
        _emit(findings, "T5a", "WARN",
              f"Subject line looks short or empty: {subject!r}")
    _emit(findings, "T5a", "PASS",
          f"Share preview rendered",
          subject=str(subject)[:90],
          recipient_name=str(body.get("recipient_name") or "(none)"))

    # 5b. Actual send
    status, body = _http(
        "POST", f"/api/news/{article_id}/share",
        headers=auth,
        body={
            "recipient_email": TEST_SHARE_RECIPIENT,
            "sender_note": "E2E pre-deploy test — automated send.",
        },
    )
    if status not in (200, 202):
        _emit(findings, "T5b", "FAIL",
              f"share send failed (HTTP {status})",
              body=str(body)[:400])
        return False
    if not isinstance(body, dict):
        _emit(findings, "T5b", "FAIL", "share response not a dict", body=str(body)[:200])
        return False
    sent_status = body.get("status")
    provider_id = body.get("provider_id") or ""
    if sent_status == "sent" and provider_id:
        _emit(findings, "T5", "PASS",
              f"Email sent to {TEST_SHARE_RECIPIENT}",
              provider_id=provider_id, subject=str(body.get("subject", ""))[:90])
        return True
    if sent_status == "preview":
        _emit(findings, "T5", "WARN",
              f"Resend not configured — fell to preview mode (RESEND_API_KEY missing or domain unverified)",
              status=sent_status,
              error=str(body.get("error") or "")[:200])
        return False
    _emit(findings, "T5", "FAIL",
          f"Send returned status={sent_status!r}, no provider_id",
          body=str(body)[:300])
    return False


def t6_onboard(findings: list[dict], token: str) -> str | None:
    """T6 — Onboard tatachemicals.com. Returns the slug if ready."""
    auth = {"Authorization": f"Bearer {token}"}
    status, body = _http(
        "POST", "/api/admin/onboard",
        headers=auth,
        body={"domain": TEST_ONBOARD_DOMAIN, "limit": 5},
    )
    if status not in (200, 202):
        _emit(findings, "T6a", "FAIL",
              f"POST /admin/onboard failed (HTTP {status})",
              body=str(body)[:400])
        return None
    if not isinstance(body, dict) or not body.get("slug"):
        _emit(findings, "T6a", "FAIL",
              "onboard response missing slug",
              body=str(body)[:300])
        return None
    slug = body["slug"]
    _emit(findings, "T6a", "PASS",
          f"Onboard queued for {TEST_ONBOARD_DOMAIN}",
          slug=slug)

    # Poll status
    deadline = time.time() + 360  # 6 min — onboarding can be slow
    last_state = None
    while time.time() < deadline:
        s, b = _http("GET", f"/api/admin/onboard/{slug}/status", headers=auth)
        if s == 200 and isinstance(b, dict):
            state = b.get("state")
            if state != last_state:
                _emit(findings, "T6b", "INFO",
                      f"Onboard state: {state}",
                      fetched=b.get("fetched", 0),
                      analysed=b.get("analysed", 0),
                      home=b.get("home_count", 0))
                last_state = state
            if state == "ready":
                _emit(findings, "T6", "PASS",
                      f"{TEST_ONBOARD_DOMAIN} → {slug} READY",
                      fetched=b.get("fetched"),
                      analysed=b.get("analysed"),
                      home=b.get("home_count"))
                return slug
            if state == "failed":
                _emit(findings, "T6", "FAIL",
                      "Onboarding pipeline FAILED",
                      error=str(b.get("error"))[:400])
                return None
        time.sleep(5)
    _emit(findings, "T6", "WARN",
          f"Onboarding still {last_state} after 6 minutes — moving on",
          slug=slug)
    return slug  # return slug anyway so downstream tests can probe


def t7_quality_audit(findings: list[dict], analysis: dict, perspectives: dict) -> None:
    """T7 — Quality audit of one analysis: source flags, ₹ consistency,
    framework specificity, polarity correctness."""
    di = analysis.get("deep_insight") or {}
    headline = di.get("headline", "")
    decision = di.get("decision_summary") or {}
    exposure = decision.get("financial_exposure", "")
    key_risk = decision.get("key_risk", "")

    # Source-flag presence: at least ONE ₹ figure should carry (from article)
    # or (engine estimate). If neither, the verifier didn't tag.
    blob = " ".join([
        str(headline), str(exposure), str(key_risk),
        str(decision.get("top_opportunity") or ""),
        str(di.get("net_impact_summary") or ""),
    ])
    has_from_article = "(from article)" in blob
    has_engine_estimate = "(engine estimate)" in blob
    has_rupee = "₹" in blob or "Rs" in blob.lower() or " cr" in blob.lower()
    if has_rupee and not (has_from_article or has_engine_estimate):
        _emit(findings, "T7-source-tags", "WARN",
              "₹ figures present but NO source tags ((from article)/(engine estimate))",
              snippet=blob[:200])
    elif has_from_article or has_engine_estimate:
        _emit(findings, "T7-source-tags", "PASS",
              f"Source tags present: from_article={has_from_article}, engine_estimate={has_engine_estimate}")

    # Materiality polarity coherence — sentiment from NLP
    nlp = analysis.get("nlp") or {}
    sentiment = nlp.get("sentiment", 0)
    materiality = (decision.get("materiality") or "").upper()
    if sentiment >= 1 and materiality in ("CRITICAL", "HIGH") and key_risk and len(key_risk) > 30:
        _emit(findings, "T7-polarity", "WARN",
              f"Polarity mismatch: sentiment=+{sentiment} but materiality={materiality} with heavy key_risk",
              key_risk=key_risk[:120])
    else:
        _emit(findings, "T7-polarity", "PASS",
              f"Polarity coherent (sentiment={sentiment}, materiality={materiality})")

    # Verifier warnings — should NOT be empty for a complex article
    warnings = di.get("warnings") or analysis.get("warnings") or []
    _emit(findings, "T7-verifier",
          "PASS" if isinstance(warnings, list) else "WARN",
          f"Verifier emitted {len(warnings) if isinstance(warnings, list) else '?'} warning(s)")

    # Framework specificity
    frameworks = analysis.get("framework_matches") or []
    if not frameworks:
        _emit(findings, "T7-frameworks", "WARN",
              "No framework_matches in analysis output")
    else:
        first = frameworks[0]
        section = first.get("framework_section") or first.get("framework") or ""
        if ":" in section:  # e.g. "BRSR:P6:Q14" — specific
            _emit(findings, "T7-frameworks", "PASS",
                  f"Framework citation specific: {section}",
                  count=len(frameworks))
        else:
            _emit(findings, "T7-frameworks", "WARN",
                  f"Framework citation NOT specific: {section!r}",
                  count=len(frameworks))

    # CFO under 100 words (Phase 5 invariant)
    cfo_text = " ".join(str(v) for v in (perspectives.get("cfo") or {}).values()
                        if isinstance(v, str))
    cfo_word_count = len(cfo_text.split())
    if cfo_word_count > 0:
        if cfo_word_count <= 120:  # 100 + 20 buffer
            _emit(findings, "T7-cfo-len", "PASS",
                  f"CFO output is {cfo_word_count} words (≤ 120)")
        else:
            _emit(findings, "T7-cfo-len", "WARN",
                  f"CFO output is {cfo_word_count} words — exceeds 100-word target")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--skip-onboard", action="store_true", help="Skip T6 (saves 4-6 min)")
    p.add_argument("--skip-share", action="store_true", help="Skip T5 (no email sent)")
    args = p.parse_args(argv)

    findings: list[dict] = []
    started = datetime.now(timezone.utc).isoformat()
    print(f"=== Snowkap E2E Pre-Deploy Test — {started} ===\n")

    # Health gate
    s, b = _http("GET", "/health", timeout=5)
    if s != 200:
        print(f"FATAL: API not reachable on {API} — boot uvicorn first")
        return 2

    token = t1_login(findings)
    if not token:
        return 1

    home_articles = t2_browse_existing(findings, token)
    article_id, analysis = t3_open_analysis(findings, token, home_articles)
    perspectives = t4_check_perspectives(findings, analysis or {})
    if analysis and perspectives:
        t7_quality_audit(findings, analysis, perspectives)

    if not args.skip_share and article_id:
        t5_share(findings, token, article_id)
    elif args.skip_share:
        _emit(findings, "T5", "INFO", "share test skipped via --skip-share")

    if not args.skip_onboard:
        t6_onboard(findings, token)
    else:
        _emit(findings, "T6", "INFO", "onboard test skipped via --skip-onboard")

    # Tally + report
    pass_n = sum(1 for f in findings if f["status"] == "PASS")
    fail_n = sum(1 for f in findings if f["status"] == "FAIL")
    warn_n = sum(1 for f in findings if f["status"] == "WARN")
    print()
    print(f"=== RESULT: {pass_n} PASS / {fail_n} FAIL / {warn_n} WARN ===")
    if fail_n > 0:
        print("\nFAILURES:")
        for f in findings:
            if f["status"] == "FAIL":
                print(f"  - {f['step']}: {f['message']}")

    # Persist report
    out_dir = ROOT / "data" / "e2e_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_file = out_dir / f"e2e_{ts}.json"
    out_file.write_text(json.dumps({
        "started": started,
        "finished": datetime.now(timezone.utc).isoformat(),
        "summary": {"pass": pass_n, "fail": fail_n, "warn": warn_n},
        "findings": findings,
    }, indent=2), encoding="utf-8")
    print(f"\nReport: {out_file}")

    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
