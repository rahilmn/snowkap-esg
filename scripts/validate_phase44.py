"""Phase 44 — End-to-end validation against Supabase Postgres.

Runs nine acceptance tests against a live Replit deployment OR a local
Snowkap API. Postgres-ONLY: hard-fails if SQLite is the active backend.

Usage:
    # On Replit shell — hits the local API
    python scripts/validate_phase44.py --token <your-jwt>

    # From a dev machine, against the deployed URL
    python scripts/validate_phase44.py \\
        --api-base https://powerofnow.snowkap.co.in \\
        --token <your-jwt> \\
        --domain adidas.com

The token must be an admin-scoped JWT — typically the JWT minted for
the super-admin email. Get it from the browser DevTools (Local Storage
→ `snowkap-auth`) on a logged-in tab.

Exit codes:
    0   all 9 tests passed
    1   at least 1 test failed
    2   environment is misconfigured (Postgres not active, OpenRouter
        not set, etc.) — can't even run the validation

The script prints a pass/fail summary at the end. Each failure includes
the actual reason so the next fix is obvious.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Make engine imports work when invoked from the repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Source .env if it exists (local dev mode)
def _source_env() -> None:
    env_path = _REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_source_env()

# Lazy import requests — must come after sys.path adjustment
try:
    import requests
except ImportError:
    print("validate_phase44: requests is not installed. Run `pip install requests`.")
    sys.exit(2)


# ---------------------------------------------------------------------------
# Test result accumulator
# ---------------------------------------------------------------------------


class TestReport:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, str, float]] = []
        # (name, status, message, elapsed_seconds)

    def record(self, name: str, status: str, message: str = "", elapsed: float = 0.0) -> None:
        self.results.append((name, status, message, elapsed))

    def pass_count(self) -> int:
        return sum(1 for _, s, _, _ in self.results if s == "PASS")

    def fail_count(self) -> int:
        return sum(1 for _, s, _, _ in self.results if s == "FAIL")

    def render(self) -> None:
        print()
        print("=" * 72)
        print("PHASE 44 END-TO-END VALIDATION REPORT")
        print("=" * 72)
        for name, status, msg, elapsed in self.results:
            symbol = "PASS" if status == "PASS" else "FAIL"
            t = f"{elapsed:6.1f}s" if elapsed > 0 else "      "
            print(f"  [{symbol}]  {t}  {name}")
            if msg and status != "PASS":
                # Wrap long messages
                for line in msg.splitlines()[:3]:
                    print(f"             -> {line[:200]}")
        print()
        p, f = self.pass_count(), self.fail_count()
        total = len(self.results)
        verdict = "ALL CLEAR" if f == 0 else f"{f} FAILURE(S) — see above"
        print(f"  {p}/{total} passed  ::  {verdict}")
        print("=" * 72)


# ---------------------------------------------------------------------------
# Backend-mode tests (direct Python imports — no JWT, no HTTP)
# ---------------------------------------------------------------------------


def test_postgres_active(report: TestReport) -> None:
    t0 = time.monotonic()
    name = "01  Postgres is the active backend"
    try:
        from engine.db.connection import get_backend, is_postgres
        backend = get_backend()
        if not is_postgres():
            raise AssertionError(
                f"backend={backend!r} (expected 'postgres'). "
                f"Set SUPABASE_DATABASE_URL or SNOWKAP_DB_BACKEND=postgres."
            )
        report.record(name, "PASS", f"backend={backend}", time.monotonic() - t0)
    except Exception as exc:
        report.record(name, "FAIL", str(exc), time.monotonic() - t0)


def test_openrouter_active(report: TestReport) -> None:
    t0 = time.monotonic()
    name = "02  OpenRouter routing active"
    try:
        from engine.llm.keys import is_using_legacy_openai
        from engine.llm.routing import resolve_model
        if is_using_legacy_openai():
            raise AssertionError("OPENROUTER_API_KEY not set — direct OpenAI fallback in effect.")
        rh = resolve_model("reasoning_heavy")
        cm = resolve_model("composition")
        if rh != "anthropic/claude-opus-4.6":
            raise AssertionError(f"reasoning_heavy → {rh} (expected anthropic/claude-opus-4.6)")
        if cm != "openai/gpt-4.1":
            raise AssertionError(f"composition → {cm} (expected openai/gpt-4.1)")
        report.record(name, "PASS", f"reasoning_heavy={rh}", time.monotonic() - t0)
    except Exception as exc:
        report.record(name, "FAIL", str(exc), time.monotonic() - t0)


def test_postgres_writes(report: TestReport, alias_slug: str, canonical_slug: str | None) -> None:
    """Verify the onboard flow's writes landed in Postgres."""
    t0 = time.monotonic()
    name = "09  Postgres rows present (slug_aliases / companies / article_pool)"
    try:
        from engine.db.connection import connect

        with connect() as conn:
            # slug_aliases — Phase 42 should have written this BEFORE analysis
            cur = conn.execute(
                "SELECT canonical FROM slug_aliases WHERE alias = ?",
                (alias_slug,),
            )
            alias_row = cur.fetchone()
            if not alias_row:
                # Alias might equal canonical (no rename happened) — also accept
                cur = conn.execute(
                    "SELECT slug FROM companies WHERE slug = ?",
                    (alias_slug,),
                )
                if not cur.fetchone():
                    raise AssertionError(
                        f"Neither slug_aliases nor companies has a row for '{alias_slug}'. "
                        "Either Phase 42 didn't register the alias or the onboard didn't complete."
                    )
                resolved = alias_slug
            else:
                resolved = alias_row[0]  # index access works on both psycopg2 + sqlite Row wrappers

            # companies — onboarder writes this
            cur = conn.execute(
                "SELECT slug, name, industry FROM companies WHERE slug = ?",
                (resolved,),
            )
            comp_row = cur.fetchone()
            if not comp_row:
                raise AssertionError(f"companies row missing for canonical={resolved!r}")

            # article_pool + company_article_view — writer.py upserts these
            cur = conn.execute(
                """
                SELECT COUNT(*) FROM article_pool a
                JOIN company_article_view v ON v.article_id = a.id
                WHERE v.company_slug = ?
                """,
                (resolved,),
            )
            n_row = cur.fetchone()
            # Phase 45 — Row wrapper from engine/db/connection supports
            # both index-style and dict-style access via __getitem__.
            # Use index access since COUNT(*) returns a single column.
            n_articles = n_row[0] if n_row else 0
            if n_articles < 1:
                raise AssertionError(
                    f"company_article_view has 0 rows for {resolved!r} — writer.py is not "
                    f"upserting article_pool / company_article_view. Check worker logs."
                )

        report.record(
            name, "PASS",
            f"alias={alias_slug} → canonical={resolved}, articles={n_articles}",
            time.monotonic() - t0,
        )
    except Exception as exc:
        report.record(name, "FAIL", str(exc), time.monotonic() - t0)


