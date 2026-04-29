# Snowkap End-to-End User Journey Audit (2026-04-27)

> Goal: trace every step a real user takes from "I just clicked Sign In" to
> "I'm reading a rich, accurate ESG/CFO/CEO brief" — and call out every
> place the output can drift from the article reality, the ontology, or the
> 12-stage pipeline. This is the single source of truth for the Phase 17
> accuracy hardening pass that follows.

---

## Glossary

- **Target company** = one of the 7 companies in `config/companies.json`
  (ICICI, YES, IDFC First, Waaree, Singularity AMC, Adani Power, JSW Energy)
- **Onboarded company** = added at runtime via `/settings/onboard`
  (sales-self-serve flow, Phase 11B + 16)
- **97 / 3 split** = Phase 17 ontology coverage: stages 3-9 + 11 are 100%
  ontology-driven; stages 10 + 12 are LLM but with ontology-computed ₹
  hard constraints; stages 1-2 are pure LLM (irreducible).

---

## Stage-by-stage user journey

### Step 0. Boot
- `EmailConfigSync` polls `/api/admin/email-config-status` once on auth.
- Ontology graph eagerly loads (Phase 13 S3) — fails-fast in prod if a
  TTL is malformed.

### Step 1. Login
**File**: `client/src/pages/LoginPage.tsx` → `POST /api/auth/login`
**Backend**: `api/routes/legacy_adapter.py::auth_login`

The backend grants permissions based on email allowlist:
```python
is_super = is_snowkap_super_admin(body.email)
permissions = (
    list(SUPER_ADMIN_PERMISSIONS)   # ← grants manage_drip_campaigns
    if is_super
    else ["read", "chat", "view_analysis", "view_news"]
)
```

**Allowlist source**: `SNOWKAP_INTERNAL_EMAILS` env var (comma-separated).
Without it, **NO ONE** is super-admin and the share-button + onboarding-page
+ campaigns-page UI never render.

### Step 2. Dashboard
- `HomePage.tsx` → `GET /api/news/stats` + `GET /api/news` for the active
  company.
- Active Signals tile (Phase 13 B8) counts HOME-tier `CRITICAL/HIGH`
  articles in the last 7 days.
- Auto-refresh every 30s (Phase 13 S6).

### Step 3. Click an article
**File**: `client/src/components/panels/ArticleDetailSheet.tsx`

On mount, the sheet checks `article.deep_insight?.headline`. If absent or
the schema_version is stale (≠ `2.0-primitives-l2`), it auto-fires
`POST /api/news/{id}/trigger-analysis` and starts polling
`GET /api/news/{id}/analysis-status` every 2-3s with a 5-stage faux-
progress spinner (Phase 13 S5).

### Step 4. Trigger-analysis (background)
**Backend**: `api/routes/legacy_adapter.py::trigger_analysis` →
`engine.analysis.on_demand.enrich_on_demand`

This re-runs the **full pipeline (stages 1-12)** from the **raw input**
article for fresh classification, then writes the enriched JSON back to
disk with the new schema version.

### Step 5. Read the analysis
- ESG Analyst, CFO, CEO perspectives all served from
  `effectiveArticle.perspectives[<lens>]`.
- Inline `PerspectiveSwitcher` (Phase 16 fix shipped 2026-04-27) lets the
  user toggle lenses from inside the article panel.
- Share button at top-right gated on `manage_drip_campaigns` AND
  `emailConfigured`.

### Step 6. Onboard a new company (admin)
**File**: `client/src/pages/SettingsOnboardPage.tsx` →
`POST /api/admin/onboard`
**Backend**: `api/routes/admin_onboard.py::_background_onboard` →
`engine.ingestion.company_onboarder.onboard_company` →
`engine.ingestion.news_fetcher.fetch_for_company` →
`engine.main._run_article` (per article)

`_run_article` runs **the same 12-stage pipeline** that target companies
go through (stages 1-9 always; stages 10-12 only on HOME tier; stages
10-12 also re-run on first user click for SECONDARY).

---

## Issues found (audit)

The numbers below correspond one-for-one to the consolidated fix set in
the next section.

### A. Quarterly-results articles default to negative-event framing on positive earnings
- **File**: `engine/analysis/recommendation_archetypes.py::is_positive_event`
  + `engine/analysis/output_verifier.py::_POSITIVE_EVENTS`
