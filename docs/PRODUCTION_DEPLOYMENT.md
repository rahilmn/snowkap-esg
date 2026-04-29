# Snowkap ESG — Production Deployment Runbook

Last updated: 2026-04-27 (Phase 16)

This runbook covers everything needed to take the Snowkap ESG engine from a
local dev machine to a hosted production deployment. Target: a small VPS or
PaaS instance (Replit Pro, Railway, Render, Fly.io, or a 4-core / 8 GB VM).

---

## 1. Pre-deployment checklist

Before promoting any commit to production, every line below must be ✅:

- [ ] **Test suite green**: `pytest tests/test_phase11_*.py tests/test_phase12_*.py tests/test_phase13_*.py tests/test_phase14_*.py tests/test_phase15_*.py -s -p no:cacheprovider` returns 122/122 (or current count) pass
- [ ] **Lint green**: `cd client && npx eslint src/` returns `0 errors` (warnings allowed)
- [ ] **Frontend builds**: `cd client && npm run build` succeeds + emits `client/dist/`
- [ ] **Fuzz harness ≥ 8/10**: `python scripts/fuzz_pipeline.py --slo-fail-pct 30` exits 0
- [ ] **No `.env` in git** — `git status` shows `.env` ignored, `git ls-files | grep .env$` empty
- [ ] **JWT_SECRET ≥ 32 chars** in production `.env`
- [ ] **REQUIRE_SIGNED_JWT=1** in production `.env`
- [ ] **SNOWKAP_ENV=production** in production `.env`

---

## 2. Required environment variables

Production `.env` file. All values must be real (not placeholders), or the
boot-time `_check_production_env()` guard will refuse to start the API.

| Variable | Purpose | Example |
|---|---|---|
| `SNOWKAP_ENV` | Tells the boot guard to require all secrets | `production` |
| `JWT_SECRET` | HS256 signing secret for bearer tokens | `<32+ random chars>` |
| `REQUIRE_SIGNED_JWT` | Reject unsigned tokens; flip to `1` post-rollout window | `1` |
| `SNOWKAP_API_KEY` | Legacy `X-API-Key` middleware; still active for service-to-service | `<random key>` |
| `OPENAI_API_KEY` | Stages 1-2, 10, 12 of the pipeline | `sk-proj-...` |
| `RESEND_API_KEY` | Outbound email (Phase 9 share + Phase 10 drip + Phase 11C newsletter) | `re_...` |
| `SNOWKAP_FROM_ADDRESS` | Verified sender on the Resend domain | `Snowkap ESG <newsletter@snowkap.co.in>` |
| `NEWSAPI_AI_KEY` | Full-text article ingestion (Phase 17b) | UUID from eventregistry.org |
| `SENTRY_DSN` | Optional — error reporting + PII scrubbing | `https://...@sentry.io/...` |
| `SENTRY_ENV` | Optional — `production` / `staging` tag for events | `production` |

**Boot-time guard** (`api/main.py::_check_production_env`) verifies:
- All required vars are set + not placeholders (`your_*`, `changeme`, `<...>`)
- `JWT_SECRET` is ≥ 32 chars (HS256 entropy)
- `REQUIRE_SIGNED_JWT=1` (otherwise unsigned tokens leak through)

If the guard fails, the API refuses to start. Fix the env and restart.

---

## 3. Deployment flow

### 3a. Provision the host