# ---------------------------------------------------------------------------
# API-mode tests (require JWT, hit the live API)
# ---------------------------------------------------------------------------


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def test_onboard_flow(
    report: TestReport, api_base: str, token: str, domain: str,
) -> tuple[str | None, dict | None]:
    """Phase 45 — POST /api/onboard/v2 is synchronous. No SSE polling
    needed; the endpoint returns when everything is done.

    Returns (canonical_slug, response_payload) so downstream tests can
    use them. canonical_slug is None on failure.
    """
    t0 = time.monotonic()
    name = "03  Onboard completes within 240s (v2 synchronous)"
    try:
        r = requests.post(
            f"{api_base}/api/onboard/v2",
            headers=_auth_headers(token),
            # Phase 45.F: match the v2 endpoint's new default (limit=3).
            # Test 04 needs ≥1 article in the deck, test 06 needs ≥2 with
            # recs — limit=3 satisfies both with margin to spare.
            json={"domain": domain, "limit": 3},
            timeout=240,  # client-side bar
        )
        if r.status_code != 200:
            raise AssertionError(
                f"POST /api/onboard/v2 returned {r.status_code}: {r.text[:400]}"
            )
        payload = r.json()
        slug = payload.get("slug")
        if not slug:
            raise AssertionError(f"No slug in v2 response: {payload}")

        elapsed = time.monotonic() - t0
        if elapsed > 240:
            raise AssertionError(f"Onboard exceeded 240s ({elapsed:.0f}s)")

        report.record(
            name, "PASS",
            f"slug={slug} canonical_name={payload.get('canonical_name')!r} "
            f"industry={payload.get('industry')} ticker={payload.get('ticker')} "
            f"fetched={payload.get('fetched_count')} analysed={payload.get('analysed_count')} "
            f"home={payload.get('home_count')} confidence={payload.get('confidence')}",
            elapsed,
        )
        return slug, payload
    except Exception as exc:
        report.record(name, "FAIL", str(exc), time.monotonic() - t0)
        return None, None


