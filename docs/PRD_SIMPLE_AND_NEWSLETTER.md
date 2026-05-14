# Snowkap — Simple PRD & Newsletter Content Guide

**Length:** 5-minute read · plain language · for the team deciding what to ship and what to put in the outbound newsletter.

---

## Part 1 — What is this app, in one paragraph

Snowkap is an **ESG news intelligence engine** for listed Indian (and now global) companies. It reads every ESG-relevant news article about a company — SEBI penalties, MSCI rating changes, contract wins, BRSR filings, supply-chain issues — and turns each one into **three different one-page briefs**: one for the CFO, one for the CEO, one for the ESG Analyst. Every ₹ figure is computed (not hallucinated by an AI), every claim has an audit trail, and the brief tells the executive what to *do* next — not just what happened.

---

## Part 2 — The problem (why this exists)

Today, when a CFO, CEO, or ESG Analyst wants to know "what does this news mean for us?", they have three bad choices:

| Today's option | Why it fails |
|---|---|
| Read the 50-page analyst report | Too slow. Often a week late. Not specific to the company. |
| Ask ChatGPT / Gemini to summarise | Fast but **makes up numbers**. "₹500 Cr penalty risk" appears in a contract-win article. CFO quotes it → reputation hit. |
| Skim the headline themselves | No context on whether it moves their P&L, board narrative, or disclosure obligation. No action attached. |

The result: ESG news lives in inboxes and Slack threads, gets read in 8 seconds, and rarely changes a decision.

**Snowkap fixes this by**:
1. Computing every ₹ figure from a deterministic engine (not LLM guesswork)
2. Tagging every claim with provenance (source = article body / source = engine estimate)
3. Re-rendering the same article three ways — because a CFO cares about ₹, a CEO cares about board narrative, an analyst cares about the filing deadline
4. Attaching a 3-5 step action list with payback months / ROI ceilings

---

## Part 3 — What each role gets

### CFO — the 10-second verdict
> "Will this move my margin or my access to capital, and by how much?"

What they see in the brief:
- **Headline leads with ₹** — e.g. *"P&L compresses ~₹1,900 Cr · payback 6 months"*
- **Hero metric**: P&L exposure (point estimate in headline, ±10% range in body)
- **Top 3 cascade hops** — where the ₹ flows: opex → margin → cost-of-capital
- **3-5 recommendations** ranked by ROI descending, each tagged with payback in months
- **Audit trail** behind every figure — for "why ₹0.5-1 Cr?" follow-ups

The CFO must be able to defend every number to the board within 10 seconds.

### CEO — the strategic brief
> "How does this change my 3-year board story, and what's my best peer doing right now?"

What they see:
- **Headline NEVER leads with ₹** — leads with positioning (e.g. *"MSCI ESG upgrade lifts FY27-29 board narrative"*)
- **Hero metric**: Strategic position vs named peers (Tata Power · HDFC · Infosys etc.)
- **3-year trajectory**: do-nothing vs act-now outcome pair, on `FY27-29` horizon
- **Stakeholder map**: 5 stakeholders with their stance + a real precedent (e.g. "BlackRock raised Tata Power's weight post-Khavda")
- **Board paragraph**: 80 words, chairman-ready

CEOs don't read framework codes or section numbers. They read peer signals.

### ESG Analyst — the audit trail
> "Which BRSR principle does this trigger, when is the filing deadline, and what's my evidence chain?"

What they see:
- **Headline leads with framework section** — e.g. *"BRSR:P6:Q14 disclosure trigger — due 2026-09-30"*
- **Hero metric**: Disclosure trigger + filing deadline
- **KPI table**: full-precision figures (no rounding — analysts cross-check)
- **All 21 frameworks scored** + mandatory/optional flag per region
- **Full causal chain**: hop-by-hop derivation of every figure (β · lag · confidence)
- **Audit trail**: regulator-grade provenance

Analysts work to filing dates and section codes. Nothing else matters.

---

## Part 4 — Newsletter Content Priorities

### The fundamental rule: **same news, three different newsletters**

Don't send the same email to all three roles. The CFO version, CEO version, and Analyst version are different subject lines, different headlines, different hero metrics, different action lists.

### Universal newsletter anatomy (applies to all three roles)

A Snowkap newsletter has **6 blocks** in this fixed order:

