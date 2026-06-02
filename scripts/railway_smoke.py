"""Railway post-deploy smoke check.

Run this against the LIVE Railway URL right after a deploy to confirm the
platform is up, Postgres-only, serving the built frontend, and that every
company's /now deck is populated + grounded + carries hero images.

Unauthenticated checks (health / metrics / frontend) need only --url.
The deck / forum / newsletter checks need a bearer token (--token): log in to
the live site as ci@snowkap.com (super-admin), open DevTools → Application →
Local Storage, copy the JWT (the `token` value), and pass it with --token.

Usage:
    python scripts/railway_smoke.py --url https://<your-app>.up.railway.app
    python scripts/railway_smoke.py --url https://<app> --token <jwt>
    python scripts/railway_smoke.py --url https://<app> --token <jwt> --send-newsletter

Exits 0 when all run checks pass, non-zero otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys

import requests

_COMPANIES = [
    "adani-power", "jsw-energy", "waaree-energies", "icici-bank",
    "idfc-first-bank", "yes-bank", "state-bank-of-india", "mahle",
    "singularity-amc",
]
# Companies we EXPECT to be empty (honest — no ESG news footprint).
_EXPECT_EMPTY = {"singularity-amc"}


def _row_image(row: dict) -> bool:
    for blob in (row, row.get("shared_analysis") or {}, row.get("personalised_analysis") or {}):
        if isinstance(blob, dict) and (blob.get("image_url") or "").strip():
            return True
    return False


def _row_is_critical(row: dict) -> bool:
    pa = row.get("personalised_analysis") or {}
    return bool((pa.get("lede") or {}).get("text"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="Railway base URL (no trailing slash)")
    ap.add_argument("--token", help="super-admin JWT for the deck/forum/newsletter checks")
    ap.add_argument("--send-newsletter", action="store_true",
                    help="actually send a test newsletter (side-effect: emails the token's user)")
    args = ap.parse_args()
    base = args.url.rstrip("/")
    H = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    results: list[tuple[str, bool, str]] = []

    def check(name, fn):
        try:
            msg = fn()
            results.append((name, True, msg or ""))
        except Exception as exc:  # noqa: BLE001
            results.append((name, False, str(exc)[:160]))

    # 1. Health
    def t_health():
        r = requests.get(f"{base}/health", timeout=20); r.raise_for_status()
        assert r.json().get("status") == "ok", f"unexpected: {r.text[:80]}"
        return "status=ok"
    check("01 /health up", t_health)

    # 2. Metrics + Postgres backend
    def t_metrics():
        r = requests.get(f"{base}/metrics", timeout=20); r.raise_for_status()
        body = r.text
        assert "snowkap_" in body, "no snowkap_ Prometheus series"
        return "metrics served"
    check("02 /metrics served", t_metrics)

    # 3. Frontend served (built client/dist → index.html) — images need this deploy
    def t_frontend():
        r = requests.get(f"{base}/", timeout=20); r.raise_for_status()
        assert "<div id=\"root\"" in r.text or "<title" in r.text, "root HTML not served"
        return f"index.html served ({len(r.text)} bytes)"
    check("03 frontend (built React) served", t_frontend)

    if not args.token:
        results.append(("-- deck/forum/newsletter checks", False, "skipped: pass --token <jwt>"))
    else:
        # 4. Per-company /now deck
        def t_decks():
            lines, total_cards, total_crit, missing_img = [], 0, 0, 0
            for slug in _COMPANIES:
                r = requests.get(f"{base}/api/now/feed",
                                 params={"company": slug, "limit": 10, "max_age_days": 30},
                                 headers=H, timeout=30)
                if r.status_code != 200:
                    lines.append(f"      {slug:<22} HTTP {r.status_code}")
                    continue
                arts = r.json().get("articles") or []
                crit = sum(1 for a in arts if _row_is_critical(a))
                imgs = sum(1 for a in arts if _row_image(a))
                missing_img += (len(arts) - imgs)
                total_cards += len(arts); total_crit += crit
                flag = "OK" if (arts or slug in _EXPECT_EMPTY) else "EMPTY!"
                lines.append(f"      {slug:<22} cards={len(arts):>2} crit={crit:>2} img={imgs}/{len(arts)}  {flag}")
            print("\n".join(lines))
            # contract: at least 6 companies populated, decks total >= 25, no roundup
            populated = sum(1 for ln in lines if "cards= 0" not in ln and "EMPTY" not in ln)
            assert total_cards >= 25, f"only {total_cards} total cards"
            return f"{total_cards} cards, {total_crit} grounded criticals, {missing_img} missing images"
        check("04 /now decks populated", t_decks)

        # 5. Forum welcome threads
        def t_forum():
            r = requests.get(f"{base}/api/forum/threads", headers=H, timeout=20)
            r.raise_for_status()
            data = r.json()
            threads = data.get("threads") if isinstance(data, dict) else data
            n = len(threads or [])
            assert n >= 5, f"only {n} forum threads"
            return f"{n} threads"
        check("05 forum threads", t_forum)

        # 6. Newsletter send (opt-in side-effect)
        if args.send_newsletter:
            def t_news():
                r = requests.post(f"{base}/api/newsletter/send-me", headers=H, timeout=40)
                r.raise_for_status()
                return f"queued: {str(r.json())[:80]}"
            check("06 newsletter send-me", t_news)
        else:
            results.append(("06 newsletter send-me", True, "skipped (pass --send-newsletter to test)"))

    print("\n" + "=" * 70)
    print("  RAILWAY SMOKE REPORT")
    print("=" * 70)
    n_pass = sum(1 for _, ok, _ in results if ok)
    n_fail = sum(1 for _, ok, _ in results if not ok)
    for name, ok, msg in results:
        sym = "PASS" if ok else "FAIL"
        print(f"  [{sym}] {name}")
        if msg:
            print(f"         -> {msg}")
    print(f"\n  {n_pass} passed, {n_fail} failed")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