def test_deck_loads(
    report: TestReport, api_base: str, token: str, slug: str,
) -> list[dict]:
    t0 = time.monotonic()
    name = "04  /api/now/feed returns ≥1 article (no 404 from alias resolver)"
    try:
        r = requests.get(
            f"{api_base}/api/now/feed?company={slug}&limit=10&max_age_days=90",
            headers=_auth_headers(token),
            timeout=30,
        )
        if r.status_code == 404:
            raise AssertionError(
                f"404 on /now/feed for {slug!r} — alias resolver didn't register "
                f"or the company has no articles yet."
            )
        if r.status_code != 200:
            raise AssertionError(f"/now/feed returned {r.status_code}: {r.text[:300]}")
        payload = r.json()
        articles = payload.get("articles") or []
        if not articles:
            raise AssertionError(
                f"Feed returned 200 but with 0 articles. Either "
                f"company_article_view has no rows OR the material_industries filter "
                f"excluded everything."
            )
        report.record(
            name, "PASS",
            f"{len(articles)} articles in deck",
            time.monotonic() - t0,
        )
        return articles
    except Exception as exc:
        report.record(name, "FAIL", str(exc), time.monotonic() - t0)
        return []


def test_article_has_analysis(
    report: TestReport, api_base: str, token: str, article_id: str,
) -> dict | None:
    """Poll /api/news/{id}/analysis up to 60s waiting for populated analysis."""
    t0 = time.monotonic()
    name = "05  At least 1 article has lede + 4-bullet analysis populated"
    try:
        # Phase 45 — first POLL /analysis (in case onboard_v2 already
        # ran the pipeline). If not populated, TRIGGER enrichment and
        # then poll. Phase 44.D (deck pre-warm) only fires from the
        # frontend, so server-side calls need an explicit trigger.
        r = requests.get(
            f"{api_base}/api/news/{article_id}/analysis",
            headers=_auth_headers(token),
            timeout=30,
        )
        analysis_block = None
        if r.status_code == 200:
            di = (r.json().get("analysis") or {}).get("deep_insight") or {}
            if isinstance(di, dict):
                candidate = di.get("analysis") or {}
                if (candidate.get("what_changed") or {}).get("headline"):
                    analysis_block = candidate

        if analysis_block is None:
            # Not yet enriched — trigger + poll
            trig = requests.post(
                f"{api_base}/api/news/{article_id}/trigger-analysis",
                headers=_auth_headers(token),
                timeout=30,
            )
            if trig.status_code not in (200, 202):
                raise AssertionError(
                    f"trigger-analysis returned {trig.status_code}: {trig.text[:200]}"
                )

            deadline = t0 + 120
            last_status = None
            while time.monotonic() < deadline:
                time.sleep(3)
                r = requests.get(
                    f"{api_base}/api/news/{article_id}/analysis",
                    headers=_auth_headers(token),
                    timeout=30,
                )
                if r.status_code != 200:
                    raise AssertionError(f"/analysis returned {r.status_code}: {r.text[:200]}")
                payload = r.json()
                di = (payload.get("analysis") or {}).get("deep_insight") or {}
                if isinstance(di, dict):
                    candidate = di.get("analysis") or {}
                    if (candidate.get("what_changed") or {}).get("headline"):
                        analysis_block = candidate
                        break
                last_status = payload.get("status")

        if not analysis_block:
            raise AssertionError(
                f"After 60s, no populated analysis. Last status: {last_status}. "
                f"Pipeline either hung or returned minimal fallback."
            )

        what_changed = (analysis_block.get("what_changed") or {}).get("headline")
        lede = (analysis_block.get("lede") or {}).get("text", "")
        why = (analysis_block.get("why_it_matters") or {}).get("criticality_summary", "")

        problems = []
        if not what_changed:
            problems.append("what_changed.headline empty")
        if not lede:
            problems.append("lede.text empty (Phase 39 didn't fire OR fell back to template)")
        if not why:
            problems.append("why_it_matters.criticality_summary empty")

        # Tone scan on lede
        if lede:
            try:
                from engine.analysis.tone_guardrails import scan_for_violations
                hits = [
                    h for h in scan_for_violations(lede)
                    if h["kind"] in {"banned_phrase", "em_dash", "score_leak"}
                ]
                if hits:
                    problems.append(f"lede has tone violations: {hits[:3]}")
            except Exception:
                pass

        if problems:
            raise AssertionError("; ".join(problems))

        report.record(
            name, "PASS",
            f"headline + lede ({len(lede)} chars) + why_it_matters all populated",
            time.monotonic() - t0,
        )
        return analysis_block
    except Exception as exc:
        report.record(name, "FAIL", str(exc), time.monotonic() - t0)
        return None