1. **Subject line** (90-char max, iPhone-preview safe)
2. **Top of inbox** — the hero metric + 1-sentence stakes
3. **Why it matters** — 3-4 bulleted insights
4. **Impacted metrics** — table or grid (financial / operational / regulatory)
5. **What to do** — 3-5 recommendations
6. **CTA** — single button at the bottom (no link spam)

Below: what fills each block per role, and what to cut when space is tight.

---

### Per-role newsletter content priorities

#### CFO newsletter — priorities (top = must show, bottom = cut first if space-limited)

| Block | Must show | Nice to have | Cut first |
|---|---|---|---|
| **Subject** | ₹ figure + verb (e.g. *"₹1,900 Cr P&L compresses — review needed"*) | Company name + sector | Date |
| **Hero metric** | P&L exposure ₹ Cr · % of revenue · payback months | Confidence band | Peer comparison |
| **Why it matters** | (a) what triggered it (b) ₹ + bps (c) deadline (d) framework section | Source-tag verification stamp | Macro context |
| **Impacted metrics** | Top 3 cascade hops with margin bps | Operational + reputational rows | TEMPLES row |
| **Recommendations** | Top 3 ranked by ROI descending; payback months on each | Audit trail link per rec | "Do nothing" option |
| **CTA** | *"Read full analysis"* (first touch) → *"Book 20-min walkthrough"* (subsequent) | — | — |

**Rule of thumb for CFO**: every line either has a ₹ figure, a date, or an action verb. If a sentence has none of those, cut it.

---

#### CEO newsletter — priorities

| Block | Must show | Nice to have | Cut first |
|---|---|---|---|
| **Subject** | Competitive positioning verb (e.g. *"MSCI upgrade — FY27-29 board narrative needs reframe"*) | Peer name | ₹ figure (CEOs ignore subject ₹) |
| **Hero metric** | Strategic position vs named peers (1-2 peers max) | Industry quartile | P&L numbers |
| **Why it matters** | (a) what changed in the competitive landscape (b) which stakeholder signal moved (c) precedent peer case | Polarity-matched analogy (positive → positive precedent) | Framework codes |
| **Impacted metrics** | Stakeholder stance grid (5 stakeholders × stance) | Brand impact | Operational detail |
| **Recommendations** | Top 3 strategic actions (positioning · capital allocation · brand) — NO compliance / filing tasks | Investor-comms angle | Operational specifics |
| **CTA** | *"Read full board memo"* | — | — |

**Rule of thumb for CEO**: every recommendation is something they'd approve at a board meeting. If a CEO has to forward it to someone else to action, cut it.

---

#### ESG Analyst newsletter — priorities

| Block | Must show | Nice to have | Cut first |
|---|---|---|---|
| **Subject** | Framework section + deadline (e.g. *"BRSR:P6:Q14 trigger — due 2026-09-30"*) | `[unverified]` flag when confidence is low | ₹ figure |
| **Hero metric** | Disclosure trigger + filing deadline | β + lag + method confidence phrase | Strategic framing |
| **Why it matters** | (a) which framework section (b) which provision text (c) which regulator (d) confidence bounds | Cross-framework alignment (BRSR:P6 ↔ GRI:303 ↔ ESRS:E3) | Stakeholder positioning |
| **Impacted metrics** | Full KPI table with full-precision ₹ figures, β, lag, confidence levels per row | Causal chain visualisation | Brand quartile |
| **Recommendations** | Disclosure / framework / KPI tracking / audit actions only — NO capex / brand / strategic | Per-rec citation link to the relevant provision | Investor-comms angle |
| **CTA** | *"View full disclosure trail"* (links to audit log) | — | — |

**Rule of thumb for Analyst**: every claim has a section code. If a claim doesn't cite a framework section, mark it `[unverified]` or cut it.

---

### Subject-line priorities (the one thing that decides whether the email gets opened)

In iPhone preview the recipient sees ~50 characters before the cut. Get the punchline in.

| Role | Pattern | Example |
|---|---|---|
| CFO | `₹{figure} {verb} — {urgency hint}` | *"₹1,900 Cr P&L compresses — review needed"* |
| CEO | `{verb} {peer/signal} — {horizon}` | *"MSCI upgrade — FY27-29 board reframe"* |
| Analyst | `{framework}:{section} — {deadline}` | *"BRSR:P6:Q14 due 2026-09-30"* |

