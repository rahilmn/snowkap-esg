# Replit Pro Launch Runbook — `powerofnow.snowkap.co.in`

> Step-by-step deploy of the Snowkap ESG Intelligence Engine to Replit Pro
> for the internal-team pilot. ~30 minutes from "create repl" to "team
> can log in".

This is a manual-Replit-UI runbook. The code side is done; everything
below is clicks-in-Replit + DNS work.

---

## Pre-deploy gate — before you start

All four MUST be green or **STOP**:

- [ ] `python scripts/smoke_test.py` → 10/10
- [ ] `python scripts/fuzz_pipeline.py --slo-fail-pct 5` → ≥ 9/10
- [ ] `cd client && npm run build` succeeds
- [ ] `cd client && npm run lint` clean (0 errors)
- [ ] **Resend domain `newsletter@snowkap.co.in` is verified** in
      [resend.com/domains](https://resend.com/domains) — confirm SPF/DKIM
      green. Without this, ALL share-email sends will fail with HTTP 403.
- [ ] **NewsAPI.ai paid-tier key in hand**. The free tier doesn't return
      enough article body to run the verifier layers.

---

## Step 1 — Create the Replit Pro workspace (5 min)

1. Log in at [replit.com](https://replit.com) with the team account.
2. Click **Create Repl** → **Import from GitHub**.
3. Authorize the Snowkap GitHub org if not already done.
4. Pick the `snowkap-esg` repo (default branch).
5. **Repl type**: Auto-detected as Python + Node from `Dockerfile` +
   `.replit`. Confirm the repl boots (red banner is fine for now —
   we haven't set secrets yet).

> The `.replit` file in the repo is now correctly wired (Track A) for
> the modern stack: `bash run.sh` → installs deps → builds frontend
> → boots `uvicorn api.main:app` on port 8000.

---

## Step 2 — Set Replit Secrets (10 min)

Open the **🔒 Secrets** tab in Replit (left sidebar). Add each of these
as a separate secret (NEVER inline in `.replit` or `.env`):

```
OPENAI_API_KEY=<paste from team password vault>
RESEND_API_KEY=<paste>
NEWSAPI_AI_API_KEY=<paste paid-tier key>
JWT_SECRET=<32+ random chars — generate via `openssl rand -hex 32`>
SNOWKAP_API_KEY=<32+ random chars — same approach>

SNOWKAP_FROM_ADDRESS=Snowkap ESG <newsletter@snowkap.co.in>
SNOWKAP_INTERNAL_EMAILS=sales@snowkap.co.in,ci@snowkap.com,newsletter@snowkap.co.in,<analyst1>@snowkap.co.in,<analyst2>@snowkap.co.in,<analyst3>@snowkap.co.in,<analyst4>@snowkap.co.in,<analyst5>@snowkap.co.in

SNOWKAP_ENV=production
REQUIRE_SIGNED_JWT=1
SNOWKAP_INPROCESS_SCHEDULER=1

# Optional but recommended
SENTRY_DSN=<from sentry.io>
```

**Critical**: every email in `SNOWKAP_INTERNAL_EMAILS` MUST end in
`@snowkap.com` or `@snowkap.co.in`, OR the user gets a regular-user
JWT (no share button). This is enforced by
`api/auth_context.py::is_snowkap_super_admin`.

After saving secrets, click **Run** to restart the repl. Watch the
**Console** tab — you should see:

```
=== SNOWKAP ESG Intelligence Engine ===
...
api startup: auth=enabled
ontology eager-loaded: 8222 triples
in-process scheduler started (ingest_every=60min, promote_every=30min)
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

If you see `RuntimeError: ontology load failed at boot` or `production
secrets missing`, fix the secret and restart. The boot is fail-fast
on purpose.

---

## Step 3 — Custom domain `powerofnow.snowkap.co.in` (10 min)

1. **Replit Deployments tab** → **Add custom domain** → enter
   `powerofnow.snowkap.co.in`.
2. Replit shows you a CNAME target like
   `<repl-id>.<region>.replit.app`. Copy it.
3. Go to the snowkap.co.in DNS provider (likely Cloudflare or AWS
   Route 53). Add a CNAME record:
   - **Name**: `powerofnow`
   - **Target**: `<the value Replit gave you>`
   - **TTL**: 5 min (lower it temporarily; raise later if stable)
   - **Proxy**: OFF if Cloudflare (Replit handles SSL)
4. Wait 2-5 min for DNS to propagate.
5. In the Replit Deployments tab, click **Verify domain**. SSL is
   auto-provisioned via Let's Encrypt — takes ~1 min.
6. Confirm with `curl -I https://powerofnow.snowkap.co.in/health`
   → 200.

---

## Step 4 — Always-On + scheduled tasks (5 min)

### 4.1 Always-On

Replit Deployments → toggle **Always On**. This keeps the deployment
warm 24/7 — required for the in-process scheduler (60-min ingest +
30-min discovery promote) to run continuously.

### 4.2 Scheduled tasks (belt-and-braces)

In the **Scheduled Tasks** tab:

- **Daily ingest** at 06:00 IST:
  ```
  Schedule: 0 0 * * *   (UTC — that's 05:30 IST)
  Command:  bash -c "cd /home/runner/$REPL_SLUG && python engine/scheduler.py --once"
  ```
- **Hourly SQLite backup**:
  ```
  Schedule: 0 * * * *
  Command:  bash -c "cd /home/runner/$REPL_SLUG && bash scripts/backup_db.sh"
  ```

These are redundant with the in-process scheduler but give us a safety
net if the API process dies between auto-restarts.

---

## Step 5 — First-time data warmup (5 min)

Open the **Shell** tab (Ctrl+`) and run:

```bash
# Pull fresh articles for all 7 companies
python engine/main.py ingest --all --max 10 --limit 5

# Drain the discovery buffer once (Phase 19 — was sitting idle since Apr 23)
python -c "from engine.ontology.discovery.promoter import batch_promote; print(batch_promote())"

# Confirm article inventory
python -c "from engine.index.sqlite_index import count; \
  print('Total articles:', count()); \
  print('HOME tier:', count(tier='HOME'))"
```

You should see at least 50 total articles and ≥ 5 HOME-tier articles
across the 7 companies. If a company has zero HOME articles, run the
ingest again a few hours later — sometimes the news APIs return
sparse results until the next news cycle.

---

## Step 6 — Smoke test the live URL (5 min)

From your laptop (NOT inside Replit):

```bash
# Public health
curl -I https://powerofnow.snowkap.co.in/health
# Expect: HTTP/2 200

# OpenAPI spec contains reanalyze + onboard + share + email-config endpoints
curl -s https://powerofnow.snowkap.co.in/openapi.json | \
  python -c "import json,sys; spec=json.load(sys.stdin); \
    print(sorted([p for p in spec['paths'] if 'admin' in p or 'share' in p]))"
# Expect: 8+ paths including /api/admin/companies/{slug}/reanalyze
```

Then open `https://powerofnow.snowkap.co.in` in a browser:

1. Login as `<your email>@snowkap.co.in` + designation.
2. Dashboard loads, 7 companies in switcher.
3. Click an HOME-tier article.
4. Toggle CFO ↔ CEO ↔ ESG Analyst — all 3 render.
5. Click **Share via email** → enter `ci@snowkap.com` → send.
6. Email lands within 30 seconds in `ci@snowkap.com` inbox with the
   dark-card layout + SNOWKAP logo.
7. DevTools console: 0 errors. Network tab: 0 5xx.

If all 6 pass → Track A complete. Hand the URL +
`docs/ANALYST_GUIDE.md` to the team.

---

## Step 7 — Continuous-loop verification (60-90 min wait)

Wait for the in-process scheduler to fire its first jobs:

- **After 60 min**: Replit Console should show
  `scheduler: starting scheduled ingestion`. Re-check `count(tier='HOME')`
  in the shell — it should grow.
- **After 90 min** (60 + 30): Replit Console should show
  `scheduler: discovery promoter ran -> {'promoted': N, ...}`. Check
  `data/ontology/discovery_audit.jsonl` — a new line should appear if
  any candidates met the threshold.

If neither fires, the in-process scheduler didn't start. Check
`SNOWKAP_INPROCESS_SCHEDULER=1` in Secrets and that the boot log
includes `in-process scheduler started`.

---

## Rollback plan

If anything breaks at launch:

1. **Replit Deployments** → **Versions** → roll back to previous green
   build. Replit keeps the last 5 deployment versions.
2. Notify the analyst Slack — "Powerofnow ESG is briefly down for
   rollback, ETA 5 min".
3. Triage from the rollback build — the API is back up while you
   investigate the broken commit.

If the app is critically broken AND rollback fails:

- Disable the deployment (Replit Deployments → Stop).
- Show the team `https://powerofnow.snowkap.co.in/maintenance.html`
  (TODO: add a static maintenance page in a follow-up).
- Engineering triages from local.

---

## Costs (rough, per month)

| Service | Tier | Estimated cost |
|---|---|---|
| Replit Pro Always-On | $25/mo (existing account) | $25 |
| OpenAI (gpt-4.1 + gpt-4.1-mini) | Pay-as-you-go | $30-80 (5 analysts, 60-min ingest) |
| Resend | Free tier (3K/mo) → $20/mo if exceeded | $0-20 |
| NewsAPI.ai | Paid plan, ~10K articles/mo | $150 |
| Sentry | Free tier (5K events) | $0 |
| **Total** | | **$205-275/mo** |

OpenAI cost-per-article logged in `engine/models/llm_calls.py` — pull
a daily summary via `SELECT SUM(cost_usd) FROM llm_calls WHERE
created_at >= date('now','-1 day')` if you want to track spend.

---

## Post-launch checklist (week 1)

After 2-3 days of internal-team usage:

- [ ] Audit `data/ontology/discovery_audit.jsonl` — at least one new
      promotion should have happened automatically (Phase 19 self-evolving
      ontology).
- [ ] Review the verifier-warnings panel on a few articles — are the
      semantic ₹ drift / reused-number warnings appearing on real
      articles, or did the team find new edge cases?
- [ ] Check Sentry for any unhandled exceptions.
- [ ] Confirm hourly backup is running:
      `ls -lh data/backups/snowkap.*.db | head` — should show 24+
      hourly snapshots after 1 day.
- [ ] Run `python scripts/fuzz_pipeline.py --slo-fail-pct 5` weekly.
- [ ] Schedule the **Track C media-demo dress rehearsal** for day 4-5
      (per the launch plan). Pre-warm the hero articles and walk the
      script.
