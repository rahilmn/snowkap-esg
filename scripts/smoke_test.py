"""Phase 16.3 — End-to-end smoke test.

Walks the entire shipped stack from boot to share-email-attempt. Run this
as the final gate before promoting a build to production OR before kicking
off a pilot week. Exits 0 on success, non-zero with a punch list on
failure. Designed to be cron-able (nightly or pre-deploy).

Steps:
  1. API boots in dev mode (no SNOWKAP_ENV) and serves /health
  2. Production env guard correctly fails when secrets are missing
  3. SQLite WAL mode is active
  4. Ontology graph loads (≥ 5,000 triples)
  5. Auth: signed JWT verifies + tampered JWT rejected
  6. /api/companies returns the 7 target companies
  7. /api/news/stats includes active_signals_count (Phase 13 B8)
  8. /api/admin/email-config-status returns enabled when RESEND_API_KEY set
  9. SQLite article_index has > 0 rows (i.e. ingestion has run)
 10. Latest fuzz report exists and shows ≥ 8/10 pass

Usage:
    python scripts/smoke_test.py
    python scripts/smoke_test.py --skip-network  # offline subset
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Test framework
# ---------------------------------------------------------------------------

GREEN = "\033[32m" if sys.stdout.isatty() else ""
RED = "\033[31m" if sys.stdout.isatty() else ""
YELLOW = "\033[33m" if sys.stdout.isatty() else ""
RESET = "\033[0m" if sys.stdout.isatty() else ""

_failures: list[str] = []
_passes: list[str] = []


def check(name: str) -> Callable:
    """Decorator that runs a single smoke check, prints OK/FAIL, records
    failures so the script can report a punch list at the end."""
    def wrap(fn: Callable[[], None]) -> Callable[[], None]:
        def runner() -> None:
            try:
                fn()
                _passes.append(name)
                print(f"  {GREEN}OK  {RESET}{name}")
            except AssertionError as exc:
                _failures.append(f"{name}: {exc}")
                print(f"  {RED}FAIL{RESET} {name}\n        {exc}")
            except Exception as exc:  # noqa: BLE001
                _failures.append(f"{name}: {type(exc).__name__}: {exc}")
                print(f"  {RED}FAIL{RESET} {name}\n        {type(exc).__name__}: {exc}")
        return runner
    return wrap


# ---------------------------------------------------------------------------
# Checks (1–10)
# ---------------------------------------------------------------------------


def _load_dotenv() -> None:
    p = _ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        # Don't overwrite if already set (production env wins)
        os.environ.setdefault(k.strip(), v.strip())


@check("1. API boots in dev mode + /health responds 200")
def t01_health() -> None:
    os.environ.pop("SNOWKAP_ENV", None)
    os.environ.pop("ENV", None)
    from importlib import reload
    import api.main as m
    reload(m)
    from fastapi.testclient import TestClient
    with TestClient(m.app) as client:
        r = client.get("/health")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"
        body = r.json()
        assert body.get("status") == "ok"


@check("2. Production env guard fails-fast on missing JWT_SECRET")
def t02_env_guard() -> None:
    from importlib import reload
    import api.main as m
    reload(m)
    saved = {k: os.environ.get(k) for k in ("SNOWKAP_ENV", "JWT_SECRET", "OPENAI_API_KEY", "RESEND_API_KEY", "SNOWKAP_FROM_ADDRESS", "SNOWKAP_API_KEY", "REQUIRE_SIGNED_JWT")}
    try:
        os.environ["SNOWKAP_ENV"] = "production"
        os.environ["JWT_SECRET"] = ""  # missing
        os.environ.setdefault("OPENAI_API_KEY", "sk-real")
        os.environ.setdefault("RESEND_API_KEY", "re_real")
        os.environ.setdefault("SNOWKAP_FROM_ADDRESS", "test@example.com")
        os.environ.setdefault("SNOWKAP_API_KEY", "real-api-key-1234567890abcd")
        os.environ.setdefault("REQUIRE_SIGNED_JWT", "1")
        try:
            m._check_production_env()
        except RuntimeError as exc:
            assert "JWT_SECRET" in str(exc), f"Expected JWT_SECRET in error, got: {exc}"
            return
        raise AssertionError("env guard did not raise on missing JWT_SECRET")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@check("3. SQLite WAL mode is enabled on data/snowkap.db")
def t03_wal() -> None:
    import sqlite3
    db = _ROOT / "data" / "snowkap.db"
    if not db.exists():
        # First boot — creating it on demand is fine, just verify the bootstrap
        from engine.index.sqlite_index import ensure_schema
        ensure_schema()
    with sqlite3.connect(str(db)) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal", f"Expected WAL, got {mode}"


@check("4. Ontology graph loads with ≥ 5,000 triples")
def t04_ontology() -> None:
    from engine.ontology.graph import OntologyGraph
    g = OntologyGraph().load()
    n = len(g.graph)
    assert n >= 5000, f"Ontology only has {n} triples (expected ≥ 5,000)"


@check("5. Signed JWT verification + tamper rejection")
def t05_jwt() -> None:
    os.environ.setdefault("JWT_SECRET", "smoke-test-secret-xxxxxxxxxxxxxxxxx")
    from api.auth_context import mint_bearer, decode_bearer
    tok = mint_bearer({"sub": "smoke@snowkap.com", "permissions": ["read"]})
    claims = decode_bearer(f"Bearer {tok}")
    assert claims.get("sub") == "smoke@snowkap.com"
    # Tamper the payload
    parts = tok.split(".")
    assert len(parts) == 3
    bad = ".".join([parts[0], parts[1] + "X", parts[2]])
    os.environ["REQUIRE_SIGNED_JWT"] = "1"
    bad_claims = decode_bearer(f"Bearer {bad}")
    assert not bad_claims, f"Tampered token should produce empty claims; got {bad_claims}"


@check("6. /api/companies returns the 7 target companies")
def t06_companies() -> None:
    from importlib import reload
    import api.main as m
    reload(m)
    from fastapi.testclient import TestClient
    os.environ.setdefault("SNOWKAP_API_KEY", "smoke-test-key")
    with TestClient(m.app) as client:
        r = client.get("/api/companies", headers={"X-API-Key": os.environ["SNOWKAP_API_KEY"]})
        assert r.status_code == 200, f"GET /api/companies → {r.status_code}: {r.text[:200]}"
        body = r.json()
        # Different shapes possible — extract a list count tolerantly
        if isinstance(body, list):
            count = len(body)
        elif isinstance(body, dict) and "companies" in body:
            count = len(body["companies"])
        elif isinstance(body, dict) and "items" in body:
            count = len(body["items"])
        else:
            count = 0
        assert count >= 7, f"Expected ≥ 7 companies, got {count}: {str(body)[:200]}"


@check("7. /api/news/stats includes active_signals_count (Phase 13 B8)")
def t07_active_signals() -> None:
    from importlib import reload
    import api.main as m
    reload(m)
    from fastapi.testclient import TestClient
    os.environ.setdefault("SNOWKAP_API_KEY", "smoke-test-key")
    with TestClient(m.app) as client:
        r = client.get("/api/news/stats", headers={"X-API-Key": os.environ["SNOWKAP_API_KEY"]})
        assert r.status_code == 200
        body = r.json()
        assert "active_signals_count" in body, f"Missing active_signals_count: {body}"
        assert isinstance(body["active_signals_count"], int)


@check("8. /api/admin/email-config-status reflects RESEND_API_KEY")
def t08_email_config() -> None:
    from importlib import reload
    import api.main as m
    reload(m)
    from fastapi.testclient import TestClient
    os.environ.setdefault("SNOWKAP_API_KEY", "smoke-test-key")
    with TestClient(m.app) as client:
        r = client.get("/api/admin/email-config-status", headers={"X-API-Key": os.environ["SNOWKAP_API_KEY"]})
        assert r.status_code == 200
        body = r.json()
        assert "enabled" in body
        # If the env has a real Resend key set, expect enabled=True
        api_key = os.environ.get("RESEND_API_KEY", "").strip()
        if api_key and not any(api_key.lower().startswith(p) for p in ("your_", "changeme", "placeholder")):
            assert body["enabled"] is True, f"RESEND_API_KEY is set but endpoint reports disabled: {body}"


@check("9. SQLite article_index has indexed articles")
def t09_article_index() -> None:
    from engine.index.sqlite_index import count
    n = count()
    assert n > 0, (
        f"article_index is empty ({n} rows). Run `python engine/main.py reindex` "
        f"or trigger ingestion before running smoke."
    )


@check("10. Latest fuzz report shows ≥ 8/10 pass")
def t10_fuzz_report() -> None:
    reports_dir = _ROOT / "data" / "fuzz_reports"
    assert reports_dir.exists(), "No fuzz_reports/ directory — run scripts/fuzz_pipeline.py first"
    json_reports = sorted(
        reports_dir.glob("fuzz_*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    assert json_reports, "No fuzz_*.json reports found"
    latest = json.loads(json_reports[0].read_text(encoding="utf-8"))
    passed = latest.get("passed", 0)
    total = latest.get("total_articles", 0)
    assert total > 0
    assert passed >= 8, (
        f"Latest fuzz report ({json_reports[0].name}) has only {passed}/{total} passing — "
        f"need ≥ 8/10 for ship-readiness"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-network", action="store_true", help="Skip checks that hit external APIs")
    args = parser.parse_args()

    _load_dotenv()

    print("Snowkap ESG end-to-end smoke test")
    print("=" * 60)

    checks: list[Callable[[], None]] = [
        t01_health, t02_env_guard, t03_wal, t04_ontology, t05_jwt,
        t06_companies, t07_active_signals, t08_email_config,
        t09_article_index, t10_fuzz_report,
    ]
    for c in checks:
        c()

    print()
    print("=" * 60)
    print(f"Result: {GREEN}{len(_passes)} pass{RESET} / {RED}{len(_failures)} fail{RESET}")
    if _failures:
        print()
        print("Punch list:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"{GREEN}All smoke checks pass — ready to ship.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