Things the subject line MUST NOT contain (every email's automatic disqualifier):
- "(engine estimate)" / "(from article)" — provenance is for the body, not the subject
- Greek letters (β, σ, Δ) — break in some email clients
- Framework section codes IN the CFO/CEO subject lines (Analyst-only)
- ₹ figures IN the CEO subject line
- More than 90 characters total

---

### Frequency + cadence

| Trigger | Send what | To whom |
|---|---|---|
| New CRITICAL article (criticality ≥ 0.75) | Immediate single-article brief | All 3 roles simultaneously |
| New HIGH article (≥ 0.55) for matched persona focus area | Within 4 business hours | The role whose persona matched |
| Weekly digest (Sunday evening) | Top 3 articles by criticality, role-specific | All 3 roles |
| MEDIUM / LOW articles | Never push by email | (Available in the dashboard) |
| Action on existing article (recommendation completed) | Confirmation email to actor | Person who actioned |

**Hard rule**: if criticality < 0.65 the email isn't sent at all. The share endpoint returns HTTP 422 with top-3 alternatives. Quality bar > volume.

---

### What to never include (regardless of role)

- ✗ Unflagged engine estimates (must be tagged `(engine estimate)` in body, never in subject)
- ✗ Hallucinated peer precedents (must come from the precedent ontology, polarity-matched)
- ✗ Action items the role can't action (no "file BRSR" in a CEO email; no "renegotiate green bond" in an Analyst email)
- ✗ "(low_confidence_classification)" articles — those get a yellow review badge in the dashboard but never email
- ✗ Past-deadline disclosures (regulatory deadlines that have already passed)
- ✗ ROI claims above the cap (compliance 500%, financial 300%, strategic 400%, operational 200%) without an `roi_capped` disclaimer
- ✗ Cross-role ₹ drift > 5% (CFO email and CEO email about the SAME article must quote the SAME ₹ figure within ±5%)

---

## Part 5 — Decision summary (what to ship in week 1 of newsletter rollout)

If you only have time to ship one slice of the newsletter content in the first week, ship this:

1. **Send to CFO only** (highest commercial value, highest action rate)
2. **Trigger only on criticality ≥ 0.65** (no daily digest yet — too noisy)
3. **One article per email** (no batch newsletters yet)
4. **Subject pattern**: `₹{figure} {verb} — {urgency}`
5. **Body**: hero metric + 3 cascade hops + top 3 recs with payback months
6. **CTA**: *"Read full analysis"*

In week 2 add CEO. In week 3 add Analyst. In week 4 add weekly digest.

Why this order: CFO emails have the highest "did they reply / book a meeting" rate in pilot. CEO emails get opened but rarely actioned in isolation. Analyst emails are best consumed in the dashboard with the full audit trail, not in inbox.

---

## Part 6 — Open content questions for the team

These are things the engine can produce both ways but the team needs to pick one:

1. **Subject-line provenance**: do we ever show `~` (approx) in the subject? Currently the spec says ₹ figures in subject are point-estimates (no range). Recommend: no ~ in subject; range goes in the body.

2. **Recommendation count cap**: spec allows 3-5 recs per role. For an email, is 3 always enough? Or do we show 5? Recommend: show top 3 in email, link to the dashboard for 4 + 5.

3. **Frequency for the same recipient on the same company**: if a CFO at Adani gets 3 CRITICAL articles in one day, do we send 3 emails or batch into one? Recommend: batch — one email with 3 article cards.

4. **"Do nothing" recommendations**: should we surface those in email? They exist in the engine (LOW-materiality articles can have a do-nothing rec). Recommend: never in email, only dashboard.

5. **Sentiment trajectory (forecaster)**: the new forecaster gives 3/6/12-month sentiment direction. Do we show this in the CEO email as a one-line trend indicator? Recommend: yes, as a sparkline above the hero metric.

Resolve these 5 before week-1 send. The rest can ride.

---

*Sister docs:*
- *Full technical PRD: `docs/PRD.md`*
- *Intelligence calculations: `docs/INTELLIGENCE_AND_CALCULATIONS.md`*
- *References & validation: `docs/REFERENCES_AND_FRAMEWORK_TAGGING.md`*
- *Newsletter renderer code: `engine/output/newsletter_renderer.py`*
- *Subject-line generator: `engine/output/subject_line.py`*