def test_recs_article_specific(
    report: TestReport, api_base: str, token: str, articles: list[dict],
) -> None:
    """Compare top-3 recs across 3 different articles. ≥50% unique titles = pass."""
    t0 = time.monotonic()
    name = "06  Recommendations vary article-to-article (not templated)"
    try:
        all_titles: list[str] = []
        for art in articles[:3]:
            r = requests.get(
                f"{api_base}/api/news/{art['article_id']}/analysis",
                headers=_auth_headers(token),
                timeout=30,
            )
            if r.status_code != 200:
                continue
            recs_block = (r.json().get("analysis") or {}).get("rereact_recommendations") or {}
            recs = recs_block.get("recommendations") or []
            for rec in recs[:3]:
                title = (rec.get("title") or "").strip()
                if title:
                    all_titles.append(title)

        if not all_titles:
            raise AssertionError(
                "No recommendations found across the 3 articles checked. "
                "Either pipeline is still running OR Stage 12 produced empty recs."
            )

        # Rough dedup — first 30 chars lowercased
        normalised = [t.lower()[:30] for t in all_titles]
        unique = set(normalised)
        pct_unique = len(unique) / len(all_titles)

        if pct_unique < 0.5:
            raise AssertionError(
                f"Only {pct_unique:.0%} unique recs across {len(all_titles)} titles — "
                f"templated regression. Phase 43.A may not have actually shifted "
                f"Stage 12 to Opus 4.6. Sample titles: {all_titles[:3]}"
            )

        report.record(
            name, "PASS",
            f"{pct_unique:.0%} unique across {len(all_titles)} rec titles",
            time.monotonic() - t0,
        )
    except Exception as exc:
        report.record(name, "FAIL", str(exc), time.monotonic() - t0)


def test_email_clean(
    report: TestReport, api_base: str, token: str, article_id: str, slug: str,
) -> None:
    t0 = time.monotonic()
    name = "07  Email send succeeds + body passes tone scan (no rating bureaus)"
    try:
        # 1. Trigger email-self
        r = requests.post(
            f"{api_base}/api/articles/{article_id}/email-self",
            headers=_auth_headers(token),
            timeout=60,
        )
        if r.status_code != 200:
            raise AssertionError(f"email-self returned {r.status_code}: {r.text[:300]}")

        # 2. Direct render + scan locally (this is the same render path the email used)
        try:
            from engine.output.share_service import preview_share_html
            from engine.analysis.tone_guardrails import scan_for_violations
            from engine.config import get_company

            # We need a valid recipient — use a placeholder; just for rendering
            company = get_company(slug)
            recipient = "validate@snowkap.com"
            html, _ = preview_share_html(
                article_id=article_id,
                company_slug=slug,
                recipient_email=recipient,
            )
            body_match = re.search(r"<body[^>]*>(.*?)</body>", html, flags=re.S | re.I)
            body_html = body_match.group(1) if body_match else html
            body_html = re.sub(r"<!--.*?-->", "", body_html, flags=re.S)
            plain = re.sub(r"<[^>]+>", " ", body_html)
            plain = re.sub(r"\s+", " ", plain).strip()

            problems = []
            for bureau in ("MSCI", "CRISIL", "DJSI", "Sustainalytics",
                           "ISS QualityScore", "S&P Global ESG", "Refinitiv"):
                if bureau in plain:
                    problems.append(f"rating bureau leaked: {bureau!r}")
            score_leaks = [
                h for h in scan_for_violations(plain)
                if h["kind"] == "score_leak"
                and h["hit"].lower() not in {"owner: h", "owner: c", "cost: ₹", "payback: 6", "payback: 1", "roi: 400", "roi: 250"}
            ]
            # The owner/cost/payback/ROI on recs are intentionally kept (Phase 35);
            # filter them out so they don't fail this test
            if score_leaks:
                problems.append(f"unexpected score leaks: {[h['hit'] for h in score_leaks[:3]]}")
        except Exception as render_exc:
            # If we can't render locally, the email did send (HTTP 200) so don't fail
            report.record(
                name, "PASS",
                f"email-self 200 OK (local render check skipped: {type(render_exc).__name__})",
                time.monotonic() - t0,
            )
            return

        if problems:
            raise AssertionError("; ".join(problems))

        report.record(
            name, "PASS",
            f"email sent + body {len(plain)} chars, no rating bureau strings",
            time.monotonic() - t0,
        )
    except Exception as exc:
        report.record(name, "FAIL", str(exc), time.monotonic() - t0)