Any Linux VM with:
- Python 3.12+
- Node 20+ + npm
- 4 GB RAM minimum (8 GB if you're running ingestion on the same box)
- 20 GB disk (ontology + outputs + SQLite + backups)
- A persistent volume for `data/` (do NOT use ephemeral local disk)

### 3b. Initial setup

```bash
git clone <repo> /opt/snowkap-esg
cd /opt/snowkap-esg

# Python deps
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Frontend build (run on host or in CI)
cd client && npm install && npm run build && cd ..

# Ontology + DB warmup
python -m engine.ontology.seeder         # creates the persistent .ttl files
python engine/main.py reindex            # populates SQLite from data/outputs/
```

### 3c. Configure systemd (or supervisor / docker-compose)

Two services need to run continuously:

**Service 1 — API** (`/etc/systemd/system/snowkap-api.service`):
```ini
[Unit]
Description=Snowkap ESG API
After=network.target

[Service]
Type=simple
User=snowkap
WorkingDirectory=/opt/snowkap-esg
EnvironmentFile=/opt/snowkap-esg/.env
ExecStart=/opt/snowkap-esg/.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/snowkap-api.log
StandardError=append:/var/log/snowkap-api.log

[Install]
WantedBy=multi-user.target
```

**Service 2 — Cron / scheduler** (Phase 10 drip + nightly fuzz):
```cron
# /etc/cron.d/snowkap
# Drip scheduler (every 15 min — Phase 10 campaign_runner picks up due sends)
*/15 * * * *   snowkap   cd /opt/snowkap-esg && /opt/snowkap-esg/.venv/bin/python -m engine.output.campaign_runner --tick >> /var/log/snowkap-runner.log 2>&1

# Hourly SQLite backup (Phase 11A)
0 * * * *      snowkap   cd /opt/snowkap-esg && bash scripts/backup_db.sh >> /var/log/snowkap-backup.log 2>&1

# Nightly fuzz harness — fails the run + emits to alerts if regression > 5%
0 2 * * *      snowkap   cd /opt/snowkap-esg && /opt/snowkap-esg/.venv/bin/python scripts/fuzz_pipeline.py --slo-fail-pct 5 >> /var/log/snowkap-fuzz.log 2>&1 || mail -s "Snowkap fuzz regression" alerts@snowkap.co.in < /var/log/snowkap-fuzz.log
```

### 3d. Reverse proxy (Nginx / Caddy)

Caddy example (TLS auto-managed):
```
api.snowkap.co.in {
  reverse_proxy localhost:8000
}

app.snowkap.co.in {
  root * /opt/snowkap-esg/client/dist
  try_files {path} /index.html
  file_server
}
```

In `client/.env.production` set `VITE_API_URL=https://api.snowkap.co.in/api`.

### 3e. Smoke test post-deploy

```bash
# Health
curl https://api.snowkap.co.in/health
# Expected: {"status":"ok","service":"snowkap-esg-api","version":"0.2.0"}

# Auth (with X-API-Key)
curl -H "X-API-Key: $SNOWKAP_API_KEY" https://api.snowkap.co.in/api/companies
# Expected: 7 companies + any onboarded prospects

# Email config status
curl -H "X-API-Key: $SNOWKAP_API_KEY" https://api.snowkap.co.in/api/admin/email-config-status
# Expected: {"enabled":true,"sender":"Snowkap ESG <newsletter@snowkap.co.in>"}

# Frontend
curl -I https://app.snowkap.co.in
# Expected: 200 OK + index.html
```

---

## 4. Rollout sequence (per Phase 13 plan)

| Week | Sends/day cap | Gate to advance |
|---|---|---|
| 1 (week of 2026-04-25) | ≤ 50 to confirmed pilots | < 5% verifier-warning rate over the week |
| 2 (week of 2026-05-02) | ≤ 200 | < 5% rate sustained, no `hallucination_audit` fires on production sends |
| 3 (week of 2026-05-09) | ≤ 500 (full autonomous) | Continued < 5% rate; nightly fuzz green |

The drip scheduler reads its caps from `config/settings.json::ingestion.send_cap_per_day`. Bump in production only after each week's gate passes.

---

## 5. Operational dashboards (Phase 11D)

- `/metrics` — Prometheus text format (Snowkap-specific gauges + counters)
  - `snowkap_articles_total{tier=}` — articles in SQLite by tier
  - `snowkap_campaigns_active` — active drip campaigns
  - `snowkap_emails_sent_24h` — outbound count rolling 24h
  - `snowkap_openai_cost_usd_24h` — pipeline LLM spend rolling 24h
  - `snowkap_cron_tick_duration_ms` — campaign_runner tick histogram

Wire these to Grafana / Datadog / your APM of choice. Alert thresholds:
- `snowkap_openai_cost_usd_24h > 50` → cost runaway
- `snowkap_emails_sent_24h > daily_cap × 1.1` → cap breach
- API request error rate > 2% over 5 min → page on-call

---

## 6. Backup + disaster recovery

- **SQLite hourly backup** runs from the cron in §3c. Stored under `data/backups/snowkap.<YYYYMMDDHH>.db`. 14-day retention.
- **Outputs** — `data/outputs/{slug}/...` JSON files are the source of truth for every article. SQLite is a re-buildable index. To recover: restore `data/` from any backup, then `python engine/main.py reindex` rebuilds the SQLite article_index from the JSON files.
- **Ontology TTL files** — committed to git, treat as code. Restore from `git checkout`.
- **Resend** — sends are recorded in the `campaign_send_log` SQLite table for audit; provider-side history is in the Resend dashboard (90-day retention by default).

Recovery time objective: restore from hourly backup → reindex → API restart = under 30 minutes.

---

## 7. Phase-by-phase ship status (current)

| Phase | Status | Test count |
|---|---|---|
| 11 — Production hardening | ✅ Shipped 2026-04-24 | 32 |
| 12 — Analysis hardening + fuzz harness | ✅ Shipped 2026-04-25 | 64 |
| 13 — Demo resilience | ✅ Shipped 2026-04-27 | 91 |
| 14 — Demo-grade analysis quality | ✅ Shipped 2026-04-27 | 112 |
| 15 — Stakeholder polarity | ✅ Shipped 2026-04-27 | 122 |
| **16 — Field readiness** | ✅ Shipped 2026-04-27 | TBD |

---

## 8. Common operational tasks

### Onboard a new prospect company (post-Phase 16.1)

**UI path**: super-admin → `/settings/onboard` → fill form → wait ~4 min → "Open dashboard →".

**CLI path** (back-compat):
```bash
python scripts/onboard_company.py --name "Tata Chemicals" --ticker TATACHEM.NS
```

### Re-index from JSON outputs (after restoring from backup)

```bash
python engine/main.py reindex
```

### Force re-analysis of a single article

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  "https://api.snowkap.co.in/api/news/<article_id>/trigger-analysis?force=true"
```

### Manually advance the drip scheduler (skip cron)

```bash
python -m engine.output.campaign_runner --tick
```

### Check production env guard

```bash
SNOWKAP_ENV=production python -c "from api.main import _check_production_env; _check_production_env()"
# Silent success = all green; RuntimeError = fix the missing var
```