- **Symptom**: IDFC First Bank Q4 calendar article (2026-04-24).
  Sentiment `+1`, profit `+48% YoY`, NPA improving, provisions DOWN 12%.
  But `event_id = event_quarterly_results` is in NEITHER the
  `_POSITIVE_EVENTS` set NOR the `_NEGATIVE_EVENTS` set, so the dispatcher
  defaults to the NEGATIVE-event prompt path. Stage 10 injects
  "190.5 bps margin compression / ₹500 Cr at risk", Stage 12 injects
  defensive remediation recs. The narrative-coherence check at
  `verify_narrative_coherence` only fires when `event_sign == +1` — so
  for `event_quarterly_results` (event_sign == 0) the check is silent.
- **Fix**: Make polarity sentiment-aware for an explicit set of
  AMBIGUOUS events: `event_quarterly_results`, `event_dividend_policy`,
  `event_ma_deal`, `event_esg_rating_change`, `event_climate_disclosure_index`.
  When the event is ambiguous, route based on `nlp.sentiment >= 1`
  (positive) / `<= -1` (negative) / else stay neutral.

### B. On-demand path uses LEGACY perspective transform, not Phase 4 dedicated generators
- **File**: `engine/analysis/on_demand.py:107-108`
  ```python
  for lens in ("esg-analyst", "cfo", "ceo"):
      perspectives[lens] = transform_for_perspective(insight, result, lens)
  ```
- **Symptom**: When the user clicks "View Insights" on a SECONDARY-tier
  article (or any stale-schema HOME), the ESG Analyst + CEO panels render
  the THIN legacy schema (no `stakeholder_map`, no `kpi_table`, no
  `audit_trail`, no `three_year_trajectory`). The same article processed
  via the ingest pipeline (`engine/main.py::_run_article`) gets the rich
  Phase 4 dedicated-generator output. So whether the user sees a CFO-grade
  brief or a thin one is purely a function of which code path ran first.
- **Fix**: On-demand path must mirror the ingest path:
  ```python
  perspectives["esg-analyst"] = generate_esg_analyst_perspective(insight, result, company)
  perspectives["ceo"] = generate_ceo_narrative_perspective(insight, result, company)
  perspectives["cfo"] = transform_for_perspective(insight, result, "cfo")
  ```

### C. Calendar-announcement / preview articles slip into HOME tier
- **File**: `engine/ingestion/news_fetcher.py` (no preview filter) +
  `engine/analysis/relevance_scorer.py` (scores them HOME)
- **Symptom**: The IDFC NDTV article is a **forward-looking calendar
  announcement** ("Q4 results due Apr 25"). It contains zero new news,
  just dates + Q3 numbers as context. Yet the engine scored it `total=6
  → HOME`, then ran the full LLM pipeline + sent it through verifier.
  The output is a "prediction" of what the Q4 release will say — pure
  speculation, but framed with engine-estimate ₹ figures that look
  authoritative.
- **Fix**: Add a `_is_calendar_announcement` filter that rejects articles
  whose title matches any of:
  * `q[1234] results: date, time`
  * `earnings call details`
  * `dividend news.*and more`
  * `to consider and approve.*results`
  AND whose body's strongest financial signal points to a PRIOR quarter
  (e.g., "Q3 FY26 net profit ₹503 Cr" while title is "Q4"). Drop these
  with a `calendar_preview` stat before the relevance scorer ever sees
  them. Cousin of the existing wrap-up detector.