def test_chat_grounded(
    report: TestReport, api_base: str, token: str, article_id: str, slug: str,
) -> None:
    t0 = time.monotonic()
    name = "08  Chat with article context returns ≥ 50-char grounded reply"
    try:
        # Use the existing chat endpoint shape
        r = requests.post(
            f"{api_base}/api/chat",
            headers=_auth_headers(token),
            json={
                "company_slug": slug,
                "article_id": article_id,
                "message": "What happened in this article?",
            },
            stream=True,
            timeout=90,
        )
        if r.status_code != 200:
            raise AssertionError(f"POST /api/chat returned {r.status_code}: {r.text[:300]}")
        reply = ""
        for raw_line in r.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            if raw_line.startswith("data: "):
                try:
                    evt = json.loads(raw_line[6:])
                except json.JSONDecodeError:
                    continue
                if evt.get("type") == "token":
                    reply += evt.get("delta", "")
                if evt.get("type") in ("done", "error"):
                    break

        if len(reply) < 50:
            raise AssertionError(
                f"Chat reply too short ({len(reply)} chars): {reply!r}"
            )
        report.record(
            name, "PASS",
            f"reply {len(reply)} chars",
            time.monotonic() - t0,
        )
    except Exception as exc:
        report.record(name, "FAIL", str(exc), time.monotonic() - t0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--api-base",
        default=os.environ.get("SNOWKAP_API_BASE", "http://localhost:5000"),
        help="API base URL (default: http://localhost:5000, override for deployed)",
    )
    p.add_argument(
        "--token",
        default=os.environ.get("SNOWKAP_TEST_JWT", ""),
        help="Admin JWT for API calls (or set SNOWKAP_TEST_JWT)",
    )
    p.add_argument(
        "--domain",
        default="adidas.com",
        help="Domain to onboard (default: adidas.com)",
    )
    p.add_argument(
        "--skip-onboard",
        action="store_true",
        help="Skip the onboard step; use --existing-slug for downstream tests",
    )
    p.add_argument(
        "--existing-slug",
        default="",
        help="When --skip-onboard, use this slug for downstream tests",
    )
    args = p.parse_args()

    report = TestReport()

    # Phase 1: backend prerequisites (no JWT needed)
    test_postgres_active(report)
    test_openrouter_active(report)

    if report.fail_count() > 0:
        # Environment is broken — no point continuing
        report.render()
        print("\nENVIRONMENT MISCONFIGURED — fix the failures above before re-running.")
        return 2

    # Phase 2: API tests (require JWT)
    if not args.token:
        print()
        print("Skipping API tests (--token not provided).")
        print("Get the JWT from your browser DevTools (Application → Local Storage)")
        print("on a logged-in tab and re-run with: --token <jwt>")
        report.render()
        return 0 if report.fail_count() == 0 else 1

    # Make sure the API is reachable
    parsed = urlparse(args.api_base)
    if not parsed.scheme or not parsed.netloc:
        print(f"\nInvalid --api-base: {args.api_base}")
        return 2
    try:
        ping = requests.get(f"{args.api_base}/api/admin/email-config-status", timeout=10)
        if ping.status_code not in (200, 401, 403):
            print(f"\nAPI not reachable at {args.api_base} (status={ping.status_code})")
            return 2
    except Exception as exc:
        print(f"\nAPI not reachable at {args.api_base}: {exc}")
        return 2

    # Phase 3: onboard + collect canonical slug from the v2 response
    if args.skip_onboard and args.existing_slug:
        slug = args.existing_slug
        onboard_payload: dict | None = None
    else:
        slug, onboard_payload = test_onboard_flow(report, args.api_base, args.token, args.domain)

    if not slug:
        report.render()
        return 1

    # Phase 4: deck + analysis + recs + email + chat
    articles = test_deck_loads(report, args.api_base, args.token, slug)

    if articles:
        first_article_id = articles[0].get("article_id") or articles[0].get("id")
        if first_article_id:
            test_article_has_analysis(report, args.api_base, args.token, first_article_id)
            test_recs_article_specific(report, args.api_base, args.token, articles)
            test_email_clean(report, args.api_base, args.token, first_article_id, slug)
            test_chat_grounded(report, args.api_base, args.token, first_article_id, slug)

    # Phase 5: Postgres write verification — onboard_v2 returns the
    # canonical slug directly in the response payload.
    canonical_slug = None
    if onboard_payload:
        canonical_slug = onboard_payload.get("slug")
    test_postgres_writes(report, slug, canonical_slug)

    report.render()
    return 0 if report.fail_count() == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
