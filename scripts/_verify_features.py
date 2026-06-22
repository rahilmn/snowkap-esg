"""Verify the Ask (chat), Forum, and Wiki features work end-to-end via the real
HTTP routes (FastAPI TestClient) against prod Supabase, with a signed JWT.

Read-only except Forum (creates one test thread + reply, then leaves it — forum
content is user data, not a canonical tenant). Prints a PASS/FAIL per feature.
"""
from __future__ import annotations

import os
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass
from dotenv import load_dotenv

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")
os.environ.setdefault("ENVIRONMENT", "production")
os.environ["OPENROUTER_API_KEY"] = ""          # OpenAI-direct (gpt-5-mini chat)
os.environ.pop("SNOWKAP_INPROCESS_SCHEDULER", None)   # no background scheduler in the test
os.environ["SNOWKAP_WIKI_BUILD_ON_STARTUP"] = "1"    # build the wiki on boot (prod default)
os.environ.setdefault("REQUIRE_SIGNED_JWT", "1")

from fastapi.testclient import TestClient  # noqa: E402
from api.main import app  # noqa: E402
from api.auth_context import mint_bearer  # noqa: E402

TOKEN = mint_bearer({
    "email": "ci@snowkap.com",
    "permissions": ["super_admin"],
    "company_slug": "idfc-first-bank",
    "tenant": "idfc-first-bank",
})
H = {"Authorization": f"Bearer {TOKEN}"}


def _line(name, ok, detail):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def main() -> int:
    results = []
    with TestClient(app) as c:
        # ---- FORUM ---------------------------------------------------------
        print("== FORUM ==")
        r = c.get("/api/forum/threads", headers=H)
        results.append(_line("list threads", r.status_code == 200,
                             f"{r.status_code} count={r.json().get('count') if r.status_code==200 else r.text[:80]}"))
        tid = None
        try:
            r = c.post("/api/forum/threads", headers=H,
                       json={"title": "Feature check — IDFC CREST fraud", "body": "Verifying forum works.", "tag": "test"})
            ok = r.status_code in (200, 201)
            if ok:
                tid = ((r.json().get("thread") or {}).get("id")) or r.json().get("id")
            results.append(_line("create thread", ok, f"{r.status_code} id={tid}"))
        except Exception as exc:
            results.append(_line("create thread", False, repr(exc)))
        if tid:
            r = c.get(f"/api/forum/threads/{tid}", headers=H)
            results.append(_line("get thread", r.status_code == 200, str(r.status_code)))
            r = c.post(f"/api/forum/threads/{tid}/replies", headers=H, json={"body": "Test reply."})
            results.append(_line("post reply", r.status_code in (200, 201), str(r.status_code)))

        # ---- WIKI ----------------------------------------------------------
        print("== WIKI ==")
        r = c.get("/api/wiki/search", params={"q": "fraud governance", "top_k": 5}, headers=H)
        j = r.json() if r.status_code == 200 else {}
        hits = j.get("hits", [])
        missing = j.get("wiki_root_missing", False)
        results.append(_line("search", r.status_code == 200 and not missing,
                             f"{r.status_code} hits={len(hits)} root_missing={missing}"))
        if hits:
            first = hits[0]
            slug = first.get("slug") or first.get("path") or first.get("id")
            rp = c.get("/api/wiki/page", params={"slug": slug, "path": slug}, headers=H)
            results.append(_line("open page", rp.status_code == 200, f"{rp.status_code} slug={slug}"))
        else:
            results.append(_line("open page", False, "no hits to open"))

        # ---- ASK (chat, SSE) ----------------------------------------------
        print("== ASK (chat) ==")
        try:
            import json as _json
            with c.stream("POST", "/api/chat", headers=H, json={
                "message": "In one sentence, what is the IDFC First Bank CREST fraud case about?",
                "company_slug": "idfc-first-bank",
            }) as resp:
                body = ""
                for chunk in resp.iter_text():
                    body += chunk
                    if len(body) > 12000:
                        break
            # Reassemble the answer from the SSE 'token' event deltas only.
            answer = ""
            for ln in body.splitlines():
                ln = ln.strip()
                if ln.startswith("data:"):
                    try:
                        payload = _json.loads(ln[5:].strip())
                        if isinstance(payload, dict) and "delta" in payload:
                            answer += str(payload["delta"])
                    except Exception:
                        pass
            ok = resp.status_code == 200 and len(answer.split()) >= 6
            grounded = any(k in answer.lower() for k in ("crest", "idfc", "fraud", "cbi", "83"))
            results.append(_line("chat replies", ok, f"{resp.status_code} answer_chars={len(answer)}"))
            results.append(_line("chat grounded (CREST/IDFC/CBI/fraud)", grounded, answer.strip()[:140] or "(empty)"))
        except Exception as exc:
            results.append(_line("chat", False, repr(exc)))

    print(f"\n== {sum(results)}/{len(results)} checks passed ==")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
