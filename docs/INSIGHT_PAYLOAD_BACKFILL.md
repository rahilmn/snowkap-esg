# Runbook — Insight-payload durability + `data/outputs` de-bloat (Phase 51.B)

## Why
The full insight detail payload was persisted **only** to
`data/outputs/{slug}/insights/*.json` (the container filesystem). On Railway
that filesystem is **ephemeral**, so a restart/redeploy wiped every
runtime-generated insight and `GET /api/insights/{id}` returned **HTTP 202
"regenerating"** — a fresh, **billable** LLM run. It's also why ~hundreds of
generated JSON files ended up committed to git (baked into the image to
survive deploys), bloating the repo and every deploy.

## What changed (already landed on the branch)
1. **`engine/models/insight_payload.py`** — a durable `insight_payload` table
   (`CREATE TABLE IF NOT EXISTS`, works on Postgres + SQLite).
2. **`engine/output/writer.py::write_insight`** — now **dual-writes** the
   payload to Postgres on every write (non-fatal; disk stays the immediate
   source of truth).
3. **`api/routes/insights.py::insight_detail`** — disk stays **primary**; when
   the on-disk file is **absent**, it serves the **DB mirror** (HTTP 200)
   instead of 202 + a billable regenerate.
4. **`scripts/backfill_insight_payload.py`** — mirrors existing on-disk
   insights into the table (idempotent, `--dry-run`, `--skip-existing`).
5. **`.gitignore`** — `data/outputs/` is now ignored (stops *new* bloat).

Covered by `tests/test_phase51_insight_payload.py` (round-trip; 200-from-mirror
when disk is gone; 202 preserved when neither disk nor DB has it).

## The sequence — ORDER MATTERS
Do **not** drop `data/outputs` before the mirror is populated, or freshly
restarted containers would 202-regenerate every legacy insight.

1. **Deploy the mirror code** (changes 1–3 above) to prod. From this moment,
   **new** insights are mirrored automatically by the dual-write.
2. **Backfill existing insights into prod Postgres.** With the prod env
   (`SNOWKAP_DB_BACKEND=postgres`, `SUPABASE_DATABASE_URL=...`):
   ```bash
   python scripts/backfill_insight_payload.py --dry-run     # preview counts
   python scripts/backfill_insight_payload.py               # mirror all
   python scripts/backfill_insight_payload.py --skip-existing   # cheap re-run / verify
   ```
   A clean run prints `errors=0`. (Local SQLite check already passed:
   `scanned=47 upserted=47`, re-run `skipped=47`.)
3. **Verify the mirror serves when disk is absent (staging).** Temporarily
   rename `data/outputs` (or deploy a container without it) and confirm
   `GET /api/insights/{id}` returns **200** (look for the log line
   `insight_detail: served <id> from Postgres mirror (disk absent)`), not 202.
4. **Drop `data/outputs` from git + image, redeploy:**
   ```bash
   git rm -r --cached data/outputs
   git commit -m "Phase 51.B: drop generated insight outputs (now mirrored in Postgres)"
   ```
   `.gitignore` already prevents re-tracking. Redeploy → the image is
   ~hundreds of files lighter and `insight_detail` serves from the mirror.

## Pre-drop caveat (verify once)
`insight_detail` reads only the **main** `insights/*.json` (which embeds
`perspectives`, `recommendations`, `analysis`, `evidence_pack`) — that's what
the mirror stores. The **split-out** files
(`data/outputs/{slug}/{risk,frameworks,causal,recommendations,perspectives}/`)
are write-through copies. Before step 4, confirm in staging (with
`data/outputs` absent) that no endpoint 500s — i.e. nothing reads those
split-out files at runtime. If something does, mirror it too before dropping.

## Rollback
- The insights remain in **git history** until the step-4 commit, and in the
  **Postgres mirror** after the backfill — so a bad drop is recoverable by
  reverting the commit or relying on the mirror.
- If the mirror itself is wrong, `insight_detail` still prefers disk while the
  files exist; the dual-write and fallback are both non-fatal and never block.

## Optional follow-on (separate de-bloat)
`data/inputs/` (raw NewsAPI.ai article JSON, ~hundreds tracked) is **not**
covered by this mirror and is still consumed by `run_full_text_retry_job`.
Treat its removal as a separate decision, not part of this runbook.