### D. Cross-section ₹ drift verifier only catches NUMERICAL drift, not SEMANTIC drift
- **File**: `engine/analysis/output_verifier.py::verify_cross_section_consistency`
- **Symptom**: When the LLM cites the SAME number in unrelated contexts
  ("₹500 Cr market cap loss" / "₹500 Cr green bond" / "₹500 Cr P/E
  expansion"), the existing checker compares the numerical values and
  finds ZERO drift — because it's the same number. But the **concept**
  is wrong (M-cap loss ≠ green bond ≠ P/E expansion). For now, this is
  hard to fully automate; the structural fix (A) eliminates most of
  these because they only appear when the LLM is in defensive-framing
  mode against article reality.
- **Fix (defer)**: Phase 18 — extract the noun phrase preceding each ₹
  figure and warn when the same value is paired with semantically
  distinct phrases. Or: add a "₹ figure used in N distinct sections"
  duplicate detector. Today's mitigation is the polarity routing in
  fix A.

### E. Onboarded companies miss `primitive_calibration` ⇒ cascade impact ≈ 0
- **File**: `engine/ingestion/company_onboarder.py` (writes basic
  financials) + `config/companies.json` (target companies have full
  `primitive_calibration`)
- **Symptom**: When a sales user onboards "tatachemicals.com", the
  resolver writes `revenue_cr` + `opex_cr` + `industry` + `news_queries`
  to `companies.json`. But the 7 target companies have a richer
  `primitive_calibration` block (`energy_share`, `labor_share`,
  `freight_intensity`, `key_exposure`) that drives the per-company β
  multiplier in `primitive_engine.compute_cascade()`. Without it, the
  cascade falls through to industry-average β only — no company-specific
  exposure flag (e.g. "Adani Power: energy_share=40% → 4× the EP→OX
  cascade"). For a banking onboard this is fine (already handled by
  Industry == Financials). For non-target industries (Steel, Auto,
  Pharma, ...) it's a silent under-call.
- **Fix**: At the end of the onboarder, derive `primitive_calibration`
  from the resolved industry + company size using existing industry
  benchmarks already in the ontology. If unknown industry, write a
  conservative `{energy_share: 0.10, labor_share: 0.20, freight_intensity: 0.05, key_exposure: ["regulatory"]}` default
  and emit a warning so the operator can refine later.

### F. Onboarding modal doesn't surface the schema-version invariant
- **File**: `client/src/pages/SettingsOnboardPage.tsx`
- **Symptom**: Onboarding completes "Ready" → user clicks "Open
  dashboard". The articles all have `schema_version = "2.0-primitives-l2"`
  because they ran through `_run_article` (which writes the current
  version). So the on-demand `enrich_on_demand` will treat them as
  cached and short-circuit. That's CORRECT — but if the schema version
  is bumped later (Phase 18), every onboarded article gets stuck on the
  old schema until the user clicks each one. We need an invalidation
  path.
- **Fix (defer)**: Add a `force_reanalyze` admin endpoint per company
  that bumps schema_version on every JSON file in `data/outputs/<slug>/`.

### G. Hallucination audit (Phase 12.7) doesn't catch reused-number hallucinations
- **File**: `engine/analysis/output_verifier.py::audit_source_tags`
- **Symptom**: For the IDFC article, the body contains exactly one
  ₹503 Cr figure (Q3 net profit). The LLM reused that number in
  unrelated contexts: "₹500 Cr at risk", "₹500 Cr exposure", etc.
  Because 500 ≈ 503 within the regex's ±10% tolerance, the audit
  considers each "₹500 Cr (from article)" tag VALID. The audit doesn't
  reason about CONTEXT, only existence.
- **Fix (defer)**: Extend the audit to count how many DIFFERENT
  semantic claims share the same ₹ value within ±10%. When a single
  article-figure is re-used in 3+ unrelated claims, downgrade all
  tags to `(engine estimate)`. Mitigation today: fix A removes the
  defensive prompt that drives this re-use behaviour.

### H. Articles with `event_id == ""` skip event-archetype routing
- **File**: `engine/analysis/recommendation_engine.py::_build_generator_prompt`
- **Symptom**: When `process_article` can't classify an event (ontology
  keyword scan empty), it returns no event_id. The archetype dispatcher
  then returns `[]`, the generic prompt is used, and the LLM defaults to
  the Phase 13 B1 generic 5-rec template. Output looks like generic ESG
  consultant boilerplate.
- **Fix**: When event_id is empty, fall back to the **theme-driven**
  archetypes — pick archetypes by `themes.primary_theme` instead of
  by event. The ontology already has theme→archetype mappings via the
  framework_section table.

### I. Onboarded companies don't appear in CompanySwitcher until first article is indexed
- **File**: `engine/index/tenant_registry.py::register_tenant`
- **Symptom**: `_background_onboard` calls `register_tenant()` with the
  resolved company name. But the SQL query backing `/api/companies` only
  returns companies that have `>=1` indexed article. If the first
  article fetch fails (no NewsAPI.ai results for an obscure ticker),
  the new company is invisible to the switcher even though "ready" was
  emitted to the onboarding modal.
