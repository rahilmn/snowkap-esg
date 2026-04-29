# Snowkap ESG — Analyst Guide

> 5-minute read. Everything you need to use the tool day 1.

The URL is **https://powerofnow.snowkap.co.in** (also reachable from your
team's bookmarks).

---

## What this is

Live ESG intelligence on 7 Indian companies (ICICI Bank, YES Bank,
IDFC First Bank, Waaree Energies, Singularity AMC, Adani Power,
JSW Energy). The engine reads news articles, extracts entities and
themes, scores ESG materiality, runs a 12-stage causal-cascade
analysis, and surfaces a CFO 10-second verdict, a CEO strategic
narrative, and a deep ESG-Analyst breakdown — per article, in real time.

Two extra companies can be onboarded in 5 minutes via the
**Settings → Onboard** page (admin only).

---

## How to log in

1. Visit **https://powerofnow.snowkap.co.in**.
2. Enter your `<your-name>@snowkap.co.in` email and your designation.
3. The system mints a session token (no password). Logout via the avatar
   menu in the top-right.

If your email isn't on the allowlist, ask `ci@snowkap.com` to add it.

---

## The dashboard

Top of the page shows your **active company** (use the company switcher
in the header to flip between the 7 targets) and four FOMO tiles:

| Tile | What it counts |
|---|---|
| **Articles** | Total articles indexed for this company |
| **High Impact** | Articles with materiality CRITICAL or HIGH (or relevance ≥ 5.0) |
| **New Today** | Articles published in the last 24 hours |
| **Active Signals** | HOME-tier CRITICAL/HIGH articles in the last 7 days |

Below the tiles, the **#1 Priority Alert** is the highest-scored HOME
article. Click it (or any of the 3 mini-cards below) to open the full
analysis panel.

The dashboard auto-refreshes every 30 seconds, and **automatically
scans for new articles when you open the page** (cooldown: 10 min, so
opening 5 tabs doesn't trigger 5 fetches). If you want an immediate
refresh, click **"⟳ Scan Now"** on the dashboard.

---

## Reading an analysis

Click any HOME-tier card → the article detail sheet opens.

**Top of the sheet — Hero card** with the article headline, ESG theme
breadcrumb, financial exposure, and priority badge.

**Inline perspective switcher** (3 segments: ESG Analyst / CFO / CEO).
Click any to flip the panel. The data is the same — the framing changes:

- **CFO** — 10-second verdict. ₹ exposure, margin impact (bps),
  P&L line items, ROI on the top recommendation.
- **CEO** — Strategic narrative. Stakeholder map (5 stakeholders with
  positive- or negative-event flavour), three-year trajectory
  (do-nothing vs act-now), board-level Q&A drafts, analogous
  precedent.
- **ESG Analyst** — Deep detail. Framework alignment (BRSR / GRI /
  TCFD / ESRS / etc.), 6-dimension relevance score, full risk matrix
  (10 ESG + 7 TEMPLES categories), causal chain, NLP evidence,
  geographic intelligence, audit trail.

**Below the perspective panel** — the recommendations (ranked per
perspective: CFO sorts by ROI, CEO by strategic impact, ESG Analyst
by compliance urgency).

**Source flags on every ₹ figure**:
- `(from article)` — the figure is in the article body verbatim.
- `(engine estimate)` — the figure is computed by the cascade engine
  from company calibration + ontology elasticities.

Hover over a ₹ figure to see the source flag. If the engine ever
mistakes one for the other, the verifier downgrades it automatically
(Phase 12.7 + Phase 18 audits).

---

## Sharing an analysis

The **"Share via email"** button at the top-right of the article sheet
sends a one-article HTML brief to a recipient. Greeting auto-extracts
the first name from their email.

1. Click **Share via email**.
2. Enter the recipient's email and an optional sender note.
3. Click **Preview** to see the rendered subject line + greeting
   first, or **Send** to fire it directly.
4. The brief uses the dark-card editorial layout with the SNOWKAP
   wordmark — renders correctly in Outlook Desktop, 365, Gmail
   (web/iOS/Android), and Apple Mail.

If the **Share unavailable** badge shows instead of the button, the
email backend isn't configured for this deployment — ping
`ci@snowkap.com`.

---

## Onboarding a new company (admin only)

If you have admin permissions, **Settings → Onboard** lets you add
any NSE/BSE-listed Indian company in ~5 minutes.

1. Paste a **company website** (e.g. `tatachemicals.com`). The system
   resolves the ticker, industry, financials, and 28 ESG news queries
   automatically.
2. Click **Personalize Snowkap**. The pipeline runs through 5 stages:
   Pending → Fetching → Analysing → Ready (or Failed).
3. On **Ready**, click **Open dashboard →** to switch to the new
   company.

Errors usually mean the company name didn't resolve to a yfinance
ticker — try the **Advanced** section to pass a ticker hint manually.

---

## Glossary

| Term | What it means |
|---|---|
| **HOME tier** | High relevance + high materiality — surfaces on the dashboard |
| **SECONDARY tier** | Lower relevance — visible in the feed but not prioritised |
| **REJECTED** | Filtered out (off-topic, wrap-up digest, calendar preview, etc.) |
| **Materiality** | CRITICAL > HIGH > MODERATE > LOW > NON-MATERIAL |
| **TEMPLES** | Volume / Value / Cost / Growth / Brand / Workforce / Outage — 7 enterprise risk categories |
| **Active Signals** | HOME articles flagged CRITICAL or HIGH in the last 7 days |
| **Cascade** | A chain of primitives (e.g. Energy Price → Opex → Margin) with elasticities calibrated per company |

---

## What to do when something looks wrong

1. **Wrong analysis** → Slack `#snowkap-quality` or email
   `ci@snowkap.com` with the article URL + a 1-line "what's off". The
   engineering team monitors this. (A formal "Report Wrong Analysis"
   button is shipping in Phase 20 — Track B in the launch plan.)
2. **Frozen spinner** → wait up to 60 seconds (cold-start analysis).
   If it hangs after that, refresh; the verifier auto-recovers.
3. **5xx error** → also pingable to `ci@snowkap.com`. Sentry will
   already have caught it; your report adds the user-side context.
4. **Stale data** → click "⟳ Scan Now" or wait 60 minutes for the
   continuous scheduler.

---

## What this tool does NOT do (yet)

- **No mobile-optimised view** — works on iPad landscape; phone
  rendering is a known limitation.
- **No saved searches / alerts** — the feed is fixed-order today.
- **No bulk export** — share one article at a time via email.
- **No comments / collaboration on articles** — single-user view.

These are on the roadmap. File requests in `#snowkap-product`.