- **Fix**: `/api/companies` query OR `register_tenant` should write a
  placeholder row that surfaces the company even with zero articles.
  Show a "No articles yet" empty state on the dashboard so the user
  understands the company onboarded but no news was found.

### J. PerspectiveSwitcher in article sheet — fixed today (Phase 16 inline switcher)
- Done. ArticleDetailSheet.tsx now embeds an inline switcher above the
  CrispInsight zone.

### K. Share button hidden on dev because `SNOWKAP_INTERNAL_EMAILS` is unset — fixed today
- Done. Allowlist added to `.env`. User must re-login to mint a fresh
  JWT carrying `manage_drip_campaigns`.

---

## Consolidated fix set (this commit)

| # | Fix | Files | LoC |
|---|---|---|---|
| **1** | Sentiment-aware polarity for ambiguous events (A) | `recommendation_archetypes.py`, `insight_generator.py`, `recommendation_engine.py`, `output_verifier.py`, `ceo_narrative_generator.py` | ~80 |
| **2** | On-demand uses Phase 4 dedicated generators (B) | `on_demand.py` | ~10 |
| **3** | Calendar-announcement filter (C) | `news_fetcher.py` | ~50 |
| **4** | Theme-driven archetypes when event_id empty (H) | `recommendation_archetypes.py`, `recommendation_engine.py` | ~30 |
| **5** | Onboarded company default `primitive_calibration` (E) | `company_onboarder.py` | ~25 |
| **6** | Regression tests | `tests/test_phase17_journey_audit.py` | ~150 |

Out of scope this commit (deferred):

- D — semantic ₹ drift detector (needs noun-phrase extraction; Phase 18)
- F — schema-version bulk reanalyze admin endpoint (low-priority)
- G — reused-number hallucination audit (mostly mitigated by fix #1)
- I — empty-state CompanySwitcher row (UX polish, Phase 18)

---

## Phase 18 follow-up (shipped 2026-04-27)

All four deferred items above are now resolved.

| # | Fix | Files | Status |
|---|---|---|---|
| **7 (D)** | `verify_semantic_consistency()` — Jaccard noun-phrase overlap on figures with the same value but distinct contexts | `engine/analysis/output_verifier.py` | ✅ |
| **8 (G)** | `audit_reused_article_figures()` — when one ₹ value is paired with `>max_distinct_uses` non-overlapping `(from article)` claims, downgrade extras to `(engine estimate)` | `engine/analysis/output_verifier.py` | ✅ |
| **9 (F)** | `POST /api/admin/companies/{slug}/reanalyze` + per-article variant — bumps `meta.schema_version` to `_invalidated` so the next user click triggers fresh on-demand enrichment | `api/routes/admin_reanalyze.py`, `api/main.py`, `client/src/lib/api.ts` | ✅ |
| **10 (I)** | CompanySwitcher renders `(empty)` badge for onboarded tenants with `article_count == 0`; HomePage honours `?company=<slug>` URL param after onboarding completes | `client/src/components/admin/CompanySwitcher.tsx`, `client/src/pages/HomePage.tsx` | ✅ |

Test count: **138 / 138** (Phase 11-18 suite) after the Phase 18 follow-up commit.

---

## Definition of done

After this commit:

- [ ] IDFC Q4 calendar article (Apr 24) is **REJECTED** at ingest as
      `calendar_preview`, never reaching the LLM.
- [ ] If a similar article DOES make it through (e.g. the title format
      is novel), its `event_quarterly_results` event with `sentiment +1`
      routes through the **POSITIVE-event prompt** in stages 10 + 11 + 12
      → no defensive ₹ injections.
- [ ] On-demand-triggered ESG Analyst + CEO panels render the same
      schema (stakeholder_map, audit_trail, kpi_table, three_year_trajectory)
      as ingest-pipeline-generated panels.
- [ ] An onboarded company "tatachemicals.com" shows up in the
      CompanySwitcher within 5 minutes with at least one article in HOME
      tier whose CFO/CEO/ESG-Analyst panels are visibly distinct.
- [ ] All 122+N tests pass (`pytest`).
- [ ] Frontend lints clean (`npm run lint`).
